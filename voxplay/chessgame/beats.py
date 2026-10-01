"""
Beat selection -- deciding what gets airtime.

This is the layer the project actually rests on. Engine analysis is solved,
LLM prose is easy, speech is a paid API. The open question is which of a
hundred half-moves deserve to be spoken about and for how long, and that is
what separates commentary from a machine reading a move list.

The problem is posed as constrained selection: maximise total salience
subject to a spoken-duration budget, mandatory coverage (the game has to have
a beginning, a decisive moment and an end), and spacing rules that stop three
detailed beats landing back to back.

Two baselines live here on purpose, next to the real selector:

    select_every_move()  flat, one beat per half-move -- the naive system
    select_top_n()       top N by evaluation swing, no constraints
    select_beats()       salience + budget + coverage + spacing

An A/B test needs all three to exist and produce the same shape of output, so
they do. If the constrained selector does not beat the baselines with
listeners, that is a real result and this module is where it gets measured.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from .evaluation import DEFAULT_THRESHOLDS, ERROR_QUALITIES
from .models import Color, GamePhase, GameResult, Motif, MoveEvent
from .pgn_source import ParsedGame

# ---------------------------------------------------------------------------
# Speaking rate
# ---------------------------------------------------------------------------

#: Written words per second of finished audio. MEASURED, not assumed: a
#: 134-word script rendered by the offline voice at 155 wpm took 72.6s, which
#: is 1.85 written words per second.
#:
#: The gap between 155 wpm and an effective 111 wpm is real and worth
#: understanding, because the obvious model gets it wrong by 40 percent:
#: sentence and clause boundaries insert pauses, numerals expand when spoken
#: ("98 percent" is three spoken words, not two), and every clip carries about
#: a second of leading and trailing padding. Budgeting at a raw speaking rate
#: therefore promises a two-minute recap and delivers nearly three.
#:
#: This is backend- and style-dependent, so CommentaryAudio.drift_ratio exists
#: to check it: if it strays from 1.0, recalibrate here rather than living
#: with budgets that lie.
WORDS_PER_SECOND = 1.85


class BeatType(str, Enum):
    INTRO = "intro"
    OPENING = "opening"
    BRIDGE = "bridge"            # fast-forward several moves in one line
    FLAT = "flat"                # one short line per move, baseline only
    MOMENT = "moment"
    TURNING_POINT = "turning_point"
    FINISH = "finish"
    OUTRO = "outro"


#: Target word count per beat type. The turning point gets the most room
#: because it is the one moment a listener needs explained rather than
#: narrated.
BEAT_WORDS: Dict[BeatType, int] = {
    BeatType.INTRO: 35,
    BeatType.OPENING: 40,
    BeatType.BRIDGE: 22,
    # The flat baseline gets one SHORT line per move, which is what
    # move-by-move commentary actually is -- the systems in the literature
    # emit a sentence per move, not a paragraph. Giving it a full moment's
    # 45 words would make the baseline 13 minutes on a 17-move game and the
    # comparison a strawman. At 14 words it is a fair opponent and still
    # three to four times longer than a paced recap.
    BeatType.FLAT: 14,
    BeatType.MOMENT: 45,
    BeatType.TURNING_POINT: 65,
    BeatType.FINISH: 50,
    BeatType.OUTRO: 28,
}


def beat_seconds(beat_type: BeatType) -> float:
    return BEAT_WORDS[beat_type] / WORDS_PER_SECOND


# ---------------------------------------------------------------------------
# Narrative arc
# ---------------------------------------------------------------------------

class Archetype(str, Enum):
    """
    The shape of the game, which decides the shape of the script.

    Conditioning the narration on this is what stops every game sounding the
    same. A collapse and a grind want different openings, different pacing and
    a different closing line, even when their move-quality statistics match.
    """
    MINIATURE = "miniature"
    COLLAPSE = "collapse"
    COMEBACK = "comeback"
    SLUGFEST = "slugfest"
    BLUNDERFEST = "blunderfest"
    SMOOTH_CONVERSION = "smooth_conversion"
    GRIND = "grind"
    DRAW = "draw"


class GameArc(BaseModel):
    """Where the game turned, and how it is shaped."""
    archetype: Archetype
    winner: Optional[Color] = None
    turning_point_ply: Optional[int] = None
    point_of_no_return_ply: Optional[int] = None
    decided_at_ply: Optional[int] = None
    lead_changes: int = 0
    total_plies: int = 0


class Beat(BaseModel):
    """
    One unit of narration.

    `plies` is a reference into the game rather than a copy of it, so there is
    exactly one source of truth for what happened. `reason` records why the
    beat earned its airtime -- useful when tuning weights, and useful as
    context for the script layer.
    """
    beat_type: BeatType
    plies: List[int] = Field(default_factory=list)
    salience: float = 0.0
    est_seconds: float = 0.0
    reason: str = ""


class BeatSheet(BaseModel):
    """The running order handed to the script layer."""
    game_id: str
    arc: GameArc
    beats: List[Beat] = Field(default_factory=list)
    total_seconds: float = 0.0
    strategy: str = "salience"

    @property
    def covered_plies(self) -> int:
        return len({ply for beat in self.beats for ply in beat.plies})

    def coverage(self, total_plies: int) -> float:
        """Fraction of the game a listener hears something about."""
        return self.covered_plies / total_plies if total_plies else 0.0


# ---------------------------------------------------------------------------
# Salience
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SalienceWeights:
    """
    Relative pull of each signal. The primary knob for the pacing experiment.

    Swing dominates by design -- what changed is more interesting than what
    was pretty -- but a sound sacrifice that holds the evaluation steady would
    score nothing on swing alone, which is why motifs carry real weight too.
    """
    swing: float = 1.0
    criticality: float = 0.35
    motif: float = 0.45
    material: float = 0.25
    think_time: float = 0.20
    narrative: float = 0.60


DEFAULT_WEIGHTS = SalienceWeights()

#: How much each motif contributes, before weighting. Mate patterns and
#: sacrifices are what people remember; a pin is worth a mention, not a stop.
MOTIF_WEIGHT: Dict[Motif, float] = {
    Motif.BACK_RANK_MATE: 1.00,
    Motif.SMOTHERED_MATE: 1.00,
    Motif.SACRIFICE: 0.90,
    Motif.DOUBLE_CHECK: 0.80,
    Motif.FORK: 0.70,
    Motif.DISCOVERED_ATTACK: 0.70,
    Motif.SKEWER: 0.60,
    Motif.PROMOTION: 0.60,
    Motif.PIN: 0.50,
    Motif.HANGING_PIECE: 0.50,
    Motif.EN_PASSANT: 0.40,
    Motif.ADVANCED_PAWN: 0.30,
}

#: Win-percentage loss that counts as a full-strength swing. Tied to the
#: blunder threshold so a blunder saturates the term -- it moved from 30 to 15
#: when that threshold was measured against Lichess's annotations rather than
#: taken from documentation. Leaving it at 30 would have made every real
#: blunder score half the salience it deserves.
SWING_SATURATION = DEFAULT_THRESHOLDS.blunder

#: Only-move gap that counts as full-strength criticality.
CRITICALITY_SATURATION = 30.0


def _think_time_pressure(move: MoveEvent, baseline: Dict[Color, float]) -> float:
    """
    How unusual this player's think time was, 0-1.

    Measured against that player's own average rather than an absolute, since
    a 30-second think means something very different in bullet and classical.
    Silent when the PGN carries no clocks, which is most of them.
    """
    if move.think_time_s is None:
        return 0.0
    average = baseline.get(move.color, 0.0)
    if average <= 0:
        return 0.0
    ratio = move.think_time_s / average
    # Twice your own average is a long think; three times is a full stop.
    return max(0.0, min(1.0, (ratio - 1.0) / 2.0))


def salience(
    move: MoveEvent,
    arc: GameArc,
    think_baseline: Dict[Color, float],
    weights: SalienceWeights = DEFAULT_WEIGHTS,
) -> float:
    """
    How much this half-move deserves airtime, on an open-ended positive scale.

    Unanalysed moves score zero rather than raising -- a PGN can always be
    paced badly, but it should still be paceable.
    """
    analysis = move.analysis
    if analysis is None:
        return 0.0

    swing = min(1.0, analysis.win_loss / SWING_SATURATION)

    criticality = 0.0
    if analysis.is_only_move and analysis.top_gap is not None:
        criticality = min(1.0, analysis.top_gap / CRITICALITY_SATURATION)

    motif = max(
        (MOTIF_WEIGHT.get(hit.motif, 0.3) for hit in analysis.motifs),
        default=0.0,
    )

    material = min(1.0, abs(analysis.material_swing) / 9.0)
    think = _think_time_pressure(move, think_baseline)

    narrative = 0.0
    if move.ply == arc.turning_point_ply:
        narrative = 1.0
    elif move.ply == arc.point_of_no_return_ply:
        narrative = 0.7

    return (
        weights.swing * swing
        + weights.criticality * criticality
        + weights.motif * motif
        + weights.material * material
        + weights.think_time * think
        + weights.narrative * narrative
    )


def _think_baselines(moves: Sequence[MoveEvent]) -> Dict[Color, float]:
    """Mean think time per side, ignoring moves with no clock."""
    baseline: Dict[Color, float] = {}
    for color in (Color.WHITE, Color.BLACK):
        times = [
            m.think_time_s for m in moves
            if m.color == color and m.think_time_s is not None
        ]
        if times:
            baseline[color] = statistics.fmean(times)
    return baseline


# ---------------------------------------------------------------------------
# Arc extraction
# ---------------------------------------------------------------------------

#: Win probability below which a side is considered to have been losing. Used
#: for comeback detection.
COMEBACK_FLOOR = 25.0

#: Win probability above which the game is treated as decided.
DECIDED_CEILING = 85.0

#: A single move losing this much win probability is a collapse, not a drift.
COLLAPSE_SWING = 40.0

#: Below this many half-moves a game is a miniature regardless of shape.
MINIATURE_PLIES = 40

#: Combined errors that make a game a blunderfest.
BLUNDERFEST_ERRORS = 6

#: Half-moves beyond which a quiet decisive game reads as a grind.
GRIND_PLIES = 80


def _winner(result: GameResult) -> Optional[Color]:
    if result == GameResult.WHITE_WINS:
        return Color.WHITE
    if result == GameResult.BLACK_WINS:
        return Color.BLACK
    return None


def extract_arc(game: ParsedGame) -> GameArc:
    """
    Find where the game turned and what shape it had.

    Runs off the win-probability trace, so it says nothing about whose moves
    were good -- only about when the outcome stopped being in doubt.
    """
    moves = game.moves
    winner = _winner(game.meta.result)
    arc = GameArc(
        archetype=Archetype.DRAW,
        winner=winner,
        total_plies=len(moves),
    )
    analysed = [m for m in moves if m.analysis is not None]
    if not analysed:
        return arc

    trace = [(m.ply, m.analysis.win_pct_after) for m in analysed]

    # Lead changes: crossings of the 50% line. Dead-even positions are skipped
    # rather than assigned to a side, so passing through equality on the way up
    # is not counted as the lead changing hands.
    leader = [pct > 50.0 for _, pct in trace if pct != 50.0]
    arc.lead_changes = sum(1 for a, b in zip(leader, leader[1:]) if a != b)

    # Biggest single error is the turning point, provided somebody erred.
    errors = [m for m in analysed if m.analysis.quality in ERROR_QUALITIES]
    if errors:
        worst = max(errors, key=lambda m: m.analysis.win_loss)
        arc.turning_point_ply = worst.ply

    if winner is not None:
        loser = Color.BLACK if winner == Color.WHITE else Color.WHITE

        def winner_pct(pct: float) -> float:
            return pct if winner == Color.WHITE else 100.0 - pct

        # Last moment the loser was still genuinely in the game.
        for ply, pct in trace:
            if winner_pct(pct) < 50.0:
                arc.point_of_no_return_ply = ply

        # First point after which the winner never dropped back.
        for index, (ply, pct) in enumerate(trace):
            if all(winner_pct(later) >= DECIDED_CEILING for _, later in trace[index:]):
                arc.decided_at_ply = ply
                break

        was_losing = any(winner_pct(pct) < COMEBACK_FLOOR for _, pct in trace)
        loser_errors = [m for m in errors if m.color == loser]
        total_errors = len(errors)

        if was_losing:
            sudden = any(m.analysis.win_loss >= COLLAPSE_SWING for m in loser_errors)
            arc.archetype = Archetype.COLLAPSE if sudden else Archetype.COMEBACK
        elif len(moves) < MINIATURE_PLIES:
            arc.archetype = Archetype.MINIATURE
        elif total_errors >= BLUNDERFEST_ERRORS:
            arc.archetype = Archetype.BLUNDERFEST
        elif arc.lead_changes >= 3:
            arc.archetype = Archetype.SLUGFEST
        elif not [m for m in errors if m.color == winner]:
            arc.archetype = Archetype.SMOOTH_CONVERSION
        elif len(moves) >= GRIND_PLIES and analysed[-1].state.phase == GamePhase.ENDGAME:
            arc.archetype = Archetype.GRIND
        else:
            arc.archetype = Archetype.SLUGFEST
    else:
        arc.archetype = Archetype.DRAW

    return arc


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PacingConfig:
    """
    The constraints that turn a ranked list into a running order.

    Without spacing, the top-scoring moves in a tactical flurry are all
    adjacent and the script narrates four consecutive half-moves in detail
    and then skips twenty. That is exactly the failure the naive baseline
    exhibits, and it is why this is a constrained problem rather than a sort.
    """
    budget_seconds: float = 120.0
    min_ply_gap: int = 4          # at least two full moves between moments
    max_silent_plies: int = 12    # longer than this needs a bridging line
    min_salience: float = 0.25    # below this, nothing worth saying


DEFAULT_PACING = PacingConfig()

#: Spoken seconds to allow per half-move when sizing a budget automatically.
SECONDS_PER_PLY = 2.0

#: Floor. The mandatory intro/opening/finish/outro alone cost about 83 seconds
#: of real speech, so a budget much under this leaves nothing between the
#: opening and the mate. Raised when WORDS_PER_SECOND was calibrated against
#: measured audio -- the old figures were the same length in honest seconds.
MIN_BUDGET_SECONDS = 150.0
MAX_BUDGET_SECONDS = 360.0


def budget_for(game: ParsedGame) -> float:
    """
    A sensible spoken duration for a game of this length.

    A fixed budget does not survive contact with real games: 120 seconds is
    generous for a 17-move miniature and leaves a 68-move blitz game with one
    beat every twenty moves, at which point the bridges start claiming that
    nothing happened across stretches where plenty did. Scaling with length
    and clamping at both ends keeps coverage roughly constant.
    """
    return max(
        MIN_BUDGET_SECONDS,
        min(MAX_BUDGET_SECONDS, game.total_plies * SECONDS_PER_PLY),
    )


def _mandatory_beats(game: ParsedGame, arc: GameArc) -> List[Beat]:
    """Intro, opening and finish: a game needs a beginning and an end."""
    moves = game.moves
    beats = [
        Beat(
            beat_type=BeatType.INTRO,
            plies=[],
            est_seconds=beat_seconds(BeatType.INTRO),
            reason="every game needs an opening line",
        ),
        Beat(
            beat_type=BeatType.OPENING,
            plies=[m.ply for m in moves[:8]],
            est_seconds=beat_seconds(BeatType.OPENING),
            reason=f"opening: {game.meta.opening or game.meta.eco or 'unnamed'}",
        ),
    ]
    if moves:
        tail = [m.ply for m in moves[-3:]]
        beats.append(Beat(
            beat_type=BeatType.FINISH,
            plies=tail,
            est_seconds=beat_seconds(BeatType.FINISH),
            reason="how the game ended",
        ))
    beats.append(Beat(
        beat_type=BeatType.OUTRO,
        plies=[],
        est_seconds=beat_seconds(BeatType.OUTRO),
        reason="sign-off",
    ))
    return beats


def _too_close(ply: int, chosen: Sequence[int], gap: int) -> bool:
    return any(abs(ply - other) < gap for other in chosen)


def select_beats(
    game: ParsedGame,
    weights: SalienceWeights = DEFAULT_WEIGHTS,
    pacing: PacingConfig = DEFAULT_PACING,
) -> BeatSheet:
    """
    Choose what to talk about, under a duration budget.

    Greedy by salience with spacing and coverage constraints. Greedy is not
    optimal, but the budget is small and the score is dominated by a handful
    of moves, so an exact solve buys very little; if that stops being true,
    this is the function to replace with a DP.
    """
    moves = game.moves
    arc = extract_arc(game)
    mandatory = _mandatory_beats(game, arc)
    reserved = sum(beat.est_seconds for beat in mandatory)
    remaining = pacing.budget_seconds - reserved

    think_baseline = _think_baselines(moves)
    scored = sorted(
        (
            (salience(move, arc, think_baseline, weights), move)
            for move in moves
            if move.analysis is not None
        ),
        key=lambda pair: (-pair[0], pair[1].ply),
    )

    # Plies already spoken for by the finish, so the ending is not narrated twice.
    finish_plies = {ply for beat in mandatory if beat.beat_type == BeatType.FINISH for ply in beat.plies}
    opening_plies = {ply for beat in mandatory if beat.beat_type == BeatType.OPENING for ply in beat.plies}
    spoken = set(finish_plies) | set(opening_plies)

    chosen: List[Beat] = []
    chosen_plies: List[int] = []

    for score, move in scored:
        if score < pacing.min_salience:
            break
        if move.ply in spoken:
            continue
        if _too_close(move.ply, chosen_plies, pacing.min_ply_gap):
            continue

        is_turning_point = move.ply == arc.turning_point_ply
        beat_type = BeatType.TURNING_POINT if is_turning_point else BeatType.MOMENT
        cost = beat_seconds(beat_type)
        if cost > remaining:
            continue

        remaining -= cost
        chosen_plies.append(move.ply)
        chosen.append(Beat(
            beat_type=beat_type,
            plies=[move.ply],
            salience=round(score, 3),
            est_seconds=cost,
            reason=_describe_selection(move, is_turning_point),
        ))

    beats = _assemble(game, mandatory, chosen, pacing, remaining)
    return BeatSheet(
        game_id=game.meta.game_id,
        arc=arc,
        beats=beats,
        total_seconds=round(sum(b.est_seconds for b in beats), 1),
        strategy="salience",
    )


def _describe_selection(move: MoveEvent, is_turning_point: bool) -> str:
    """Human-readable justification, for tuning and for prompt context."""
    if is_turning_point:
        return "the move the game turned on"
    analysis = move.analysis
    if analysis.motifs:
        return analysis.motifs[0].description
    if analysis.quality in ERROR_QUALITIES:
        return f"{analysis.quality.value}, {analysis.win_loss:.0f} points of win probability"
    if analysis.is_only_move:
        return "the only move that held the position"
    return f"{analysis.quality.value} move"


def _assemble(
    game: ParsedGame,
    mandatory: Sequence[Beat],
    chosen: Sequence[Beat],
    pacing: PacingConfig,
    remaining: float,
) -> List[Beat]:
    """
    Put the running order together and bridge the long silences.

    A gap longer than max_silent_plies gets a fast-forward line if there is
    budget for one. Without them the script jumps from move 9 to move 31 with
    no acknowledgement that anything happened in between.
    """
    intro = [b for b in mandatory if b.beat_type == BeatType.INTRO]
    opening = [b for b in mandatory if b.beat_type == BeatType.OPENING]
    finish = [b for b in mandatory if b.beat_type == BeatType.FINISH]
    outro = [b for b in mandatory if b.beat_type == BeatType.OUTRO]

    middle = sorted(chosen, key=lambda b: b.plies[0])
    total_plies = game.total_plies

    bridged: List[Beat] = []
    previous = max((ply for b in opening for ply in b.plies), default=0)
    for beat in middle:
        gap_start, gap_end = previous + 1, beat.plies[0] - 1
        if gap_end - gap_start + 1 > pacing.max_silent_plies:
            cost = beat_seconds(BeatType.BRIDGE)
            if cost <= remaining:
                remaining -= cost
                bridged.append(Beat(
                    beat_type=BeatType.BRIDGE,
                    plies=list(range(gap_start, gap_end + 1)),
                    est_seconds=cost,
                    reason=f"covers moves {gap_start} to {gap_end} in one line",
                ))
        bridged.append(beat)
        previous = beat.plies[-1]

    # And the run-up to the finish.
    finish_start = min((ply for b in finish for ply in b.plies), default=total_plies)
    if finish_start - previous - 1 > pacing.max_silent_plies:
        cost = beat_seconds(BeatType.BRIDGE)
        if cost <= remaining:
            bridged.append(Beat(
                beat_type=BeatType.BRIDGE,
                plies=list(range(previous + 1, finish_start)),
                est_seconds=cost,
                reason="covers the run-up to the finish",
            ))

    return [*intro, *opening, *bridged, *finish, *outro]


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def select_every_move(game: ParsedGame) -> BeatSheet:
    """
    Baseline A: one beat per half-move, no selection at all.

    The naive system, and the thing the pacing hypothesis claims to beat.
    Note the duration it produces -- a 40-move game runs past ten minutes,
    which is most of the argument on its own.
    """
    arc = extract_arc(game)
    beats = [
        Beat(
            beat_type=BeatType.FLAT,
            plies=[move.ply],
            salience=0.0,
            est_seconds=beat_seconds(BeatType.FLAT),
            reason="flat baseline covers every move",
        )
        for move in game.moves
    ]
    return BeatSheet(
        game_id=game.meta.game_id,
        arc=arc,
        beats=beats,
        total_seconds=round(sum(b.est_seconds for b in beats), 1),
        strategy="every_move",
    )


def select_top_n(game: ParsedGame, n: int = 8) -> BeatSheet:
    """
    Baseline B: the N largest evaluation swings, ranked and ordered.

    No spacing, no coverage, no narrative role -- so in a tactical flurry it
    happily picks four adjacent half-moves and says nothing about the rest of
    the game. Isolates the value of the constraints specifically, as distinct
    from the value of scoring moves at all.
    """
    arc = extract_arc(game)
    analysed = [m for m in game.moves if m.analysis is not None]
    ranked = sorted(analysed, key=lambda m: (-m.analysis.win_loss, m.ply))[:n]
    beats = [
        Beat(
            beat_type=BeatType.MOMENT,
            plies=[move.ply],
            salience=round(move.analysis.win_loss, 2),
            est_seconds=beat_seconds(BeatType.MOMENT),
            reason=f"{move.analysis.win_loss:.0f} points of win probability",
        )
        for move in sorted(ranked, key=lambda m: m.ply)
    ]
    return BeatSheet(
        game_id=game.meta.game_id,
        arc=arc,
        beats=beats,
        total_seconds=round(sum(b.est_seconds for b in beats), 1),
        strategy=f"top_{n}",
    )
