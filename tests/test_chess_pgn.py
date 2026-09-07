"""
Tests for PGN -> event stream parsing.

Uses the 1858 Opera Game (public domain), which is short but exercises almost
everything the parser has to get right: captures, checks, queenside castling,
a queen sacrifice, and mate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from voxplay.chessgame.models import (
    ChessEvent,
    ChessEventType,
    Color,
    GamePhase,
    GameResult,
    GameStartEvent,
    GameEndEvent,
    MoveEvent,
)
from voxplay.chessgame.pgn_source import (
    _extract_lichess_id,
    iter_games,
    load_pgn,
    parse_pgn_text,
)

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"

CLOCK_PGN = """[Event "Test"]
[White "A"]
[Black "B"]
[Result "*"]
[TimeControl "300+3"]

1. e4 { [%clk 0:05:00] } e5 { [%clk 0:04:55] } 2. Nf3 { [%clk 0:04:50] } Nc6 { [%clk 0:04:40] } *
"""

EN_PASSANT_PGN = """[Event "Test"]
[Result "*"]

1. e4 a6 2. e5 d5 3. exd6 *
"""

PROMOTION_PGN = """[Event "Test"]
[Result "*"]
[FEN "8/P7/8/8/8/8/8/K5k1 w - - 0 1"]
[SetUp "1"]

1. a8=Q *
"""


@pytest.fixture(scope="module")
def opera():
    return load_pgn(SAMPLE_PGN)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_headers_are_parsed(opera):
    assert opera.meta.white == "Paul Morphy"
    assert opera.meta.result == GameResult.WHITE_WINS
    assert opera.meta.eco == "C41"


def test_unknown_headers_become_none():
    """PGN writes '?' and '-' for unknown; those must not leak downstream."""
    parsed = parse_pgn_text('[White "?"]\n[Round "-"]\n[Result "*"]\n\n1. e4 *')
    assert parsed.meta.white is None


def test_game_id_prefers_the_platform_id_from_the_site_url():
    parsed = parse_pgn_text('[Site "https://lichess.org/abcd1234"]\n[Result "*"]\n\n1. e4 *')
    assert parsed.meta.game_id == "abcd1234"
    assert parsed.meta.source == "lichess"


def test_game_id_falls_back_to_a_slug():
    parsed = parse_pgn_text('[White "A B"]\n[Black "C D"]\n[Result "*"]\n\n1. e4 *')
    assert parsed.meta.game_id == "A-B_C-D_nodate"


# ---------------------------------------------------------------------------
# Event stream shape
# ---------------------------------------------------------------------------

def test_stream_is_bookended_by_start_and_end_events(opera):
    assert isinstance(opera.events[0], GameStartEvent)
    assert isinstance(opera.events[-1], GameEndEvent)


def test_opera_game_has_thirty_three_plies(opera):
    assert opera.total_plies == 33
    assert opera.events[-1].total_plies == 33


def test_plies_are_numbered_from_one_without_gaps(opera):
    assert [m.ply for m in opera.moves] == list(range(1, 34))


def test_colours_alternate_starting_with_white(opera):
    colours = [m.color for m in opera.moves]
    assert colours[0] == Color.WHITE
    assert all(a != b for a, b in zip(colours, colours[1:]))


def test_events_round_trip_through_the_discriminated_union(opera):
    adapter = TypeAdapter(ChessEvent)
    for event in opera.events:
        restored = adapter.validate_python(event.model_dump())
        assert restored.event_type == event.event_type


# ---------------------------------------------------------------------------
# Move detail
# ---------------------------------------------------------------------------

def test_the_game_ends_in_checkmate(opera):
    final = opera.moves[-1]
    assert final.san == "Rd8#"
    assert final.is_checkmate
    assert final.state.is_checkmate


def test_queenside_castling_is_flagged(opera):
    castle = next(m for m in opera.moves if m.san == "O-O-O")
    assert castle.is_castle
    assert castle.color == Color.WHITE
    assert castle.move_number == 12


def test_captures_record_what_was_taken(opera):
    """16...Nxb8 accepts Morphy's queen sacrifice."""
    capture = next(m for m in opera.moves if m.san == "Nxb8")
    assert capture.is_capture
    assert capture.captured_piece == "queen"
    assert capture.color == Color.BLACK


