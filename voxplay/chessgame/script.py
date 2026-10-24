"""
Beat sheet -> spoken commentary script.

The load-bearing rule of this module: the model narrates facts it is handed
and never derives a chess fact of its own. It is given the spoken form of the
move, the classification, the evaluation in words, the motifs, and what the
engine preferred -- and asked only to say them well. It never sees a board,
never sees raw notation, and is never asked whether something was a fork.

That is the whole anti-hallucination design. Everything a listener could
catch as wrong is computed upstream by code that has tests.

Two generators produce the same shape of script:

    TemplateGenerator  deterministic sentences built from the facts
    ClaudeGenerator    two-pass LLM, outline then beats

The template generator is not only an offline fallback. It is the third
baseline for the listener study -- accurate, grounded, and completely flat --
which isolates how much the LLM contributes over correct facts alone.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from .beats import Beat, BeatSheet, BeatType, WORDS_PER_SECOND
from .evaluation import ERROR_QUALITIES, describe_eval
from .models import Color, MoveEvent, MoveQuality
from .pgn_source import ParsedGame
from .speech import (
    ascii_fold,
    normalise_for_speech,
    speak_colour,
    speak_duration,
    speak_move,
    speak_san,
    speak_squares_in,
)


def _article(word: str) -> str:
    """'a' or 'an' -- 'a inaccuracy' is the kind of slip a listener notices."""
    return "an" if word[:1].lower() in "aeiou" else "a"

#: Default model. The cricket engine still points at claude-sonnet-4-6; that
#: is worth updating separately rather than as a side effect of this module.
DEFAULT_MODEL = "claude-opus-5"


class ScriptError(RuntimeError):
    """Raised when a script cannot be generated."""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

class ScriptLine(BaseModel):
    """One spoken line, tied to the beat it came from."""
    beat_type: BeatType
    plies: List[int] = Field(default_factory=list)
    text: str
    words: int = 0
    est_seconds: float = 0.0


class CommentaryScript(BaseModel):
    """
    The finished script.

    `est_seconds` here is measured from the words actually written, unlike the
    beat sheet's figure which is a budget estimate. Comparing the two says
    whether the pacing model's duration assumptions hold.
    """
    game_id: str
    generator: str
    strategy: str
    lines: List[ScriptLine] = Field(default_factory=list)
    total_words: int = 0
    total_seconds: float = 0.0
    budgeted_seconds: float = 0.0

    @property
    def full_text(self) -> str:
        return "\n\n".join(line.text for line in self.lines)


#: Published per-million-token prices, for turning usage into a number the
#: report can quote. Cache reads are a tenth of the input rate, cache writes
#: a quarter more than it.
MODEL_PRICES: Dict[str, Tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass
class TokenUsage:
    """What a generation actually cost, accumulated across calls."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, usage) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cost(self, model: str) -> Optional[float]:
        """Dollars, or None for a model whose price is not recorded here."""
        price = MODEL_PRICES.get(model)
        if price is None:
            return None
        inp, out = price
        return (
            self.input_tokens * inp
            + self.cache_read * inp * 0.1
            + self.cache_write * inp * 1.25
            + self.output_tokens * out
        ) / 1_000_000

    def summary(self, model: str) -> str:
        spend = self.cost(model)
        money = "unknown price" if spend is None else f"${spend:.4f}"
        return (
            f"{self.calls} calls, {self.input_tokens:,} in "
            f"({self.cache_read:,} cached) / {self.output_tokens:,} out, {money}"
        )


@dataclass(frozen=True)
class ScriptStyle:
    """
    Narration controls.

    `audience_elo` decides whether chess vocabulary gets explained. Below the
    threshold "a fork" becomes "attacking two pieces at once"; above it, the
    explanation is patronising and slows the commentary down.
    """
    persona: str = "analytical"          # analytical | energetic
    audience_elo: int = 1200
    spoiler_free: bool = False           # narrate as if the result is unknown

    @property
    def explain_terms(self) -> bool:
        return self.audience_elo < 1600


DEFAULT_STYLE = ScriptStyle()


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------

