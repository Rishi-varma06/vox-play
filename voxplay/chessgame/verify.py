"""
The verification gate: checking generated commentary against the facts.

This is the third thing the project claims is new, alongside pacing and
spoken delivery, and the literature supports the need for it directly.
Hebbar et al. (2026) measured a strong contemporary model producing incorrect
chess claims 22.0% of the time without tool support, and smaller open-weight
models exceeding 40%. They measure hallucination after the fact; this module
is the other half -- catching it before anyone hears it.

The design principle it enforces is the one the script layer is built on: the
model narrates facts it is handed and never derives a chess fact of its own.
Every check below asks the same question in a different way -- *is this claim
traceable to something the model was actually given?* A claim that is not is
either invented or inferred, and the listener cannot tell which.

These are checks on OUR OWN output, so unlike the cricket checks they are
not purely advisory. A notation leak or an invented tactic is a defect, and
`findings` separates those from stylistic warnings.

What is deliberately not checked
--------------------------------
Whether the commentary is any good. Fluency, tone and whether a human enjoys
listening are the listener study's job. This module only establishes that
nothing said was made up, which is a precondition for the rest mattering.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Set

from pydantic import BaseModel, Field

from .beats import BeatSheet, BeatType
from .evaluation import describe_eval
from .models import Motif, MoveEvent
from .pgn_source import ParsedGame
from .script import BEST_LINE_PLIES, CommentaryScript, ScriptLine
from .speech import ascii_fold, speak_san

#: Severity. Factual problems are defects; the rest are worth a human's eye.
ERROR = "error"
WARNING = "warning"


class Finding(BaseModel):
    """
    One problem found in generated commentary.

    Shaped like the cricket `QualityFlag` on purpose -- the two domains
    converge here eventually -- but kept local so the chess package does not
    depend on the cricket one.
    """
    check: str
    beat_index: int
    beat_type: BeatType
    severity: str = ERROR
    message: str = ""


class VerificationReport(BaseModel):
    """Everything found in one script, plus the rate that goes in the report."""
    game_id: str
    beats: int = 0
    findings: List[Finding] = Field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def clean(self) -> bool:
        return not self.errors

    @property
    def errors_per_hundred_beats(self) -> float:
        """
        The headline number. Comparable across games of different lengths,
        and the thing to drive toward zero.
        """
        return 100.0 * len(self.errors) / self.beats if self.beats else 0.0

    def summary(self) -> str:
        counts = Counter(f.check for f in self.findings)
        lines = [
            "  %d beats, %d findings (%d errors, %d warnings)"
            % (self.beats, len(self.findings), len(self.errors),
               len(self.findings) - len(self.errors)),
            "  %.1f errors per 100 beats" % self.errors_per_hundred_beats,
        ]
        for check, count in counts.most_common():
            lines.append(f"    {check:<26} {count}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Spoken-text helpers
# ---------------------------------------------------------------------------

#: Algebraic notation in any form. "six" contains an x, so a bare character
#: scan is useless -- this matches piece-and-square, bare squares, and the
#: check/mate/promotion suffixes.
NOTATION = re.compile(
    r"\b[KQRBN][a-h]?[1-8]?x?[a-h][1-8]\b|\b[a-h][1-8]\b|(?<=\w)[+#](?=\s|$)|\b\w+=[QRBN]\b"
)

_RANK_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8",
}

#: "knight to f three" -> f3. The script layer speaks every square this way,
#: so anything matching here is a square the narration named out loud.
_SPOKEN_SQUARE = re.compile(
    r"\b([a-h])\s+(one|two|three|four|five|six|seven|eight)\b", re.IGNORECASE
)

#: Tactic vocabulary. Closed set, because these are exactly the words the
#: motif layer can license -- if the narration uses one, a detector must have
#: found it.
TACTIC_WORDS: Dict[str, Motif] = {
    "fork": Motif.FORK,
    "forks": Motif.FORK,
    "forking": Motif.FORK,
    "pin": Motif.PIN,
    "pins": Motif.PIN,
    "pinned": Motif.PIN,
    "pinning": Motif.PIN,
    "skewer": Motif.SKEWER,
    "skewers": Motif.SKEWER,
    "skewered": Motif.SKEWER,
    "discovered": Motif.DISCOVERED_ATTACK,
    "sacrifice": Motif.SACRIFICE,
    "sacrifices": Motif.SACRIFICE,
    "sacrificed": Motif.SACRIFICE,
    "sacrificing": Motif.SACRIFICE,
    "smothered": Motif.SMOTHERED_MATE,
    "hanging": Motif.HANGING_PIECE,
    "promotes": Motif.PROMOTION,
    "promoting": Motif.PROMOTION,
    "promotion": Motif.PROMOTION,
    "passant": Motif.EN_PASSANT,
}

#: Claims about who stands better. Checked against the position the beat is
#: actually about, because "White is winning" in a lost position is the kind
#: of error a listener catches instantly.
_WHITE_AHEAD = re.compile(r"\bwhite\b[^.]{0,40}\b(winning|clearly better|completely winning)\b", re.I)
_BLACK_AHEAD = re.compile(r"\bblack\b[^.]{0,40}\b(winning|clearly better|completely winning)\b", re.I)

#: Phrases that are shared vocabulary rather than repetition worth flagging.
_FILLER = frozenset({"the position is roughly", "white is slightly better",
                     "black is slightly better", "and white won", "and black won"})


def spoken_squares(text: str) -> Set[str]:
    """Squares the narration named aloud, as algebraic names."""
    return {
        f"{file_.lower()}{_RANK_WORDS[rank.lower()]}"
        for file_, rank in _SPOKEN_SQUARE.findall(text)
    }


def _words(text: str) -> List[str]:
    return re.findall(r"\b[a-z']+\b", text.lower())


def _ngrams(text: str, n: int = 5) -> Set[str]:
    tokens = _words(text)
    if len(tokens) < n:
        return set()
    grams = {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}
    return {g for g in grams if not any(f in g for f in _FILLER)}


# ---------------------------------------------------------------------------
# What each beat is allowed to say
# ---------------------------------------------------------------------------

class BeatLicence(BaseModel):
    """
    Everything a beat is permitted to assert, derived from the same facts the
    model was given. Anything outside this was not supplied and is therefore
    either invented or inferred.
    """
    squares: Set[str] = Field(default_factory=set)
    motifs: Set[Motif] = Field(default_factory=set)
    names: Set[str] = Field(default_factory=set)
    win_pct_white: Optional[float] = None
    result_known: bool = False


def licence_for(
    line: ScriptLine, game: ParsedGame, sheet: BeatSheet, spoiler_free: bool = False
) -> BeatLicence:
    """
    Build the set of claims this beat's facts actually support.

    This must mirror `script.beat_facts` exactly. Anything the facts block
    gives the model but the licence omits becomes a false positive, and
    anything the licence allows that the facts never supplied becomes a blind
    spot. The first version missed the engine's suggested alternative and
    promptly flagged "better was king to d eight" as an invented square -- a
    fact the model had been handed.
    """
    by_ply: Dict[int, MoveEvent] = {m.ply: m for m in game.moves}
    moves = [by_ply[p] for p in line.plies if p in by_ply]

    licence = BeatLicence()
    meta = game.meta
    for name in (meta.white, meta.black, meta.event, meta.opening):
        if not name:
            continue
        folded = ascii_fold(name)
        licence.names.update(
            _spelling(w) for w in re.findall(r"[A-Za-z]{3,}", folded)
        )
        # A username like "Amin_Biriya" is usually spoken and written without
        # its punctuation, so license the joined form too. The first live run
        # flagged "AminBiriya" as invented when it is simply the player.
        joined = re.sub(r"[^A-Za-z]", "", folded)
        if len(joined) > 2:
            licence.names.add(_spelling(joined))
        # And license each whitespace-delimited token the same way, because
        # that is exactly what check_names does to the generated text: it
        # strips punctuation per token, so "Nimzo-Larsen Attack" reaches the
        # check as "NimzoLarsen" and "Attack". Licensing only the whole
        # joined string left "NimzoLarsen" unlicensed, and an opening name
        # the facts had supplied was reported as invented -- 12 of the 15
        # false positives in the gate measurement, all of them opening names.
        for token in folded.split():
            piece = re.sub(r"[^A-Za-z]", "", token)
            if len(piece) > 2:
                licence.names.add(_spelling(piece))

    for move in moves:
        licence.squares.add(move.from_square)
        licence.squares.add(move.to_square)
        if move.analysis:
            for hit in move.analysis.motifs:
                licence.motifs.add(hit.motif)
                licence.squares.update(hit.squares)
            # The facts name the engine's preference, so the narration may too.
            if move.analysis.best_move_san:
                licence.squares.update(
                    spoken_squares(speak_san(move.analysis.best_move_san))
                )
            # And the facts now carry the continuation of that line, so the
            # squares in it are licensed on exactly the same grounds. This
            # pairing is the one in the docstring above: the licence has to
            # move whenever beat_facts does, or a handed fact reads as an
            # invention.
            for san in move.analysis.best_line_san[1:BEST_LINE_PLIES + 1]:
                if san:
                    licence.squares.update(spoken_squares(speak_san(san)))

    if moves and moves[-1].analysis:
        licence.win_pct_white = moves[-1].analysis.win_pct_after
    elif line.beat_type in (BeatType.INTRO, BeatType.OUTRO):
        licence.win_pct_white = None

    # The result is a fact of the game, released by the beats that are given
    # it; spoiler-free narration withholds it until the finish.
    if not spoiler_free or line.beat_type in (BeatType.FINISH, BeatType.OUTRO):
        licence.result_known = True

    return licence


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_notation(line: ScriptLine, index: int, licence: BeatLicence) -> List[Finding]:
    """No algebraic notation may reach the speech backend."""
    found = NOTATION.findall(line.text)
    if not found:
        return []
    return [Finding(
        check="notation_leak", beat_index=index, beat_type=line.beat_type,
        severity=ERROR,
        message=f"chess notation in spoken text: {sorted(set(found))[:5]}",
    )]


def check_squares(line: ScriptLine, index: int, licence: BeatLicence) -> List[Finding]:
    """Every square named aloud must belong to a move or motif in this beat."""
    named = spoken_squares(line.text)
    invented = sorted(named - licence.squares)
    if not invented:
        return []
    return [Finding(
        check="ungrounded_square", beat_index=index, beat_type=line.beat_type,
        severity=ERROR,
        message=f"names squares not in this beat's facts: {invented}",
    )]


def check_tactics(line: ScriptLine, index: int, licence: BeatLicence) -> List[Finding]:
    """A tactic may only be named if a detector actually found it."""
    claimed = {
        TACTIC_WORDS[w] for w in _words(line.text) if w in TACTIC_WORDS
    }
    invented = sorted(m.value for m in claimed - licence.motifs)
    if not invented:
        return []
    return [Finding(
        check="ungrounded_tactic", beat_index=index, beat_type=line.beat_type,
        severity=ERROR,
        message=f"claims tactics no detector found: {invented}",
    )]


def check_evaluation(line: ScriptLine, index: int, licence: BeatLicence) -> List[Finding]:
    """A claim about who stands better must match the evaluation."""
    if licence.win_pct_white is None:
        return []
    white_pct = licence.win_pct_white
    findings = []
    if _WHITE_AHEAD.search(line.text) and white_pct < 50.0:
        findings.append(Finding(
            check="evaluation_contradiction", beat_index=index,
            beat_type=line.beat_type, severity=ERROR,
            message=f"says White is better; position is {describe_eval(white_pct)}",
        ))
    if _BLACK_AHEAD.search(line.text) and white_pct > 50.0:
        findings.append(Finding(
            check="evaluation_contradiction", beat_index=index,
            beat_type=line.beat_type, severity=ERROR,
            message=f"says Black is better; position is {describe_eval(white_pct)}",
        ))
    return findings


#: Words that look like proper nouns but are ordinary narration.
_COMMON = frozenset({
    "white", "black", "the", "a", "an", "and", "but", "now", "then", "this",
    "that", "here", "after", "before", "with", "from", "both", "king", "queen",
    "rook", "bishop", "knight", "pawn", "accuracy", "move", "moves", "game",
    "it", "he", "she", "they", "his", "her", "their", "if", "in", "on", "at",
    "no", "not", "so", "as", "is", "was", "by", "for", "of", "to", "up",
    "percent", "check", "checkmate", "castles", "kingside", "queenside",
    "opening", "endgame", "middlegame", "position", "player", "players",
})


#: British/American pairs that appear in chess vocabulary. A model writing
#: "Sicilian Defence" when the database says "Defense" has not invented an
#: opening, and flagging it would be a false positive rather than a catch.
_SPELLING_VARIANTS = (
    ("defence", "defense"), ("offence", "offense"),
    ("centre", "center"), ("calibre", "caliber"),
    ("manoeuvre", "maneuver"), ("analyse", "analyze"),
)


def _spelling(word: str) -> str:
    """Normalise a word to one spelling so variants compare equal."""
    lowered = word.lower()
    for british, american in _SPELLING_VARIANTS:
        if british in lowered:
            lowered = lowered.replace(british, american)
    return lowered


def check_names(line: ScriptLine, index: int, licence: BeatLicence) -> List[Finding]:
    """
    Capitalised words mid-sentence should be names we supplied.

    Only mid-sentence, because a sentence-initial capital carries no signal.
    """
    candidates = set()
    for sentence in re.split(r"(?<=[.!?])\s+", line.text):
        tokens = sentence.split()
        for token in tokens[1:]:
            # Strip the possessive BEFORE removing punctuation, or "White's"
            # becomes "Whites" and is flagged as an invented name. The first
            # live generation produced exactly that three times, and all
            # three were false positives.
            word = re.sub(r"['’]s\b", "", token)
            word = re.sub(r"[^A-Za-z]", "", word)
            if len(word) > 2 and word[0].isupper() and word.lower() not in _COMMON:
                candidates.add(word)

    invented = sorted(w for w in candidates if _spelling(w) not in licence.names)
    if not invented:
        return []
    return [Finding(
        check="ungrounded_name", beat_index=index, beat_type=line.beat_type,
        severity=ERROR,
        message=f"names not present in the game data: {invented}",
    )]


def check_repetition(script: CommentaryScript, window: int = 3) -> List[Finding]:
    """
    Phrases reused between nearby beats.

    A warning rather than an error: repetition is not a false claim, it is
    what makes a correct script sound mechanical. The cricket engine applies
    the same idea across a rolling ball window.
    """
    findings: List[Finding] = []
    seen: List[Set[str]] = []
    for index, line in enumerate(script.lines):
        grams = _ngrams(line.text)
        for offset, earlier in enumerate(seen[-window:], start=1):
            shared = grams & earlier
            if shared:
                findings.append(Finding(
                    check="phrase_repetition", beat_index=index,
                    beat_type=line.beat_type, severity=WARNING,
                    message=f"reuses phrasing from a nearby beat: "
                            f"{sorted(shared)[:2]}",
                ))
                break
        seen.append(grams)
    return findings


def check_length(line: ScriptLine, index: int, budget_words: int) -> List[Finding]:
    """Badly overrunning the budget breaks the pacing the beat sheet planned."""
    if budget_words <= 0 or line.words <= budget_words * 1.6:
        return []
    return [Finding(
        check="length_overrun", beat_index=index, beat_type=line.beat_type,
        severity=WARNING,
        message=f"{line.words} words against a {budget_words}-word target",
    )]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

PER_BEAT_CHECKS = (check_notation, check_squares, check_tactics,
                   check_evaluation, check_names)


def verify_line(
    line: ScriptLine, index: int, licence: BeatLicence
) -> List[Finding]:
    """
    Run the factual checks over a single beat.

    Exposed separately so generation can gate on it: a beat that invents
    something is worth one regeneration with the problem fed back, which is
    cheaper and more honest than shipping it and reporting the rate later.
    """
    findings: List[Finding] = []
    for check in PER_BEAT_CHECKS:
        findings.extend(check(line, index, licence))
    return findings


def verify_script(
    script: CommentaryScript,
    game: ParsedGame,
    sheet: BeatSheet,
    spoiler_free: bool = False,
) -> VerificationReport:
    """
    Run every check over a generated script.

    Returns findings rather than raising: a caller regenerating a single bad
    beat needs to know which one and why, and a caller reporting a
    hallucination rate needs all of them.
    """
    report = VerificationReport(game_id=script.game_id, beats=len(script.lines))
    budgets = {
        index: int(beat.est_seconds * 1.85)
        for index, beat in enumerate(sheet.beats)
    }

    for index, line in enumerate(script.lines):
        licence = licence_for(line, game, sheet, spoiler_free=spoiler_free)
        for check in PER_BEAT_CHECKS:
            report.findings.extend(check(line, index, licence))
        report.findings.extend(check_length(line, index, budgets.get(index, 0)))

    report.findings.extend(check_repetition(script))
    report.findings.sort(key=lambda f: (f.beat_index, f.check))
    return report
