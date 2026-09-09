"""
Chess notation -> spoken English.

Deliberately rule-based. Asking a model to read "Qxh7#" aloud works most of
the time and then says "queen x h seven hash" on the move that matters, and
mispronounced notation is the first thing a listener notices. It is a solved
problem with a closed grammar, so it gets a function rather than a prompt.

Nothing here parses SAN. MoveEvent already carries the piece, both squares,
and every flag the sentence needs, so the spoken form is built from
structured fields -- there is no string to misparse.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import Color, MoveEvent

# ---------------------------------------------------------------------------
# Squares
# ---------------------------------------------------------------------------

#: Rank digits as words. "e4" read as "e four" survives every TTS backend;
#: "e4" left as digits is read as "e-four" by some and "e forty" by others.
_RANK_WORDS = {
    "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight",
}

_FILE_WORDS = {
    "a": "a", "b": "b", "c": "c", "d": "d",
    "e": "e", "f": "f", "g": "g", "h": "h",
}


def speak_square(square: str) -> str:
    """'e4' -> 'e four'. Unrecognised input is returned unchanged."""
    if len(square) != 2:
        return square
    file_name, rank = square[0].lower(), square[1]
    if file_name not in _FILE_WORDS or rank not in _RANK_WORDS:
        return square
    return f"{_FILE_WORDS[file_name]} {_RANK_WORDS[rank]}"


#: Standalone algebraic squares embedded in prose, e.g. the square names the
#: motif detectors put in their descriptions.
_SQUARE_IN_TEXT = re.compile(r"\b([a-h][1-8])\b")


def speak_squares_in(text: str) -> str:
    """
    Replace bare square names in prose with their spoken form.

    Motif descriptions are written as data ("the bishop on d7 forks..."), and
    data is where square names belong. This is the one place they have to be
    converted, because that text can reach the speech backend verbatim.
    """
    return _SQUARE_IN_TEXT.sub(lambda m: speak_square(m.group(1)), text)


def speak_colour(colour: Color) -> str:
    return "White" if colour == Color.WHITE else "Black"


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------

def speak_move(move: MoveEvent, *, with_number: bool = False) -> str:
    """
    A MoveEvent as a narrator would say it.

    Examples:
        e4      -> "pawn to e four"
        Nf3     -> "knight to f three"
        Bxf3    -> "bishop takes the knight on f three"
        O-O-O   -> "castles queenside"
        a8=Q    -> "pawn to a eight, promoting to a queen"
        Qb8+    -> "queen to b eight, check"
        Rd8#    -> "rook to d eight, checkmate"
    """
    if move.is_castle:
        side = "kingside" if move.to_square[0] == "g" else "queenside"
        body = f"castles {side}"
    elif move.is_capture:
        target = speak_square(move.to_square)
        if move.captured_piece:
            body = f"{move.piece} takes the {move.captured_piece} on {target}"
        else:
            body = f"{move.piece} takes on {target}"
        if move.is_en_passant:
            body += ", en passant"
    else:
        body = f"{move.piece} to {speak_square(move.to_square)}"

    if move.is_promotion and not move.is_castle:
        body += f", promoting to a {move.promotion_piece or 'queen'}"

    if move.is_checkmate:
        body += ", checkmate"
    elif move.is_check:
        body += ", check"

    if with_number:
        dots = "." if move.color == Color.WHITE else "..."
        return f"{move.move_number}{dots} {body}"
    return body


def speak_san(san: str, *, colour: Optional[Color] = None) -> str:
    """
    Spoken form of a bare SAN string, for engine suggestions.

    Less precise than speak_move because a SAN string alone does not say what
    was captured. Used only for "the engine preferred ...", where naming the
    destination square is enough.
    """
    if not san:
        return ""
    text = san.strip()

    suffix = ""
    if text.endswith("#"):
        suffix, text = ", checkmate", text[:-1]
    elif text.endswith("+"):
        suffix, text = ", check", text[:-1]

    if text in ("O-O", "0-0"):
        return "castles kingside" + suffix
    if text in ("O-O-O", "0-0-0"):
        return "castles queenside" + suffix

    promotion = ""
    if "=" in text:
        text, _, promoted = text.partition("=")
        promotion = f", promoting to a {_PIECE_LETTERS.get(promoted[:1], 'queen')}"

    piece = _PIECE_LETTERS.get(text[:1], "pawn")
    if text[:1] in _PIECE_LETTERS:
        text = text[1:]

    takes = "x" in text
    destination = text.split("x")[-1][-2:] if takes else text[-2:]

    verb = "takes on" if takes else "to"
    return f"{piece} {verb} {speak_square(destination)}{promotion}{suffix}"


_PIECE_LETTERS = {
    "K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight",
}


# ---------------------------------------------------------------------------
# Clocks
# ---------------------------------------------------------------------------

def speak_duration(seconds: Optional[float]) -> str:
    """Seconds as a narrator would say them. Empty string for None."""
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes, rest = divmod(seconds, 60)
    if not rest:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''} {rest} seconds"