def _players(game: ParsedGame) -> str:
    meta = game.meta
    white = ascii_fold(meta.white or "White")
    black = ascii_fold(meta.black or "Black")
    if meta.white_elo and meta.black_elo:
        return f"{white} ({meta.white_elo}) vs {black} ({meta.black_elo})"
    return f"{white} vs {black}"


def _opening(game: ParsedGame) -> Optional[str]:
    """Opening name, folded to ASCII -- real PGNs carry accented names."""
    return ascii_fold(game.meta.opening) if game.meta.opening else None


#: How many half-moves of the engine's line to pass on. Far enough to show
#: the idea, short enough to stay a sentence: a commentator says "after king
#: to d eight the attack runs out of pieces", not twelve plies of analysis
#: nobody can follow without a board.
BEST_LINE_PLIES = 4


def _spoken_line(san_moves: Sequence[str]) -> str:
    """The engine's continuation in speakable form, or '' if there is none."""
    # The first entry is the engine's preferred move, which the facts have
    # already named on its own line; repeating it would have the narrator
    # say it twice.
    rest = [san for san in san_moves[1:BEST_LINE_PLIES + 1] if san]
    return ", then ".join(speak_san(san) for san in rest)


def _move_facts(move: MoveEvent, style: ScriptStyle) -> List[str]:
    """Everything true about one move, in words the narrator can reuse."""
    facts = [
        f"move: {move.move_number}, {speak_colour(move.color)}",
        f"spoken form: {speak_move(move)}",
    ]
    analysis = move.analysis
    if analysis is None:
        return facts

    facts.append(f"verdict: {analysis.quality.value}")
    facts.append(f"position after: {describe_eval(analysis.win_pct_after)}")

    if analysis.quality in (MoveQuality.BLUNDER, MoveQuality.MISTAKE, MoveQuality.INACCURACY):
        facts.append(
            f"cost: {analysis.win_loss:.0f} points of winning chances; "
            f"before this move, {describe_eval(analysis.win_pct_before)}"
        )
    if analysis.is_only_move:
        facts.append("this was the only move that held the position")
    if analysis.best_move_san and not analysis.is_best:
        facts.append(f"engine preferred: {speak_san(analysis.best_move_san)}")
        # Naming the better move without its continuation gives the narrator
        # half a thought: "king to d eight was better" invites "better how?"
        # and the model has nothing to answer with. The engine already
        # searched the line, so hand over what it found -- this is the
        # difference between stating a verdict and explaining one.
        follow_up = _spoken_line(analysis.best_line_san)
        if follow_up:
            facts.append(f"what that line gives: {follow_up}")
    if analysis.material_swing:
        facts.append(f"material won on this move: {analysis.material_swing} pawns")
    for hit in analysis.motifs:
        facts.append(f"tactic: {speak_squares_in(hit.description)}")
    if move.think_time_s is not None:
        facts.append(f"time spent on this move: {speak_duration(move.think_time_s)}")
    if style.explain_terms and analysis.motifs:
        facts.append("audience is a beginner -- explain any chess term you use")
    return facts


