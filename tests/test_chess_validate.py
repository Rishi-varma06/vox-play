"""
Tests for the motif validation harness.

Entirely offline -- puzzles are constructed directly rather than fetched, so
the suite never depends on Lichess being reachable or on a rate limit.

The position-reconstruction tests matter most. /api/puzzle/next does not
return a FEN, so getting the ply convention wrong would silently score every
detector against the wrong board and produce a plausible, meaningless table.
"""

from __future__ import annotations

import chess
import pytest

from voxplay.chessgame.models import Motif
from voxplay.chessgame.validate import (
    CONTROL_THEMES,
    SCORABLE,
    Puzzle,
    ThemeScore,
    ValidationReport,
    detected_motifs,
    load_cached,
    puzzle_fen,
    score_theme,
)


def board_after(sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


# ---------------------------------------------------------------------------
# Position reconstruction
# ---------------------------------------------------------------------------

def test_an_explicit_fen_is_used_as_given():
    fen = "7k/8/8/8/8/8/8/K6R w - - 0 1"
    assert puzzle_fen({"fen": fen}, {"pgn": "e4 e5"}) == fen


def test_the_position_is_replayed_to_initial_ply_plus_one():
    """
    Verified against the daily puzzle, which supplies both a FEN and the
    moves: board and side to move agree at initialPly + 1, not at initialPly.
    Off by one here scores every detector against the wrong board.
    """
    game = {"pgn": "e4 e5 Nf3 Nc6"}
    fen = puzzle_fen({"initialPly": 2}, game)
    board = chess.Board(fen)
    assert board.board_fen() == board_after(["e4", "e5", "Nf3"]).board_fen()
    assert board.turn == chess.BLACK


def test_the_solution_move_is_legal_in_the_reconstructed_position():
    game = {"pgn": "e4 e5 Nf3 Nc6 Bc4 Nf6"}
    puzzle = Puzzle(id="x", fen=puzzle_fen({"initialPly": 5}, game), solution=["f3g5"])
    assert puzzle.move in chess.Board(puzzle.fen).legal_moves


def test_missing_data_yields_no_position():
    assert puzzle_fen({"initialPly": 2}, None) is None
    assert puzzle_fen({}, {"pgn": "e4 e5"}) is None


def test_an_unplayable_move_list_yields_no_position():
    assert puzzle_fen({"initialPly": 3}, {"pgn": "e4 e5 Qxz9"}) is None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_a_known_fork_is_detected():
    puzzle = Puzzle(
        id="fork1", fen="r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1",
        solution=["b5c7"], themes=["fork"],
    )
    assert Motif.FORK in detected_motifs(puzzle)


def test_a_puzzle_with_no_solution_detects_nothing():
    assert detected_motifs(Puzzle(id="x", fen=chess.STARTING_FEN)) == set()


def test_an_unparseable_position_is_skipped_not_counted():
    """A data problem must not be charged to the detectors as a miss."""
    assert detected_motifs(Puzzle(id="x", fen="not a fen", solution=["e2e4"])) == set()


def test_an_illegal_solution_move_is_skipped():
    puzzle = Puzzle(id="x", fen=chess.STARTING_FEN, solution=["e2e5"])
    assert detected_motifs(puzzle) == set()


def test_a_malformed_uci_string_is_skipped():
    assert Puzzle(id="x", fen=chess.STARTING_FEN, solution=["zz99"]).move is None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

FORK_PUZZLE = Puzzle(
    id="f", fen="r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", solution=["b5c7"], themes=["fork"]
)
QUIET_PUZZLE = Puzzle(
    id="q", fen=chess.STARTING_FEN, solution=["e2e4"], themes=["opening"]
)


def test_recall_counts_detections_among_tagged_puzzles():
    score = score_theme(Motif.FORK, "fork", [FORK_PUZZLE, QUIET_PUZZLE], [])
    assert score.positives == 2
    assert score.detected_in_positives == 1
    assert score.recall == pytest.approx(0.5)


def test_controls_measure_how_often_the_detector_fires_anyway():
    # Same fork geometry, but the puzzle is not about a fork -- precisely the
    # case the control sample exists to catch.
    untagged = Puzzle(id="u", fen=FORK_PUZZLE.fen, solution=["b5c7"], themes=["endgame"])
    score = score_theme(Motif.FORK, "fork", [], [untagged, QUIET_PUZZLE, QUIET_PUZZLE])
    assert score.controls == 3
    assert score.fired_in_controls == 1
    assert score.false_positive_rate == pytest.approx(1 / 3)


def test_a_control_actually_tagged_with_the_theme_is_discarded():
    """
    Otherwise a genuine detection is scored as a false positive purely
    because the puzzle landed in the control pool.
    """
    tagged = Puzzle(id="t", fen=FORK_PUZZLE.fen, solution=["b5c7"],
                    themes=["endgame", "fork"])
    score = score_theme(Motif.FORK, "fork", [], [tagged, QUIET_PUZZLE])
    assert score.controls == 1


def test_empty_samples_do_not_divide_by_zero():
    score = score_theme(Motif.FORK, "fork", [], [])
    assert score.recall == 0.0 and score.false_positive_rate == 0.0


# ---------------------------------------------------------------------------
# Configuration and reporting
# ---------------------------------------------------------------------------

def test_every_scorable_motif_is_a_real_motif():
    for motif in SCORABLE:
        assert isinstance(motif, Motif)


def test_scorable_theme_names_match_the_motif_values():
    """
    The Motif enum was named after Lichess themes on purpose, so validation
    is a direct comparison rather than a mapping that can drift.
    """
    for motif, theme in SCORABLE.items():
        assert motif.value == theme


def test_control_themes_are_not_themselves_scored_motifs():
    scored = set(SCORABLE.values())
    assert not scored.intersection(CONTROL_THEMES)


def test_the_report_table_renders_every_score():
    report = ValidationReport(
        scores=[
            ThemeScore(motif=Motif.FORK, theme="fork", positives=10,
                       detected_in_positives=8, controls=20, fired_in_controls=2),
            ThemeScore(motif=Motif.PIN, theme="pin", positives=10,
                       detected_in_positives=5, controls=20, fired_in_controls=1),
        ],
        puzzles_used=40,
    )
    table = report.table()
    assert "fork" in table and "pin" in table
    assert "80.0%" in table and "50.0%" in table
    assert "MEAN" in table


def test_the_table_is_ascii_only():
    """The project's terminal is CP1252; non-ASCII output breaks it."""
    report = ValidationReport(scores=[
        ThemeScore(motif=Motif.FORK, theme="fork", positives=1,
                   detected_in_positives=1, controls=1, fired_in_controls=0)
    ])
    report.table().encode("ascii")


def test_a_missing_cache_reads_as_empty(tmp_path):
    assert load_cached(tmp_path, "fork") == []


def test_a_corrupt_cache_reads_as_empty_rather_than_raising(tmp_path):
    (tmp_path / "puzzles_fork.json").write_text("{ not json", encoding="utf-8")
    assert load_cached(tmp_path, "fork") == []
