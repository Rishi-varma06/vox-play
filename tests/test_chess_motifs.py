"""
Tests for tactical motif detection.

Positions are hand-built and minimal so each one isolates a single pattern.
Where a real game is used it is the Opera Game, whose tactics are documented
well enough to serve as an external check.

These detectors will later be scored against the Lichess puzzle database
(CC0, human-tagged themes) for precision and recall. These tests pin the
behaviour that scoring run will measure.
"""

from __future__ import annotations

from pathlib import Path

import chess

from voxplay.chessgame.models import Motif
from voxplay.chessgame.motifs import (
    detect_motifs,
    material_at_risk,
    static_exchange_eval,
)
from voxplay.chessgame.pgn_source import load_pgn

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"


def motifs_for(fen: str, san: str):
    """Detect motifs for `san` played in `fen`."""
    board = chess.Board(fen)
    return detect_motifs(board, board.parse_san(san))


def kinds(hits):
    return {hit.motif for hit in hits}


# ---------------------------------------------------------------------------
# Static exchange evaluation
# ---------------------------------------------------------------------------

def test_taking_a_free_pawn_wins_a_pawn():
    board = chess.Board("4k3/p7/8/8/8/8/8/R3K3 w Q - 0 1")
    assert static_exchange_eval(board, board.parse_san("Rxa7")) == 1


def test_taking_a_defended_pawn_with_a_rook_loses_material():
    """Win a pawn, lose a rook: 1 - 5."""
    board = chess.Board("r3k3/p7/8/8/8/8/8/R3K3 w Q - 0 1")
    assert static_exchange_eval(board, board.parse_san("Rxa7")) == -4


def test_an_even_trade_nets_nothing():
    board = chess.Board("4k3/3n4/8/4N3/8/8/8/4K3 w - - 0 1")
    assert static_exchange_eval(board, board.parse_san("Nxd7")) == 0


def test_a_quiet_move_captures_nothing():
    board = chess.Board()
    assert static_exchange_eval(board, board.parse_san("e4")) == 0


def test_material_at_risk_reports_an_undefended_piece():
    board = chess.Board("4k3/p7/8/8/8/8/8/R3K3 w Q - 0 1")
    assert material_at_risk(board, chess.A7) == 1


def test_material_at_risk_is_zero_when_adequately_defended():
    board = chess.Board("r3k3/p7/8/8/8/8/8/R3K3 w Q - 0 1")
    assert material_at_risk(board, chess.A7) == 0


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------