def beat_facts(beat: Beat, game: ParsedGame, sheet: BeatSheet, style: ScriptStyle) -> str:
    """
    The complete, and only, factual basis for one spoken line.

    If something is not in here, the narrator must not say it.
    """
    by_ply: Dict[int, MoveEvent] = {m.ply: m for m in game.moves}
    moves = [by_ply[p] for p in beat.plies if p in by_ply]
    meta = game.meta
    arc = sheet.arc
    facts: List[str] = []

    if beat.beat_type == BeatType.INTRO:
        facts.append(f"players: {_players(game)}")
        if meta.event:
            facts.append(f"event: {meta.event}")
        if meta.opening:
            facts.append(f"opening: {_opening(game)}")
        facts.append(f"length: {arc.total_plies} half-moves")
        facts.append(f"shape of the game: {arc.archetype.value.replace('_', ' ')}")
        if not style.spoiler_free and arc.winner:
            facts.append(f"result: {speak_colour(arc.winner)} won")
        elif not style.spoiler_free:
            facts.append("result: drawn")

    elif beat.beat_type == BeatType.OPENING:
        if meta.opening:
            facts.append(f"opening played: {_opening(game)}")
        if meta.eco:
            facts.append(f"ECO code: {meta.eco}")
        book = game.opening_report
        if book and book.name:
            facts.append(f"named line: {ascii_fold(book.name)}")
            if book.exit_ply:
                facts.append(
                    f"they left known theory on move {book.exit_move_number} "
                    f"with {speak_san(book.exit_san or '')}"
                )
            else:
                facts.append("the whole opening stayed inside known theory")
        facts.append("moves: " + "; ".join(speak_move(m, with_number=True) for m in moves))
        if moves and moves[-1].analysis:
            facts.append(f"after these moves: {describe_eval(moves[-1].analysis.win_pct_after)}")

    elif beat.beat_type == BeatType.BRIDGE:
        if moves:
            facts.append(
                f"covering moves {moves[0].move_number} to {moves[-1].move_number} "
                "in a single sentence -- summarise, do not list them"
            )
            captures = [m for m in moves if m.is_capture]
            if captures:
                facts.append(f"pieces traded: {len(captures)}")
            slips = [m for m in moves if m.analysis
                     and m.analysis.quality in ERROR_QUALITIES]
            if slips:
                facts.append(f"errors in this stretch: {len(slips)}")
            else:
                facts.append("no errors in this stretch")
            if moves[-1].analysis:
                facts.append(f"position at the end of this stretch: "
                             f"{describe_eval(moves[-1].analysis.win_pct_after)}")

    elif beat.beat_type == BeatType.FLAT:
        # Full-match coverage: one line per half-move, so this beat carries
        # the same per-move facts a moment would. Without them the narrator
        # has nothing concrete to say and fills the line with generalities
        # -- which the verifier passes, because vagueness asserts nothing.
        for move in moves:
            facts.extend(_move_facts(move, style))
        if moves:
            move = moves[0]
            if move.ply <= 2:
                facts.append("this is the start of the game")
            else:
                facts.append(
                    f"this is half-move {move.ply} of {arc.total_plies}"
                )
            if move.is_capture and move.captured_piece:
                facts.append(f"this move captures a {move.captured_piece}")
            if move.is_checkmate:
                facts.append("this move is checkmate")
            elif move.is_check:
                facts.append("this move gives check")
            if move.is_castle:
                facts.append("this move castles the king to safety")
        facts.append(
            "narrate this move specifically -- name what was played and what "
            "it does. Do not drift into general advice about chess."
        )

    elif beat.beat_type in (BeatType.MOMENT, BeatType.TURNING_POINT):
        for move in moves:
            facts.extend(_move_facts(move, style))
        if beat.beat_type == BeatType.TURNING_POINT:
            facts.append("this is the moment the game turned -- give it room")

    elif beat.beat_type == BeatType.FINISH:
        facts.append("moves: " + "; ".join(speak_move(m, with_number=True) for m in moves))
        if arc.winner:
            facts.append(f"result: {speak_colour(arc.winner)} won")
        else:
            facts.append("result: drawn")
        for move in moves:
            for hit in (move.analysis.motifs if move.analysis else []):
                facts.append(f"tactic: {speak_squares_in(hit.description)}")

    elif beat.beat_type == BeatType.OUTRO:
        for colour, name in ((Color.WHITE, meta.white), (Color.BLACK, meta.black)):
            played = [m for m in game.moves if m.color == colour and m.analysis]
            if played:
                average = sum(m.analysis.accuracy for m in played) / len(played)
                facts.append(f"{name or speak_colour(colour)} accuracy: {average:.0f} percent")
        facts.append(f"shape of the game: {arc.archetype.value.replace('_', ' ')}")

    facts.append(f"target length: about {int(beat.est_seconds * WORDS_PER_SECOND)} words")
    return "\n".join(f"- {fact}" for fact in facts)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a chess commentator narrating a completed game for audio.
Your voice is warm, clear and knowledgeable -- a person talking, not a report being read.

