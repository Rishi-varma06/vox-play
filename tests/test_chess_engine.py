"""
Tests for the analysis layer.

Everything here runs on MockAnalysisEngine, so the suite needs no Stockfish
binary. The mock's evaluations are meaningless as chess, but they are stable
and position-dependent, which is exactly what is needed to test the wiring.
"""

from __future__ import annotations

from pathlib import Path

import chess
import pytest

from voxplay.chessgame.engine import (
    AnalysisConfig,
    AnalysisError,
    MockAnalysisEngine,
    analyse_game,
    get_engine,
    terminal_evaluation,
)
from voxplay.chessgame.evaluation import win_pct_white
from voxplay.chessgame.models import MoveQuality
from voxplay.chessgame.pgn_source import load_pgn, parse_pgn_text

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"

FOOLS_MATE = "1. f3 e5 2. g4 Qh4# 0-1"
SCHOLARS_MATE = "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0"
STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


class CountingEngine(MockAnalysisEngine):
    """Mock that records how many positions it was asked about."""

    def __init__(self, config: AnalysisConfig = AnalysisConfig()) -> None:
        super().__init__(config)
        self.calls = 0

    def analyse_position(self, board):
        self.calls += 1
        return super().analyse_position(board)


@pytest.fixture(scope="module")
def opera():
    return load_pgn(SAMPLE_PGN)


# ---------------------------------------------------------------------------
# Terminal positions
# ---------------------------------------------------------------------------

def test_ongoing_position_is_not_terminal():
    assert terminal_evaluation(chess.Board()) is None


def test_white_checkmated_scores_zero():
    board = parse_pgn_text(FOOLS_MATE).moves[-1]
    ev = terminal_evaluation(chess.Board(board.fen_after))
    assert ev is not None and win_pct_white(ev) == 0.0


def test_black_checkmated_scores_one_hundred():
    board = parse_pgn_text(SCHOLARS_MATE).moves[-1]
    ev = terminal_evaluation(chess.Board(board.fen_after))
    assert ev is not None and win_pct_white(ev) == 100.0


def test_stalemate_is_a_dead_heat():
    ev = terminal_evaluation(chess.Board(STALEMATE_FEN))
    assert ev is not None and win_pct_white(ev) == 50.0


# ---------------------------------------------------------------------------
# Mock engine
# ---------------------------------------------------------------------------

def test_mock_is_deterministic():
    board = chess.Board()
    first = MockAnalysisEngine().analyse_position(board)
    second = MockAnalysisEngine().analyse_position(board)
    assert [line.move_uci for line in first] == [line.move_uci for line in second]


def test_mock_returns_multipv_lines_ranked():
    lines = MockAnalysisEngine(AnalysisConfig(multipv=3)).analyse_position(chess.Board())
    assert [line.rank for line in lines] == [1, 2, 3]


def test_mock_only_suggests_legal_moves():
    board = chess.Board()
    legal = {m.uci() for m in board.legal_moves}
    assert all(line.move_uci in legal for line in MockAnalysisEngine().analyse_position(board))


def test_engine_returns_nothing_for_a_finished_game():
    assert MockAnalysisEngine().analyse_position(chess.Board(STALEMATE_FEN)) == []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_builds_the_mock():
    with get_engine("mock") as engine:
        assert engine.name == "mock"


def test_factory_rejects_an_unknown_engine():
    with pytest.raises(AnalysisError):
        get_engine("houdini")


def test_auto_never_fails_even_without_stockfish():
    """
    The demo path must run on a machine with no engine installed.

    Held in a `with` block deliberately: on a machine that DOES have
    Stockfish this spawns a real subprocess, and leaking it hangs pytest at
    exit waiting on the child. Always close an AnalysisEngine.
    """
    with get_engine("auto") as engine:
        assert engine.name in {"stockfish", "mock"}


# ---------------------------------------------------------------------------
# Whole-game analysis
# ---------------------------------------------------------------------------

def test_moves_start_unanalysed(opera):
    assert all(m.analysis is None for m in opera.moves)


def test_analysis_populates_every_move():
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    assert all(m.analysis is not None for m in game.moves)
    assert all(isinstance(m.analysis.quality, MoveQuality) for m in game.moves)


def test_analysis_fills_win_percentage_on_every_state():
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    assert all(m.state.win_pct_white is not None for m in game.moves)
    assert game.events[0].state.win_pct_white is not None


def test_final_position_is_a_win_for_white():
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    assert game.events[-1].state.win_pct_white == 100.0
    assert game.moves[-1].analysis.win_pct_after == 100.0


def test_win_loss_is_never_negative():
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    assert all(m.analysis.win_loss >= 0 for m in game.moves)


def test_each_position_is_evaluated_once_not_twice():
    """
    The N+1 optimisation: the position after move i is the position before
    move i+1. A naive per-move implementation would ask the engine 2N times.
    33 moves means 34 positions, one of which is checkmate and needs no
    engine, so 33 calls rather than 66.
    """
    engine = CountingEngine()
    game = analyse_game(load_pgn(SAMPLE_PGN), engine)
    assert game.total_plies == 33
    assert engine.calls == 33


def test_capturing_a_queen_shows_a_nine_pawn_swing():
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    accepted = next(m for m in game.moves if m.san == "Nxb8")
    assert accepted.analysis.material_swing == 9


def test_quiet_moves_show_no_material_swing():
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    opening = game.moves[0]
    assert opening.analysis.material_swing == 0


def test_analysing_a_game_with_no_moves_is_a_no_op():
    empty = parse_pgn_text('[Result "*"]\n\n*')
    assert analyse_game(empty, MockAnalysisEngine()).moves == []