def test_knight_forking_king_and_rook():
    hits = motifs_for("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "Nc7+")
    assert Motif.FORK in kinds(hits)
    fork = next(h for h in hits if h.motif == Motif.FORK)
    assert "c7" in fork.squares and "e8" in fork.squares and "a8" in fork.squares


def test_fork_description_names_both_targets():
    hits = motifs_for("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "Nc7+")
    fork = next(h for h in hits if h.motif == Motif.FORK)
    assert "king" in fork.description and "rook" in fork.description


def test_attacking_two_defended_pawns_is_not_a_fork():
    """
    Without a value filter, any queen move would register as a fork.

    Here the queen eyes c6, b5 and d5; every one is defended by another pawn,
    and none is worth more than a queen, so none is a target worth having.
    """
    hits = motifs_for("7k/1p6/p1p5/1p1p4/8/8/2Q5/K7 w - - 0 1", "Qc4")
    assert Motif.FORK not in kinds(hits)


# ---------------------------------------------------------------------------
# Pin and skewer
# ---------------------------------------------------------------------------

def test_knight_pinned_against_the_king():
    hits = motifs_for("4k3/8/8/4n3/8/8/8/K2R4 w - - 0 1", "Re1")
    pin = next(h for h in hits if h.motif == Motif.PIN)
    assert pin.squares == ["e1", "e5", "e8"]
    assert "to the king" in pin.description


def test_queen_skewered_in_front_of_a_rook():
    hits = motifs_for("4r2k/8/8/4q3/8/8/8/3R3K w - - 0 1", "Re1")
    skewer = next(h for h in hits if h.motif == Motif.SKEWER)
    assert skewer.squares == ["e1", "e5", "e8"]


def test_pin_and_skewer_are_distinguished_by_which_piece_is_worth_more():
    """Same geometry; only the ordering of the two values differs."""
    pinned = kinds(motifs_for("4k3/8/8/4n3/8/8/8/K2R4 w - - 0 1", "Re1"))
    skewered = kinds(motifs_for("4r2k/8/8/4q3/8/8/8/3R3K w - - 0 1", "Re1"))
    assert Motif.PIN in pinned and Motif.SKEWER not in pinned
    assert Motif.SKEWER in skewered and Motif.PIN not in skewered


def test_a_pinned_pawn_is_not_worth_reporting():
    """
    Geometrically a pin, conversationally nothing. Unfiltered, pawn pins
    produced 17 "pins" across the 33-move Opera Game and buried the two that
    mattered.
    """
    hits = motifs_for("3q3k/8/3p4/8/8/8/8/Q3K3 w - - 0 1", "Qd1")
    assert Motif.PIN not in kinds(hits)


def test_a_skewer_must_win_more_than_a_pawn():
    """Queen in front, pawn behind: real geometry, not worth the word."""
    hits = motifs_for("7k/4p3/8/4q3/8/8/8/3R2K1 w - - 0 1", "Re1")
    assert Motif.SKEWER not in kinds(hits)


def test_two_equal_pieces_on_a_line_are_neither():
    hits = motifs_for("4r2k/8/8/4r3/8/8/8/K2R4 w - - 0 1", "Re1")
    assert Motif.PIN not in kinds(hits) and Motif.SKEWER not in kinds(hits)


# ---------------------------------------------------------------------------
# Discovered attack and double check
# ---------------------------------------------------------------------------

def test_moving_a_blocker_uncovers_the_bishop():
    """The white king sits off the h-file, or the queen would be checking it."""
    hits = motifs_for("k6q/8/8/8/3N4/8/8/B5K1 w - - 0 1", "Nb5")
    discovered = next(h for h in hits if h.motif == Motif.DISCOVERED_ATTACK)
    assert "d4" in discovered.squares and "a1" in discovered.squares


def test_a_normal_move_is_not_a_discovery():
    assert Motif.DISCOVERED_ATTACK not in kinds(motifs_for(chess.STARTING_FEN, "e4"))


def test_knight_stepping_aside_with_check_is_a_double_check():
    """Nd6 checks from d6 and uncovers the rook down the e-file at once."""
    hits = motifs_for("4k3/8/8/8/4N3/8/8/4R2K w - - 0 1", "Nd6+")
    assert Motif.DOUBLE_CHECK in kinds(hits)


def test_an_ordinary_check_is_not_a_double_check():
    hits = motifs_for("4k3/8/8/8/8/8/8/3R3K w - - 0 1", "Re1+")
    assert Motif.DOUBLE_CHECK not in kinds(hits)


# ---------------------------------------------------------------------------
# Mating patterns
# ---------------------------------------------------------------------------

def test_back_rank_mate_is_named():
    hits = motifs_for("6k1/5ppp/8/8/8/8/8/R6K w - - 0 1", "Ra8#")
    mate = next(h for h in hits if h.motif == Motif.BACK_RANK_MATE)
    assert "g8" in mate.squares


def test_smothered_mate_is_named():
    hits = motifs_for("6rk/6pp/3N4/8/8/8/8/7K w - - 0 1", "Nf7#")
    assert Motif.SMOTHERED_MATE in kinds(hits)


def test_a_non_mating_move_names_no_mate_pattern():
    hits = kinds(motifs_for("6k1/5ppp/8/8/8/8/8/R6K w - - 0 1", "Ra7"))
    assert Motif.BACK_RANK_MATE not in hits and Motif.SMOTHERED_MATE not in hits


# ---------------------------------------------------------------------------
# Sacrifice
# ---------------------------------------------------------------------------

def test_hanging_a_rook_reads_as_a_sacrifice():
    hits = motifs_for("r3k3/8/8/8/8/8/8/R3K3 w Q - 0 1", "Ra7")
    sacrifice = next(h for h in hits if h.motif == Motif.SACRIFICE)
    assert "a7" in sacrifice.squares


def test_an_even_trade_is_not_a_sacrifice():
    assert Motif.SACRIFICE not in kinds(
        motifs_for("4k3/3n4/8/4N3/8/8/8/4K3 w - - 0 1", "Nxd7")
    )


def test_a_safe_developing_move_is_not_a_sacrifice():
    assert Motif.SACRIFICE not in kinds(motifs_for(chess.STARTING_FEN, "Nf3"))


# ---------------------------------------------------------------------------
# Pawn motifs
# ---------------------------------------------------------------------------

def test_promotion_is_reported():
    hits = motifs_for("8/P7/8/8/8/8/8/K5k1 w - - 0 1", "a8=Q")
    promotion = next(h for h in hits if h.motif == Motif.PROMOTION)
    assert "queen" in promotion.description


def test_en_passant_is_reported():
    board = chess.Board()
    for san in ("e4", "a6", "e5", "d5"):
        board.push_san(san)
    assert Motif.EN_PASSANT in kinds(detect_motifs(board, board.parse_san("exd6")))


def test_a_first_move_pawn_push_is_not_an_advanced_pawn():
    assert Motif.ADVANCED_PAWN not in kinds(motifs_for(chess.STARTING_FEN, "e4"))


# ---------------------------------------------------------------------------
# Hanging pieces
# ---------------------------------------------------------------------------

def test_an_undefended_piece_left_en_prise_is_flagged():
    """The rook swings to a4 and only then attacks the knight."""
    hits = motifs_for("4k3/8/8/n7/7R/8/8/4K3 w - - 0 1", "Ra4")
    hanging = [h for h in hits if h.motif == Motif.HANGING_PIECE]
    assert any("a5" in h.squares for h in hanging)


def test_the_opening_position_has_nothing_hanging():
    assert Motif.HANGING_PIECE not in kinds(motifs_for(chess.STARTING_FEN, "e4"))


# ---------------------------------------------------------------------------
# Whole-game behaviour
# ---------------------------------------------------------------------------

def test_the_opera_game_queen_sacrifice_is_detected():
    """16.Qb8+ hands over the queen to force mate."""
    game = load_pgn(SAMPLE_PGN)
    sac = next(m for m in game.moves if m.san == "Qb8+")
    hits = detect_motifs(chess.Board(sac.fen_before), chess.Move.from_uci(sac.uci))
    assert Motif.SACRIFICE in kinds(hits)


def test_the_opera_game_ends_in_a_named_mate_pattern():
    game = load_pgn(SAMPLE_PGN)
    final = game.moves[-1]
    hits = detect_motifs(chess.Board(final.fen_before), chess.Move.from_uci(final.uci))
    assert Motif.BACK_RANK_MATE in kinds(hits)


def test_a_standing_pin_is_reported_only_when_it_is_created():
    """
    Bg5 pins the f6 knight, and that pin stands for five more moves. Before
    the standing-motif filter it was re-reported on every one of them, which
    is how commentary starts sounding like a machine reading a database.
    """
    game = load_pgn(SAMPLE_PGN)
    announced = [
        move.san for move in game.moves
        if Motif.PIN in kinds(
            detect_motifs(chess.Board(move.fen_before), chess.Move.from_uci(move.uci))
        )
    ]
    assert announced == ["Bg4", "Bg5"]


def test_every_move_of_a_real_game_is_analysable():
    """No detector may raise on any position a real game passes through."""
    game = load_pgn(SAMPLE_PGN)
    for move in game.moves:
        detect_motifs(chess.Board(move.fen_before), chess.Move.from_uci(move.uci))


def test_motifs_carry_a_description_and_real_squares():
    game = load_pgn(SAMPLE_PGN)
    for move in game.moves:
        for hit in detect_motifs(chess.Board(move.fen_before), chess.Move.from_uci(move.uci)):
            assert hit.description
            assert all(sq in chess.SQUARE_NAMES for sq in hit.squares)