ABSOLUTE RULES:
1. Every chess fact you state must appear in the FACTS block you are given.
   Never invent a move, an evaluation, a tactic, a player name, or a result.
   If a fact is not given to you, leave it out. An omitted detail is always
   better than a wrong one.
2. You are writing for TEXT-TO-SPEECH. Never write chess notation. Never write
   "Nf3", "Qxh7#", "1.e4", "+2.3" or "#". The FACTS block gives you the spoken
   form of every move -- use it, or rephrase it in your own words.
3. Never state a numeric evaluation. The FACTS block describes the position in
   words; use those words.
4. Do not describe what happens later in the game than the moment you are
   narrating, unless the facts for this beat say the result explicitly.
5. Vary your phrasing. Do not open consecutive lines with the same word, and do
   not reuse a run of four or more words from the previous lines shown to you.
6. Never refer to yourself, to being an AI, to engines, evaluations, or to this
   being generated.

Write only the words to be spoken. No headings, no stage directions, no notation."""


#: How each shape of game wants to be told. The archetype has always been
#: computed and handed over as a single word, which told the narrator what
#: the game was without telling it anything about how to say it -- so a
#: collapse and a grind came out in the same measured register. These are
#: instructions about shape and emphasis only. None of them licenses a
#: claim; the facts block remains the whole of what may be asserted.
ARCHETYPE_NOTE: Dict[str, str] = {
    "miniature": (
        "This game was over quickly. Keep the whole narration brisk and do "
        "not pad the quiet moves -- the speed is the story."
    ),
    "collapse": (
        "One side was fine and then was not. Hold the earlier beats steady "
        "so the break lands hard, and do not foreshadow it."
    ),
    "comeback": (
        "The player who won was losing first. Let the early beats sit with "
        "that, so the recovery reads as a recovery."
    ),
    "slugfest": (
        "The advantage changed hands repeatedly. Keep the listener oriented "
        "-- say who stands better each time it moves."
    ),
    "blunderfest": (
        "Both sides erred badly and often. Be plain about it without "
        "sneering; these are people, and one of them still found a way to win."
    ),
    "smooth_conversion": (
        "The winner never slipped. Attend to accumulation rather than drama, "
        "and resist inventing tension the game did not have."
    ),
    "grind": (
        "This was decided slowly, deep into the game. Give the long middle "
        "its due and keep the register patient."
    ),
    "draw": (
        "Nobody won. Do not narrate toward a result that never arrives -- "
        "the balance itself is what there is to explain."
    ),
}


def _arc_note(sheet: BeatSheet) -> str:
    """Narration guidance for the shape of this particular game."""
    return ARCHETYPE_NOTE.get(sheet.arc.archetype.value, "")


def _style_note(style: ScriptStyle) -> str:
    voice = {
        "analytical": "Measured and precise. Explain why things matter.",
        "energetic": "Lively and quick. Lean into the drama, but never invent it.",
    }.get(style.persona, "Measured and precise.")
    audience = (
        "Your listener is a beginner: explain any chess term the moment you use it."
        if style.explain_terms
        else "Your listener is a club player: do not explain basic chess terms."
    )
    return f"{voice}\n{audience}"


def outline_prompt(game: ParsedGame, sheet: BeatSheet, style: ScriptStyle) -> str:
    arc = sheet.arc
    running_order = "\n".join(
        f"  {i + 1}. {beat.beat_type.value} -- {beat.reason}"
        for i, beat in enumerate(sheet.beats)
    )
    return f"""Plan the narration for one chess game before writing it.

FACTS
- players: {_players(game)}
- opening: {game.meta.opening or 'not named'}
- length: {arc.total_plies} half-moves
- shape of the game: {arc.archetype.value.replace('_', ' ')}
- result: {speak_colour(arc.winner) + ' won' if arc.winner else 'drawn'}

RUNNING ORDER
{running_order}

{_style_note(style)}
{_arc_note(sheet)}

