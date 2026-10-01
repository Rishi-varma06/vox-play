"""
Tests for classifier agreement against Lichess annotations.

Offline: games are synthesised as PGN text with the same `[%eval]` comments
and NAG suffixes Lichess exports, so the suite never needs the network.

The chaining test matters most. Lichess attaches an evaluation to the
position AFTER a move, so a move's "before" is the previous node's value.
Reading them off by one would shift every classification by one move and
still produce a complete, plausible confusion matrix.
"""

from __future__ import annotations

import pytest

from voxplay.chessgame.agreement import (
    CLASSES,
    NAG_TO_QUALITY,
    NOT_AN_ERROR,
    AgreementReport,
    annotated_moves,
    classify_with_our_rule,
    compare,
    read_games,
    rule_agreement,
)
from voxplay.chessgame.evaluation import ClassificationThresholds
from voxplay.chessgame.models import Color, MoveQuality

HEADER = '[Event "Test"]\n[Site "https://lichess.org/testgame"]\n[Result "*"]\n\n'

PLAIN = HEADER + "1. e4 { [%eval 0.2] } 1... e5 { [%eval 0.1] } *"
LABELLED = HEADER + (
    "1. e4 { [%eval 0.2] } 1... e5?! { [%eval 0.9] } "
    "2. Nf3 { [%eval 0.8] } 2... Nc6? { [%eval 2.0] } "
    "3. Bc4 { [%eval 1.9] } 3... d6?? { [%eval 6.0] } *"
)


def only_game(text):
    return next(iter(read_games(text)))


# ---------------------------------------------------------------------------
# Reading annotations
# ---------------------------------------------------------------------------

def test_lichess_nags_map_to_our_qualities():
    assert NAG_TO_QUALITY[6] is MoveQuality.INACCURACY   # ?!
    assert NAG_TO_QUALITY[2] is MoveQuality.MISTAKE      # ?
    assert NAG_TO_QUALITY[4] is MoveQuality.BLUNDER      # ??


def test_every_move_is_read():
    assert len(annotated_moves(only_game(PLAIN))) == 2


def test_evaluations_chain_so_before_is_the_previous_position():
    """
    Lichess attaches [%eval] AFTER a move. Off by one here shifts every
    classification by a move and still produces a plausible matrix.
    """
    first, second = annotated_moves(only_game(PLAIN))
    assert first.win_before == 50.0           # start is equal by definition
    assert second.win_before == first.win_after
    assert first.win_after > 50.0             # +0.2 favours White


def test_an_unlabelled_move_is_not_an_error():
    assert annotated_moves(only_game(PLAIN))[0].lichess == NOT_AN_ERROR


def test_labels_are_read_from_the_move_suffix():
    moves = annotated_moves(only_game(LABELLED))
    assert [m.lichess for m in moves] == [
        NOT_AN_ERROR, "inaccuracy", NOT_AN_ERROR, "mistake", NOT_AN_ERROR, "blunder",
    ]


def test_colours_alternate():
    moves = annotated_moves(only_game(PLAIN))
    assert moves[0].color == Color.WHITE and moves[1].color == Color.BLACK


def test_a_game_without_evaluations_yields_nothing_to_score():
    assert annotated_moves(only_game(HEADER + "1. e4 e5 *")) == []


def test_an_unanalysed_tail_stops_the_scoring():
    """Half an analysed game is still usable; the tail just is not scored."""
    text = HEADER + "1. e4 { [%eval 0.2] } 1... e5 2. Nf3 *"
    assert len(annotated_moves(only_game(text))) == 1


def test_the_game_id_is_recovered_from_the_site_header():
    assert annotated_moves(only_game(PLAIN))[0].game_id == "testgame"


# ---------------------------------------------------------------------------
# Our rule
# ---------------------------------------------------------------------------

def test_our_rule_labels_a_large_swing_as_a_blunder():
    moves = annotated_moves(only_game(LABELLED))
    last = moves[-1]  # Black goes from +1.9 to +6.0 against itself
    assert classify_with_our_rule(last) == "blunder"


def test_good_and_best_collapse_to_not_an_error():
    """The comparison is about errors; Lichess has no label for a fine move."""
    moves = annotated_moves(only_game(PLAIN))
    assert classify_with_our_rule(moves[0]) == NOT_AN_ERROR


def test_thresholds_change_our_verdicts():
    moves = annotated_moves(only_game(LABELLED))
    lenient = ClassificationThresholds(inaccuracy=60.0, mistake=70.0, blunder=80.0)
    assert classify_with_our_rule(moves[-1], lenient) == NOT_AN_ERROR


# ---------------------------------------------------------------------------
# Agreement statistics
# ---------------------------------------------------------------------------

def test_perfect_agreement_is_kappa_one():
    pairs = [("blunder", "blunder")] * 10 + [(NOT_AN_ERROR, NOT_AN_ERROR)] * 10
    assert compare(pairs, "x").kappa == pytest.approx(1.0)


def test_chance_level_agreement_is_kappa_zero():
    """
    Both raters call everything "ok": raw agreement is 100% and skill is nil.
    This is why kappa is the headline and raw agreement is not -- most moves
    are not errors, so a do-nothing classifier scores over 90% raw.
    """
    report = compare([(NOT_AN_ERROR, NOT_AN_ERROR)] * 50, "x")
    assert report.observed == 1.0
    assert report.kappa == 0.0


def test_total_disagreement_is_negative_kappa():
    pairs = [("blunder", NOT_AN_ERROR)] * 10 + [(NOT_AN_ERROR, "blunder")] * 10
    assert compare(pairs, "x").kappa < 0


def test_per_class_agreement_is_reported():
    pairs = [("blunder", "blunder")] * 8 + [(NOT_AN_ERROR, "blunder")] * 2
    assert compare(pairs, "x").error_recall("blunder") == pytest.approx(0.8)


def test_a_class_lichess_never_used_has_no_recall():
    assert compare([(NOT_AN_ERROR, NOT_AN_ERROR)], "x").error_recall("mistake") is None


def test_an_empty_comparison_does_not_divide_by_zero():
    report = compare([], "x")
    assert report.observed == 0.0 and report.kappa == 0.0


def test_the_matrix_counts_every_move():
    moves = annotated_moves(only_game(LABELLED))
    assert rule_agreement(moves).moves == len(moves)


def test_the_table_is_ascii_and_names_both_sides():
    table = compare([("blunder", "blunder")], "test basis").table()
    table.encode("ascii")
    assert "lichess" in table and "ours" in table
    assert "kappa" in table


def test_every_class_appears_in_the_table():
    table = compare([(NOT_AN_ERROR, NOT_AN_ERROR)], "x").table()
    for cls in CLASSES:
        assert cls in table


def test_the_report_records_what_it_was_measured_on():
    assert AgreementReport(basis="our rule on Lichess evaluations").basis
