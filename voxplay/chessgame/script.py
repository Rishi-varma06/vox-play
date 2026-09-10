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
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .beats import Beat, BeatSheet, BeatType, WORDS_PER_SECOND
from .evaluation import ERROR_QUALITIES, describe_eval
from .models import Color, MoveEvent, MoveQuality
from .pgn_source import ParsedGame
from .speech import (
    ascii_fold,
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

Write the spoken words for this beat only."""


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
            return f"{named}The game opened: {spoken}."

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

    def __init__(self, model: str = DEFAULT_MODEL, style_cache: bool = True) -> None:
        self.model = model
        self.name = model
        self._cache = style_cache

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

        if response.stop_reason == "refusal":
            raise ScriptError("the model declined to generate this beat")
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    def generate(self, game, sheet, style=DEFAULT_STYLE) -> CommentaryScript:
        client = self._client()
        outline = self._ask(
            client, outline_prompt(game, sheet, style), max_tokens=2000, effort="medium"
        )

        spoken: List[str] = []
        lines: List[ScriptLine] = []
        for beat in sheet.beats:
            text = self._ask(
                client,
                beat_prompt(beat, game, sheet, style, outline, spoken),
                max_tokens=1000,
                effort="low",
            )
            spoken.append(text)
            lines.append(_script_line(beat, text))
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