In no more than 80 words, state the through-line of this game: what it is
about, and what a listener should come away understanding. This is a note to
yourself, not something anyone hears. Do not write any commentary yet."""


def beat_prompt(
    beat: Beat,
    game: ParsedGame,
    sheet: BeatSheet,
    style: ScriptStyle,
    outline: str,
    previous: List[str],
) -> str:
    recent = "\n".join(f"  - {line}" for line in previous[-3:]) or "  (this is the first line)"
    return f"""THROUGH-LINE OF THE GAME
{outline}

LINES YOU HAVE ALREADY SPOKEN (do not repeat their phrasing)
{recent}

THIS BEAT: {beat.beat_type.value}

FACTS
{beat_facts(beat, game, sheet, style)}

{_style_note(style)}
{_arc_note(sheet)}

Write the spoken words for this beat only."""


#: How many consecutive beats one batched call covers. Six is about a run of
#: three full moves: long enough that the model can shape a phrase across
#: them, short enough that the facts for all six still fit comfortably and a
#: single rejected line only costs a small rewrite.
BATCH_SIZE = 6


def batch_prompt(
    batch: Sequence[Beat],
    game: ParsedGame,
    sheet: BeatSheet,
    style: ScriptStyle,
    outline: str,
    previous: List[str],
) -> str:
    """
    Prompt for a run of consecutive beats answered in one call.

    Narrating a whole game one beat per request is both slow and oddly
    written: each line is composed without sight of the next, so the result
    reads as a list of observations rather than someone talking. Giving the
    model the whole run lets it carry a thought across moves -- and a game
    covered move by move is mostly runs of quiet moves, which is exactly
    where that matters.
    """
    recent = "\n".join(f"  - {line}" for line in previous[-3:]) or "  (this is the opening of the commentary)"
    blocks = []
    for offset, beat in enumerate(batch, start=1):
        blocks.append(
            f"--- LINE {offset} ({beat.beat_type.value}) ---\n"
            + beat_facts(beat, game, sheet, style)
        )
    facts = "\n\n".join(blocks)
    return f"""THROUGH-LINE OF THE GAME
{outline}

LINES YOU HAVE ALREADY SPOKEN (do not repeat their phrasing)
{recent}

You are writing {len(batch)} consecutive lines of spoken commentary. Each one
covers the moves in its own block and nothing else. Let the run read as
continuous speech -- vary the sentence shapes, and do not begin every line
the same way -- but keep each line able to stand on its own, because the
listener can jump straight to any of them.

{facts}

{_style_note(style)}
{_arc_note(sheet)}

Return exactly {len(batch)} lines, each prefixed with its number and a pipe,
like:
1| <the spoken words for line 1>
2| <the spoken words for line 2>