def test_checks_are_flagged(opera):
    sacrifice = next(m for m in opera.moves if m.san == "Qb8+")
    assert sacrifice.is_check
    assert not sacrifice.is_capture
    assert not sacrifice.is_checkmate


def test_piece_names_are_spoken_words_not_notation(opera):
    """The TTS layer must never receive 'N' or 'Nf3'."""
    assert {m.piece for m in opera.moves} <= {
        "pawn", "knight", "bishop", "rook", "queen", "king",
    }


def test_squares_are_recorded_separately_from_san(opera):
    opening = opera.moves[0]
    assert (opening.san, opening.from_square, opening.to_square) == ("e4", "e2", "e4")


def test_fens_chain_so_each_move_starts_where_the_last_ended(opera):
    for previous, current in zip(opera.moves, opera.moves[1:]):
        assert previous.fen_after == current.fen_before


# ---------------------------------------------------------------------------
# Derived state
# ---------------------------------------------------------------------------

def test_material_starts_level(opera):
    assert opera.events[0].state.material_balance == 0
    assert opera.events[0].state.white_material == 39


def test_material_balance_tracks_the_queen_sacrifice(opera):
    """Morphy is a queen down after 16...Nxb8, and mates anyway."""
    accepted = next(m for m in opera.moves if m.san == "Nxb8")
    assert accepted.state.material_balance < 0


def test_the_game_opens_in_the_opening_phase(opera):
    assert opera.moves[0].state.phase == GamePhase.OPENING


def test_phase_advances_as_material_leaves_the_board(opera):
    phases = {m.state.phase for m in opera.moves}
    assert GamePhase.OPENING in phases
    assert len(phases) > 1


# ---------------------------------------------------------------------------
# Clocks
# ---------------------------------------------------------------------------

def test_clock_annotations_are_read():
    moves = parse_pgn_text(CLOCK_PGN).moves
    assert moves[0].state.white_clock_s == pytest.approx(300.0)
    assert moves[1].state.black_clock_s == pytest.approx(295.0)


def test_the_move_itself_carries_the_movers_clock():
    """Not just the state snapshot -- the salience model reads it off the move."""
    moves = parse_pgn_text(CLOCK_PGN).moves
    assert [m.clock_s for m in moves] == [300.0, 295.0, 290.0, 280.0]


def test_think_time_accounts_for_the_increment():
    """300+3: spending 10s of clock means 13s of thought."""
    moves = parse_pgn_text(CLOCK_PGN).moves
    assert moves[2].think_time_s == pytest.approx(13.0)
    assert moves[3].think_time_s == pytest.approx(18.0)


def test_first_move_of_each_side_has_no_think_time():
    moves = parse_pgn_text(CLOCK_PGN).moves
    assert moves[0].think_time_s is None
    assert moves[1].think_time_s is None


def test_games_without_clocks_parse_cleanly(opera):
    assert all(m.clock_s is None for m in opera.moves)


# ---------------------------------------------------------------------------
# Special moves
# ---------------------------------------------------------------------------

def test_en_passant_is_flagged_and_credits_a_pawn():
    capture = parse_pgn_text(EN_PASSANT_PGN).moves[-1]
    assert capture.is_en_passant
    assert capture.is_capture
    assert capture.captured_piece == "pawn"


def test_promotion_is_flagged_with_the_new_piece():
    promotion = parse_pgn_text(PROMOTION_PGN).moves[-1]
    assert promotion.is_promotion
    assert promotion.promotion_piece == "queen"


# ---------------------------------------------------------------------------
# Multi-game files and remote ids
# ---------------------------------------------------------------------------

def test_multi_game_pgn_yields_every_game():
    text = SAMPLE_PGN.read_text(encoding="utf-8")
    assert len(list(iter_games(text + "\n\n" + text))) == 2


def test_empty_pgn_raises():
    with pytest.raises(ValueError):
        parse_pgn_text("")


@pytest.mark.parametrize(
    "value",
    [
        "abcd1234",
        "https://lichess.org/abcd1234",
        "https://lichess.org/abcd1234/black",
        "https://lichess.org/abcd1234?foo=1",
        "lichess.org/abcd1234/",
    ],
)
def test_lichess_ids_are_extracted_from_any_url_form(value):
    assert _extract_lichess_id(value) == "abcd1234"
