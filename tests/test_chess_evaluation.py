"""
Tests for the centipawn -> win probability -> classification chain.

Pure maths, no engine and no board. These are the numbers every downstream
decision is built on, so they are pinned tightly.
"""

from __future__ import annotations

import pytest

from voxplay.chessgame.evaluation import (
    ClassificationThresholds,
    accuracy_pct,
    classify_move,
    cp_to_win_pct,
    describe_eval,
    win_loss_for_move,
    win_pct_for,
    win_pct_white,
)
from voxplay.chessgame.models import Color, Evaluation, MoveQuality


# ---------------------------------------------------------------------------
# cp -> win%
# ---------------------------------------------------------------------------

def test_equal_position_is_exactly_fifty_percent():
    assert cp_to_win_pct(0) == pytest.approx(50.0)


def test_win_pct_is_symmetric_about_equality():
    assert cp_to_win_pct(300) == pytest.approx(100.0 - cp_to_win_pct(-300))


def test_win_pct_is_monotonic_in_centipawns():
    values = [cp_to_win_pct(cp) for cp in range(-800, 801, 100)]
    assert values == sorted(values)


def test_win_pct_clamps_beyond_the_saturation_point():
    assert cp_to_win_pct(5000) == pytest.approx(cp_to_win_pct(1000))
    assert cp_to_win_pct(-5000) == pytest.approx(cp_to_win_pct(-1000))


def test_the_same_centipawns_matter_less_in_a_decided_position():
    """The whole reason this module exists -- 100cp is not a fixed quantity."""
    near_equal = cp_to_win_pct(0) - cp_to_win_pct(-100)
    already_won = cp_to_win_pct(900) - cp_to_win_pct(800)
    assert near_equal > already_won * 5


# ---------------------------------------------------------------------------
# Mate handling
# ---------------------------------------------------------------------------

def test_white_mating_is_one_hundred():
    assert win_pct_white(Evaluation(mate=3)) == 100.0


def test_black_mating_is_zero():
    assert win_pct_white(Evaluation(mate=-2)) == 0.0


def test_mate_zero_means_white_has_been_mated():
    """Matches chess.engine.Mate(0), which is a loss for the POV side."""
    assert win_pct_white(Evaluation(mate=0)) == 0.0


def test_mate_distance_does_not_change_probability():
    assert win_pct_white(Evaluation(mate=1)) == win_pct_white(Evaluation(mate=9))


def test_empty_evaluation_is_neutral():
    assert win_pct_white(Evaluation()) == 50.0


def test_win_pct_for_black_is_the_complement():
    ev = Evaluation(cp=200)
    assert win_pct_for(ev, Color.BLACK) == pytest.approx(100.0 - win_pct_for(ev, Color.WHITE))


# ---------------------------------------------------------------------------
# Win loss
# ---------------------------------------------------------------------------

def test_white_losing_ground_is_a_positive_loss():
    assert win_loss_for_move(70.0, 40.0, Color.WHITE) == pytest.approx(30.0)


def test_black_losing_ground_is_a_positive_loss():
    """Black's position worsening means White's win% went UP."""
    assert win_loss_for_move(40.0, 70.0, Color.BLACK) == pytest.approx(30.0)


def test_improving_your_position_is_never_a_negative_loss():
    """Search noise can revise an earlier verdict; that is not a real gain."""
    assert win_loss_for_move(40.0, 70.0, Color.WHITE) == 0.0


# ---------------------------------------------------------------------------
# Accuracy
# ---------------------------------------------------------------------------

def test_perfect_move_scores_about_one_hundred():
    assert accuracy_pct(0.0) == pytest.approx(100.0, abs=0.01)


def test_accuracy_falls_as_win_probability_is_thrown_away():
    assert accuracy_pct(5.0) > accuracy_pct(20.0) > accuracy_pct(50.0)


def test_accuracy_stays_inside_zero_to_one_hundred():
    assert 0.0 <= accuracy_pct(100.0) <= 100.0


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "win_loss,expected",
    [
        (0.0, MoveQuality.GOOD),
        (9.9, MoveQuality.GOOD),
        (10.0, MoveQuality.INACCURACY),
        (19.9, MoveQuality.INACCURACY),
        (20.0, MoveQuality.MISTAKE),
        (29.9, MoveQuality.MISTAKE),
        (30.0, MoveQuality.BLUNDER),
        (95.0, MoveQuality.BLUNDER),
    ],
)
def test_classification_thresholds(win_loss, expected):
    assert classify_move(win_loss) == expected


def test_engine_top_move_is_labelled_best():
    assert classify_move(0.0, is_best=True) == MoveQuality.BEST


def test_book_move_is_labelled_book():
    assert classify_move(2.0, in_book=True) == MoveQuality.BOOK


def test_book_does_not_hide_a_blunder():
    """Opening databases contain bad theory; that is worth talking about."""
    assert classify_move(45.0, in_book=True) == MoveQuality.BLUNDER


def test_thresholds_are_tunable():
    strict = ClassificationThresholds(inaccuracy=5.0, mistake=10.0, blunder=15.0)
    assert classify_move(16.0, thresholds=strict) == MoveQuality.BLUNDER
    assert classify_move(16.0) == MoveQuality.INACCURACY


def test_classifier_never_invents_brilliancies():
    """BRILLIANT needs sacrifice detection, which lives in a later layer."""
    qualities = {classify_move(x / 2.0) for x in range(0, 200)}
    assert MoveQuality.BRILLIANT not in qualities
    assert MoveQuality.GREAT not in qualities


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------

def test_balanced_position_is_described_as_balanced():
    assert describe_eval(50.0) == "the position is roughly balanced"


def test_extremes_are_described_as_winning():
    assert "black" in describe_eval(1.0)
    assert "white" in describe_eval(99.5)


def test_every_win_percentage_has_a_description():
    for pct in range(0, 101):
        assert describe_eval(float(pct))