No other text, no blank lines between them."""


def split_batch_reply(text: str, expected: int) -> Optional[List[str]]:
    """
    Pull the numbered lines back out of a batched reply.

    Returns None rather than guessing when the shape is wrong, so the caller
    can fall back to asking for the beats one at a time. A half-parsed batch
    would silently attach one move's commentary to a different move, which
    the verifier cannot catch -- every claim would be true, just about the
    wrong position.
    """
    found: Dict[int, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head, sep, rest = line.partition("|")
        if not sep:
            continue
        head = head.strip().rstrip(".)")
        if not head.isdigit():
            continue
        index = int(head)
        if 1 <= index <= expected and index not in found:
            spoken = rest.strip()
            if spoken:
                found[index] = spoken
    if len(found) != expected:
        return None
    return [found[i] for i in range(1, expected + 1)]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

class ScriptGenerator(ABC):
    """Turns a beat sheet into spoken lines."""

    name: str = "abstract"

    @abstractmethod
    def generate(
        self,
        game: ParsedGame,
        sheet: BeatSheet,
        style: ScriptStyle = DEFAULT_STYLE,
    ) -> CommentaryScript:
        ...


class TemplateGenerator(ScriptGenerator):
    """
    Deterministic sentences assembled from the facts.

    Runs offline, and every claim it makes is true by construction. Flat and
    repetitive by design -- that is the point. As the third arm of the
    listener study it separates "the facts were right" from "the writing was
    good", which is the only way to tell how much the LLM is actually adding.
    """

    name = "template"

    def generate(self, game, sheet, style=DEFAULT_STYLE) -> CommentaryScript:
        by_ply = {m.ply: m for m in game.moves}
        lines = [
            _script_line(beat, self._line(beat, game, sheet, style, by_ply))
            for beat in sheet.beats
        ]
        return _assemble(game, sheet, lines, generator=self.name)

    def _line(self, beat, game, sheet, style, by_ply) -> str:
        moves = [by_ply[p] for p in beat.plies if p in by_ply]
        arc = sheet.arc
        meta = game.meta
        outcome = f"{speak_colour(arc.winner)} won" if arc.winner else "the game was drawn"

        if beat.beat_type == BeatType.INTRO:
            name = _opening(game)
            opening = f", {_article(name)} {name}" if name else ""
            return (f"{_players(game)}{opening}. "
                    f"{arc.total_plies} half-moves, and {outcome}.")

        if beat.beat_type == BeatType.OPENING:
            spoken = "; ".join(speak_move(m) for m in moves[:6])
            name = _opening(game)
            named = f"They played the {name}. " if name else ""
            book = game.opening_report
            left = ""
            if book and book.name and book.exit_ply:
                left = (f" They left known theory on move "
                        f"{book.exit_move_number}.")
            return f"{named}The game opened: {spoken}.{left}"

        if beat.beat_type == BeatType.FLAT and moves:
            # One short line per move, as move-by-move commentary actually is.
            move = moves[0]
            spoken = f"{speak_colour(move.color)} plays {speak_move(move)}."
            analysis = move.analysis
            if analysis and analysis.quality in (MoveQuality.BLUNDER,
                                                 MoveQuality.MISTAKE,
                                                 MoveQuality.INACCURACY):
                spoken += f" {_article(analysis.quality.value).capitalize()} "                           f"{analysis.quality.value}."
            return spoken

        if beat.beat_type == BeatType.BRIDGE and moves:
            # Never claim a stretch was uneventful without checking. A bridge
            # over 40 moves of a real game said "pass without incident" across
            # six errors and a dozen tactics.
            span = f"Moves {moves[0].move_number} through {moves[-1].move_number}"
            slips = sum(
                1 for m in moves
                if m.analysis and m.analysis.quality in ERROR_QUALITIES
            )
            captures = sum(1 for m in moves if m.is_capture)
            if slips:
                return (f"{span} bring {slips} more "
                        f"{'slips' if slips != 1 else 'slip'} from both sides.")
            if captures:
                return (f"{span} see {captures} "
                        f"{'pieces' if captures != 1 else 'piece'} come off the board.")
            return f"{span} pass quietly."

        if beat.beat_type in (BeatType.MOMENT, BeatType.TURNING_POINT) and moves:
            move = moves[0]
            analysis = move.analysis
            parts = [f"{speak_colour(move.color)} plays {speak_move(move)}."]
            if analysis:
                if analysis.motifs:
                    described = speak_squares_in(analysis.motifs[0].description)
                    parts.append(described[:1].upper() + described[1:] + ".")
                if analysis.quality in (MoveQuality.BLUNDER, MoveQuality.MISTAKE,
                                        MoveQuality.INACCURACY):
                    label = analysis.quality.value
                    parts.append(f"That is {_article(label)} {label}.")
                    if analysis.best_move_san:
                        parts.append(f"Better was {speak_san(analysis.best_move_san)}.")
                parts.append(f"Now {describe_eval(analysis.win_pct_after)}.")
            return " ".join(parts)

        if beat.beat_type == BeatType.FINISH and moves:
            spoken = "; ".join(speak_move(m) for m in moves)
            return f"The finish: {spoken}. {outcome.capitalize()}."

        if beat.beat_type == BeatType.OUTRO:
            accuracies = []
            for colour, name in ((Color.WHITE, meta.white), (Color.BLACK, meta.black)):
                played = [m for m in game.moves if m.color == colour and m.analysis]
                if played:
                    average = sum(m.analysis.accuracy for m in played) / len(played)
                    accuracies.append(f"{name or speak_colour(colour)} {average:.0f} percent")
            tail = f" Accuracy: {', '.join(accuracies)}." if accuracies else ""
            return f"{outcome.capitalize()}.{tail}"

        return ""


class ClaudeGenerator(ScriptGenerator):
    """
    Two-pass generation: an editorial outline, then each beat in order.

    The outline exists so the whole script argues one thing. Generating beats
    independently produces seven competent sentences that do not add up to a
    story, and generating the whole script in one call drifts and repeats.

    Each beat is given the previous lines so it can avoid their phrasing --
    the same rolling-window idea the cricket engine uses.
    """

    name = DEFAULT_MODEL

    #: How many times a beat that fails verification is regenerated before
    #: being accepted anyway. One retry catches most of it; more than two
    #: usually means the facts do not support the beat at all, which is a
    #: problem for the beat sheet rather than the writing.
    max_repairs: int = 1

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        style_cache: bool = True,
        max_repairs: int = 1,
    ) -> None:
        self.model = model
        self.name = model
        self._cache = style_cache
        self.max_repairs = max_repairs
        # Objective 5 asks for cost per game measured rather than estimated,
        # so every call's usage is accumulated rather than inferred from
        # prompt lengths.
        self.usage = TokenUsage()

    def _client(self):
        try:
            import anthropic
        except ImportError as exc:
            raise ScriptError("anthropic SDK not installed. Run: pip install anthropic") from exc
        return anthropic.Anthropic()

    def _ask(self, client, prompt: str, max_tokens: int, effort: str) -> str:
        import anthropic

        system = [{"type": "text", "text": SYSTEM_PROMPT}]
        if self._cache:
            # Stable across every beat in a game, so it is worth caching.
            system[0]["cache_control"] = {"type": "ephemeral"}
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIStatusError as exc:
            # exc.message is the whole JSON envelope; pull the human sentence
            # out of it so the CLI can print something a person can act on.
            try:
                detail = exc.body["error"]["message"]
            except Exception:  # noqa: BLE001 - best-effort extraction
                detail = exc.message
            raise ScriptError(f"Claude API error {exc.status_code}: {detail}") from exc
        except anthropic.APIConnectionError as exc:
            raise ScriptError(f"could not reach the Claude API: {exc}") from exc

        self.usage.add(response.usage)
        if response.stop_reason == "refusal":
            raise ScriptError("the model declined to generate this beat")
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    def _ask_batch(
        self, client, batch, game, sheet, style, outline, spoken
    ) -> Optional[List[str]]:
        """One call for a run of beats, or None if the reply was malformed."""
        prompt = batch_prompt(batch, game, sheet, style, outline, spoken)
        text = self._ask(
            client, prompt, max_tokens=400 * len(batch) + 300, effort="low"
        )
        return split_batch_reply(text, len(batch))

    def _settle(
        self, client, beat, index, draft, game, sheet, style, outline, spoken
    ) -> ScriptLine:
        """
        Verify one drafted line, rewriting it alone if it does not hold up.

        A batched line is checked exactly as a singly-generated one is. When
        it fails, only that line is asked for again -- the rest of the batch
        was fine, and re-rolling all six to fix one would undo the saving.
        """
        from .verify import licence_for, verify_line

        candidate = _script_line(beat, draft)
        licence = licence_for(candidate, game, sheet, spoiler_free=style.spoiler_free)
        problems = verify_line(candidate, index, licence)
        if not problems:
            return candidate

        prompt = beat_prompt(beat, game, sheet, style, outline, spoken)
        for attempt in range(self.max_repairs + 1):
            prompt = (
                f"{prompt}\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED\n"
                + "\n".join(f"- {p.message}" for p in problems)
                + "\nRewrite it using only the facts above."
            )
            candidate = _script_line(beat, self._ask(
                client, prompt, max_tokens=1000, effort="low"
            ))
            licence = licence_for(
                candidate, game, sheet, spoiler_free=style.spoiler_free
            )
            problems = verify_line(candidate, index, licence)
            if not problems:
                break
        return candidate

    def generate(self, game, sheet, style=DEFAULT_STYLE) -> CommentaryScript:
        # Imported here because verify.py reads this module's types.
        from .verify import licence_for, verify_line

        client = self._client()
        outline = self._ask(
            client, outline_prompt(game, sheet, style), max_tokens=2000, effort="medium"
        )

        spoken: List[str] = []
        lines: List[ScriptLine] = []

        # A paced sheet is a handful of beats and each one earns its own
        # call. A full-match sheet is every half-move, where one call per
        # beat is both slow and badly written -- so those go out in runs.
        batched = len(sheet.beats) > BATCH_SIZE * 2
        pending = list(enumerate(sheet.beats))

        while batched and pending:
            batch = pending[:BATCH_SIZE]
            drafts = self._ask_batch(
                client, [b for _, b in batch], game, sheet, style, outline, spoken
            )
            if drafts is None:
                break  # malformed reply: fall through to one call per beat
            for (index, beat), draft in zip(batch, drafts):
                line = self._settle(
                    client, beat, index, draft, game, sheet, style, outline, spoken
                )
                spoken.append(line.text)
                lines.append(line)
            pending = pending[BATCH_SIZE:]

        for index, beat in pending:
            prompt = beat_prompt(beat, game, sheet, style, outline, spoken)
            line: Optional[ScriptLine] = None

            for attempt in range(self.max_repairs + 1):
                text = self._ask(client, prompt, max_tokens=1000, effort="low")
                candidate = _script_line(beat, text)
                licence = licence_for(
                    candidate, game, sheet, spoiler_free=style.spoiler_free
                )
                problems = verify_line(candidate, index, licence)
                if not problems or attempt == self.max_repairs:
                    line = candidate
                    break
                # Hand the failure back rather than silently keeping it. The
                # model invents when it is reaching past its facts, and saying
                # which claim was unsupported is usually enough to stop it.
                prompt = (
                    f"{prompt}\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED\n"
                    + "\n".join(f"- {p.message}" for p in problems)
                    + "\nRewrite it using only the facts above."
                )

            spoken.append(line.text)
            lines.append(line)
        return _assemble(game, sheet, lines, generator=self.name)


def get_generator(name: str = "auto", model: str = DEFAULT_MODEL) -> ScriptGenerator:
    """
    Factory mirroring get_engine() and get_provider().

    'auto' falls back to the template generator when no API key is configured,
    so the demo path always produces a script.
    """
    key = (name or "").lower()
    if key == "template":
        return TemplateGenerator()
    if key == "claude":
        return ClaudeGenerator(model)
    if key == "auto":
        has_credentials = bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        return ClaudeGenerator(model) if has_credentials else TemplateGenerator()
    raise ScriptError(f"unknown generator '{name}' (expected template, claude or auto)")


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _script_line(beat: Beat, text: str) -> ScriptLine:
    # Every generator's output passes through here, so this is the one place
    # typographic punctuation can be normalised before it reaches the player,
    # the speech backend or a CP1252 terminal.
    text = normalise_for_speech(text)
    words = len(text.split())
    return ScriptLine(
        beat_type=beat.beat_type,
        plies=list(beat.plies),
        text=text,
        words=words,
        est_seconds=round(words / WORDS_PER_SECOND, 1),
    )


def _assemble(
    game: ParsedGame, sheet: BeatSheet, lines: List[ScriptLine], generator: str
) -> CommentaryScript:
    kept = [line for line in lines if line.text]
    return CommentaryScript(
        game_id=game.meta.game_id,
        generator=generator,
        strategy=sheet.strategy,
        lines=kept,
        total_words=sum(line.words for line in kept),
        total_seconds=round(sum(line.est_seconds for line in kept), 1),
        budgeted_seconds=sheet.total_seconds,
    )


def generate_script(
    game: ParsedGame,
    sheet: BeatSheet,
    generator: str = "auto",
    style: ScriptStyle = DEFAULT_STYLE,
    model: str = DEFAULT_MODEL,
) -> CommentaryScript:
    """Convenience wrapper: pick a generator by name and run it."""
    return get_generator(generator, model).generate(game, sheet, style)
