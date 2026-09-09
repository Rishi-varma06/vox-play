"""
Tests for notation -> spoken English.

A closed grammar with exact expected outputs, which is precisely why this is
a function and not a prompt. Mispronounced notation is the first thing a
listener notices.
"""

from __future__ import annotations

import re

import chess
import pytest

from voxplay.chessgame.models import Color
from voxplay.chessgame.pgn_source import parse_pgn_text
from voxplay.chessgame.speech import (
    speak_duration,
    speak_move,
    speak_san,
    speak_square,
    speak_squares_in,
)


def move_for(pgn: str):
    """The last move of a short PGN, as a MoveEvent."""
    return parse_pgn_text(pgn).moves[-1]


# ---------------------------------------------------------------------------
# Squares
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "square,expected",
    [("e4", "e four"), ("a1", "a one"), ("h8", "h eight"), ("d7", "d seven")],
)
def test_squares_are_spelled_out(square, expected):
    assert speak_square(square) == expected


def test_every_square_on_the_board_has_a_spoken_form():
    for name in chess.SQUARE_NAMES:
        spoken = speak_square(name)
        assert spoken != name and " " in spoken


def test_nonsense_input_is_left_alone():
    assert speak_square("z9") == "z9"
    assert speak_square("") == ""


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------

def test_a_quiet_pawn_move():
    assert speak_move(move_for('[Result "*"]\n\n1. e4 *')) == "pawn to e four"


def test_a_piece_move():
    assert speak_move(move_for('[Result "*"]\n\n1. Nf3 *')) == "knight to f three"


def test_a_capture_names_what_was_taken():
    pgn = '[Result "*"]\n\n1. e4 d5 2. exd5 *'
    assert speak_move(move_for(pgn)) == "pawn takes the pawn on d five"


def test_kingside_castling():
    pgn = '[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O *'
    assert speak_move(move_for(pgn)) == "castles kingside"


def test_queenside_castling():
    pgn = ('[Result "*"]\n\n1. d4 d5 2. Nc3 Nc6 3. Bf4 Bf5 4. Qd2 Qd7 5. O-O-O *')
    assert speak_move(move_for(pgn)) == "castles queenside"


def test_check_is_announced():
    """A check that is not mate -- Black answers with knight takes e five."""
    pgn = '[Result "*"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 g6 4. Qxe5+ *'
    assert speak_move(move_for(pgn)).endswith(", check")


def test_checkmate_is_announced():
    pgn = '[Result "*"]\n\n1. f3 e5 2. g4 Qh4# *'
    assert speak_move(move_for(pgn)).endswith(", checkmate")


def test_promotion_names_the_new_piece():
    pgn = ('[FEN "8/P7/8/8/8/8/8/K5k1 w - - 0 1"]\n[SetUp "1"]\n[Result "*"]\n\n1. a8=Q *')
    assert speak_move(move_for(pgn)) == "pawn to a eight, promoting to a queen"


def test_en_passant_is_named():
    pgn = '[Result "*"]\n\n1. e4 a6 2. e5 d5 3. exd6 *'
    assert "en passant" in speak_move(move_for(pgn))


def test_move_numbers_use_dots_for_white_and_ellipsis_for_black():
    white = move_for('[Result "*"]\n\n1. e4 *')
    black = move_for('[Result "*"]\n\n1. e4 e5 *')
    assert speak_move(white, with_number=True).startswith("1. ")
    assert speak_move(black, with_number=True).startswith("1... ")


#: Algebraic notation in any form. "six" contains an x, so a bare character
#: check is useless -- this matches piece-and-square, bare squares, and the
#: check/mate/promotion suffixes.
NOTATION = re.compile(r"\b[KQRBN][a-h]?[1-8]?x?[a-h][1-8]\b|\b[a-h][1-8]\b|[+#=]")


def test_no_move_ever_speaks_raw_notation():
    """The whole point: nothing that reaches speech may contain notation."""
    game = parse_pgn_text(
        '[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Bxc6 dxc6 5. O-O *'
    )
    for move in game.moves:
        spoken = speak_move(move)
        assert not NOTATION.search(spoken), f"notation leaked: {spoken!r}"


# ---------------------------------------------------------------------------
# Bare SAN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "san,expected",
    [
        ("Nf3", "knight to f three"),
        ("e4", "pawn to e four"),
        ("O-O", "castles kingside"),
        ("O-O-O", "castles queenside"),
        ("Qxh7", "queen takes on h seven"),
    ],
)
def test_bare_san_is_spoken(san, expected):
    assert speak_san(san) == expected


def test_bare_san_carries_check_and_mate():
    assert speak_san("Qb8+").endswith(", check")
    assert speak_san("Rd8#").endswith(", checkmate")


def test_bare_san_handles_promotion():
    assert "promoting to a queen" in speak_san("a8=Q")


def test_empty_san_is_empty():
    assert speak_san("") == ""


# ---------------------------------------------------------------------------
# Squares embedded in prose
# ---------------------------------------------------------------------------

def test_squares_inside_a_sentence_are_converted():
    """Motif descriptions carry raw squares; they must not reach speech."""
    text = "the bishop on d7 forks the queen on e6, king on e8"
    assert speak_squares_in(text) == (
        "the bishop on d seven forks the queen on e six, king on e eight"
    )


def test_ordinary_words_are_untouched():
    assert speak_squares_in("a fine game with no squares") == (
        "a fine game with no squares"
    )


# ---------------------------------------------------------------------------
# Durations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seconds,expected",
    [
        (1, "1 second"),
        (45, "45 seconds"),
        (60, "1 minute"),
        (120, "2 minutes"),
        (95, "1 minute 35 seconds"),
    ],
)
def test_durations_are_spoken(seconds, expected):
    assert speak_duration(seconds) == expected


def test_a_missing_clock_says_nothing():
    assert speak_duration(None) == ""
