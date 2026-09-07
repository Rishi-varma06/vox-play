"""
Tests for beat selection -- the pacing layer.

Runs on MockAnalysisEngine so the suite stays fast and needs no Stockfish.
The mock's evaluations are not chess, so these tests assert structural
properties (budget respected, spacing held, mandatory beats present) rather
than "move 18 gets picked". The claims that depend on real evaluations belong
in the listener study, not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxplay.chessgame import beats as B
from voxplay.chessgame.engine import MockAnalysisEngine, analyse_game
from voxplay.chessgame.evaluation import classify_move, win_loss_for_move
from voxplay.chessgame.models import Color, GameResult, Motif, MotifHit
from voxplay.chessgame.pgn_source import load_pgn

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"


@pytest.fixture(scope="module")
def game():
    return analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())


@pytest.fixture(scope="module")
def raw():
    """The same game with no engine verdicts attached."""
    return load_pgn(SAMPLE_PGN)


def traced(win_pcts, result=GameResult.WHITE_WINS):
    """
    A game whose win-probability trace is exactly `win_pcts`.

    Arc classification reads the trace and nothing else, so testing it against
    a mock engine's material-driven noise tests nothing. Constructing the
    trace directly lets each archetype be checked against the exact shape it
    is supposed to name. win_loss and quality are derived with the real
    functions so the game stays self-consistent.
    """
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    game.meta.result = result
    previous = 50.0
    for move, pct in zip(game.moves, win_pcts):
        loss = win_loss_for_move(previous, pct, move.color)
        move.analysis.win_pct_before = previous
        move.analysis.win_pct_after = pct
        move.analysis.win_loss = loss
        move.analysis.quality = classify_move(loss)
        move.state.win_pct_white = pct
        previous = pct
    # Trim to the traced length so untraced moves cannot skew the arc.
    keep = len(list(win_pcts))
    game.events = [game.events[0]] + [
        e for e in game.events[1:-1][:keep]
    ] + [game.events[-1]]
    return game


# ---------------------------------------------------------------------------
# Duration model
# ---------------------------------------------------------------------------

def test_a_turning_point_gets_more_airtime_than_a_passing_moment():
    assert B.beat_seconds(B.BeatType.TURNING_POINT) > B.beat_seconds(B.BeatType.MOMENT)


def test_a_bridge_is_the_cheapest_spoken_beat():
    spoken = [t for t in B.BeatType if t is not B.BeatType.INTRO]
    assert B.beat_seconds(B.BeatType.BRIDGE) == min(B.beat_seconds(t) for t in spoken)


# ---------------------------------------------------------------------------
# Salience
# ---------------------------------------------------------------------------

def test_an_unanalysed_move_scores_nothing(raw):
    """A PGN can be paced badly, but it must still be paceable."""
    arc = B.extract_arc(raw)
    assert B.salience(raw.moves[0], arc, {}) == 0.0


def test_throwing_the_game_away_outscores_a_quiet_move(game):
    arc = B.extract_arc(game)
    quiet = min(game.moves, key=lambda m: m.analysis.win_loss)
    blunder = max(game.moves, key=lambda m: m.analysis.win_loss)
    assert B.salience(blunder, arc, {}) > B.salience(quiet, arc, {})


def test_the_turning_point_is_lifted_by_its_narrative_role():
    steady = [50.0] * 10
    collapse = [20.0] * 10          # one black move throws 30 points away
    game = traced(steady + collapse)
    arc = B.extract_arc(game)
    assert arc.turning_point_ply is not None
    move = next(m for m in game.moves if m.ply == arc.turning_point_ply)
    without = B.GameArc(archetype=arc.archetype, total_plies=arc.total_plies)
    assert B.salience(move, arc, {}) > B.salience(move, without, {})


def test_a_tactic_scores_even_when_the_evaluation_holds_steady(game):
    """A sound sacrifice moves no needle; motif weight is why it still counts."""
    arc = B.extract_arc(game)
    move = game.moves[0]
    move.analysis.motifs = []
    plain = B.salience(move, arc, {})
    move.analysis.motifs = [
        MotifHit(motif=Motif.SACRIFICE, squares=["d1"], description="x")
    ]
    assert B.salience(move, arc, {}) > plain
    move.analysis.motifs = []


def test_a_long_think_is_measured_against_that_players_own_average(game):
    arc = B.extract_arc(game)
    move = game.moves[0]
    move.think_time_s = 60.0
    fast = B.salience(move, arc, {Color.WHITE: 30.0})
    slow = B.salience(move, arc, {Color.WHITE: 2.0})
    assert slow > fast
    move.think_time_s = None


def test_think_time_is_silent_without_clocks(game):
    arc = B.extract_arc(game)
    assert all(m.think_time_s is None for m in game.moves)
    assert B.salience(game.moves[0], arc, {}) >= 0.0


# ---------------------------------------------------------------------------
# Arc
# ---------------------------------------------------------------------------

def test_the_winner_is_read_from_the_result(game):
    assert B.extract_arc(game).winner == Color.WHITE


def test_a_short_decisive_game_is_a_miniature():
    """White pulls away steadily and it is over inside 40 half-moves."""
    assert B.extract_arc(traced([50.0 + i * 1.5 for i in range(33)])).archetype == (
        B.Archetype.MINIATURE
    )


def test_being_nearly_lost_and_winning_gradually_is_a_comeback():
    sinking = [50.0 - i * 4.0 for i in range(10)]     # down to 14
    recovery = [14.0 + i * 8.0 for i in range(11)]    # back up to 94
    assert B.extract_arc(traced(sinking + recovery)).archetype == B.Archetype.COMEBACK


def test_one_catastrophic_move_makes_it_a_collapse():
    """Same comeback shape, but handed over in a single move rather than drifting."""
    sinking = [50.0 - i * 4.0 for i in range(10)]     # down to 14
    handover = [14.0, 80.0] + [90.0] * 9              # one 66-point swing
    assert B.extract_arc(traced(sinking + handover)).archetype == B.Archetype.COLLAPSE


def test_a_drawn_game_is_classified_as_a_draw():
    game = traced([50.0] * 20, result=GameResult.DRAW)
    assert B.extract_arc(game).archetype == B.Archetype.DRAW


def test_lead_changes_are_counted():
    swinging = [60.0, 40.0, 60.0, 40.0, 60.0]
    assert B.extract_arc(traced(swinging)).lead_changes == 4


def test_a_one_sided_game_has_no_lead_changes():
    assert B.extract_arc(traced([50.0 + i * 1.5 for i in range(33)])).lead_changes == 0


def test_the_arc_knows_how_long_the_game_was(game):
    assert B.extract_arc(game).total_plies == game.total_plies


def test_an_empty_game_does_not_crash_the_arc():
    from voxplay.chessgame.pgn_source import parse_pgn_text
    arc = B.extract_arc(parse_pgn_text('[Result "*"]\n\n*'))
    assert arc.total_plies == 0


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_the_running_order_stays_inside_its_budget(game):
    pacing = B.PacingConfig(budget_seconds=120.0)
    assert B.select_beats(game, pacing=pacing).total_seconds <= 120.0


def test_a_tighter_budget_produces_a_shorter_show(game):
    generous = B.select_beats(game, pacing=B.PacingConfig(budget_seconds=240.0))
    tight = B.select_beats(game, pacing=B.PacingConfig(budget_seconds=90.0))
    assert tight.total_seconds < generous.total_seconds


def test_every_game_gets_a_beginning_and_an_end(game):
    kinds = [b.beat_type for b in B.select_beats(game).beats]
    for required in (B.BeatType.INTRO, B.BeatType.OPENING, B.BeatType.FINISH, B.BeatType.OUTRO):
        assert required in kinds


def test_beats_run_in_chronological_order(game):
    starts = [b.plies[0] for b in B.select_beats(game).beats if b.plies]
    assert starts == sorted(starts)


def test_detailed_beats_are_never_adjacent(game):
    """
    The constraint that separates this from a sort. Without it the top-scoring
    moves in a tactical flurry are all neighbours, and the script narrates four
    consecutive half-moves then skips twenty.
    """
    pacing = B.PacingConfig(budget_seconds=600.0, min_ply_gap=4)
    sheet = B.select_beats(game, pacing=pacing)
    picked = sorted(
        b.plies[0] for b in sheet.beats
        if b.beat_type in (B.BeatType.MOMENT, B.BeatType.TURNING_POINT)
    )
    assert all(b - a >= 4 for a, b in zip(picked, picked[1:]))


def test_the_ending_is_not_narrated_twice(game):
    """The finish beat owns the last few plies; no moment may re-take them."""
    sheet = B.select_beats(game, pacing=B.PacingConfig(budget_seconds=600.0))
    finish = {p for b in sheet.beats if b.beat_type == B.BeatType.FINISH for p in b.plies}
    moments = {
        p for b in sheet.beats
        if b.beat_type in (B.BeatType.MOMENT, B.BeatType.TURNING_POINT)
        for p in b.plies
    }
    assert not (finish & moments)


def test_a_generous_budget_bridges_the_long_silences(game):
    sheet = B.select_beats(
        game, pacing=B.PacingConfig(budget_seconds=600.0, max_silent_plies=4)
    )
    assert any(b.beat_type == B.BeatType.BRIDGE for b in sheet.beats)


def test_every_beat_records_why_it_earned_its_airtime(game):
    assert all(b.reason for b in B.select_beats(game).beats)


def test_selection_is_deterministic(game):
    first = B.select_beats(game)
    second = B.select_beats(game)
    assert [b.plies for b in first.beats] == [b.plies for b in second.beats]


def test_weights_actually_change_what_is_selected(game):
    """The pacing experiment varies these; they have to matter."""
    swing_only = B.SalienceWeights(swing=1.0, criticality=0, motif=0, material=0,
                                   think_time=0, narrative=0)
    motif_only = B.SalienceWeights(swing=0, criticality=0, motif=1.0, material=0,
                                   think_time=0, narrative=0)
    pacing = B.PacingConfig(budget_seconds=200.0)
    a = {tuple(b.plies) for b in B.select_beats(game, swing_only, pacing).beats}
    b = {tuple(b.plies) for b in B.select_beats(game, motif_only, pacing).beats}
    assert a != b


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def test_flat_baseline_covers_every_half_move(game):
    sheet = B.select_every_move(game)
    assert len(sheet.beats) == game.total_plies
    assert sheet.coverage(game.total_plies) == 1.0


def test_flat_baseline_is_absurdly_long(game):
    """
    Most of the pacing argument in one number: a 17-move game narrated
    move-by-move runs to nearly ten minutes.
    """
    assert B.select_every_move(game).total_seconds > 500


def test_top_n_returns_exactly_n_beats(game):
    assert len(B.select_top_n(game, 8).beats) == 8


def test_top_n_will_happily_pick_adjacent_moves(game):
    """
    Documents the failure the constraints exist to prevent. On this game
    top-N picks two consecutive half-moves of the same combination and
    spends two beats saying one thing.
    """
    picked = sorted(b.plies[0] for b in B.select_top_n(game, 8).beats)
    assert any(b - a == 1 for a, b in zip(picked, picked[1:]))


def test_the_system_is_shorter_than_both_baselines(game):
    system = B.select_beats(game, pacing=B.PacingConfig(budget_seconds=120.0))
    assert system.total_seconds < B.select_every_move(game).total_seconds
    assert system.total_seconds < B.select_top_n(game, 12).total_seconds


def test_every_strategy_labels_itself(game):
    assert B.select_beats(game).strategy == "salience"
    assert B.select_every_move(game).strategy == "every_move"
    assert B.select_top_n(game, 5).strategy == "top_5"
