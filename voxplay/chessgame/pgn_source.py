"""
PGN -> chess event stream.

This is the whole reason the project moved to chess. The cricket domain needs
a human scoring ball by ball (or a vision model nobody has built) before any
commentary is possible. Chess hands you a complete, unambiguous event stream
in a text file that both major platforms give away for free.

Nothing in this module talks to an engine. It produces the structural facts --
what was played, by whom, what was captured, how long they thought -- and
leaves every value judgement to the analysis layer. Parsing stays engine-free
so it can be tested exhaustively and run in milliseconds.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Iterator, List, Optional

import chess
import chess.pgn
from pydantic import BaseModel, Field

from .models import (
    ChessEvent,
    OpeningReport,
    Color,
    GameEndEvent,
    GameMeta,
    GamePhase,
    GameResult,
    GameStartEvent,
    GameState,
    MoveEvent,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Standard piece values, in pawns. Kings excluded (they never come off).
PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}

#: Spoken piece names. The TTS layer must never be handed "N" or "Nf3".
PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}

#: Non-pawn material on the board at the start, both sides combined.
_START_NON_PAWN = 62

#: Phase boundaries, in combined non-pawn material.
_OPENING_MIN_MATERIAL = 52
_OPENING_MAX_MOVE = 12
_ENDGAME_MAX_MATERIAL = 20

_CLOCK_RE = re.compile(r"\[%clk\s+(\d+):(\d+):(\d+(?:\.\d+)?)\]")
_TIME_CONTROL_RE = re.compile(r"^(\d+)(?:\+(\d+))?")


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

class ParsedGame(BaseModel):
    """A single game: metadata plus its full event stream."""
    meta: GameMeta
    events: List[ChessEvent] = Field(default_factory=list)
    #: Filled in by openings.probe_opening when the book is consulted.
    opening_report: Optional[OpeningReport] = None

    @property
    def moves(self) -> List[MoveEvent]:
        return [e for e in self.events if isinstance(e, MoveEvent)]

    @property
    def total_plies(self) -> int:
        return len(self.moves)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _material(board: chess.Board, color: chess.Color) -> int:
    """Total material for one side, in pawns, kings excluded."""
    return sum(
        len(board.pieces(piece_type, color)) * value
        for piece_type, value in PIECE_VALUES.items()
    )


def _non_pawn_material(board: chess.Board) -> int:
    """Combined non-pawn material, used for phase detection."""
    total = 0
    for piece_type, value in PIECE_VALUES.items():
        if piece_type == chess.PAWN:
            continue
        total += (
            len(board.pieces(piece_type, chess.WHITE))
            + len(board.pieces(piece_type, chess.BLACK))
        ) * value
    return total


def _detect_phase(board: chess.Board, move_number: int) -> GamePhase:
    """
    Coarse phase label from material and move number.

    Deliberately a heuristic rather than anything clever: the phase only
    steers narration ("still in the opening", "deep into the endgame"), so
    being roughly right everywhere beats being exactly right sometimes.
    """
    non_pawn = _non_pawn_material(board)
    if non_pawn <= _ENDGAME_MAX_MATERIAL:
        return GamePhase.ENDGAME
    if move_number <= _OPENING_MAX_MOVE and non_pawn >= _OPENING_MIN_MATERIAL:
        return GamePhase.OPENING
    return GamePhase.MIDDLEGAME


def _parse_clock(comment: str) -> Optional[float]:
    """Seconds remaining from a [%clk H:MM:SS] annotation, if present."""
    match = _CLOCK_RE.search(comment or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_increment(time_control: Optional[str]) -> float:
    """Increment in seconds from a PGN TimeControl header such as '300+3'."""
    if not time_control:
        return 0.0
    match = _TIME_CONTROL_RE.match(time_control.strip())
    if not match or match.group(2) is None:
        return 0.0
    return float(match.group(2))


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _clean(value: Optional[str]) -> Optional[str]:
    """
    Normalise a PGN header, returning None for the various "unknown" spellings.

    PGN placeholders are punctuation-only -- '?', '-', and dates written as
    '????.??.??'. Testing for the absence of any alphanumeric character
    catches all of them, including forms we have not seen yet.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not any(char.isalnum() for char in text):
        return None
    return text


def _derive_game_id(headers: chess.pgn.Headers) -> str:
    """
    Stable id for a game. Prefers the platform's own id from the Site URL,
    which makes cached engine analysis reusable across runs.
    """
    site = _clean(headers.get("Site"))
    if site and "/" in site:
        tail = site.rstrip("/").rsplit("/", 1)[-1]
        if tail:
            return tail
    parts = [
        _clean(headers.get("White")) or "white",
        _clean(headers.get("Black")) or "black",
        _clean(headers.get("Date")) or "nodate",
    ]
    slug = "_".join(parts)
    return re.sub(r"[^A-Za-z0-9_.-]", "-", slug)


def _detect_source(headers: chess.pgn.Headers) -> str:
    site = (_clean(headers.get("Site")) or "").lower()
    if "lichess" in site:
        return "lichess"
    if "chess.com" in site:
        return "chesscom"
    return "pgn"


def _build_meta(headers: chess.pgn.Headers, game_id: Optional[str]) -> GameMeta:
    try:
        result = GameResult(_clean(headers.get("Result")) or "*")
    except ValueError:
        result = GameResult.UNKNOWN

    return GameMeta(
        game_id=game_id or _derive_game_id(headers),
        white=_clean(headers.get("White")),
        black=_clean(headers.get("Black")),
        white_elo=_int_or_none(headers.get("WhiteElo")),
        black_elo=_int_or_none(headers.get("BlackElo")),
        result=result,
        eco=_clean(headers.get("ECO")),
        opening=_clean(headers.get("Opening")),
        event=_clean(headers.get("Event")),
        site=_clean(headers.get("Site")),
        date=_clean(headers.get("Date")) or _clean(headers.get("UTCDate")),
        time_control=_clean(headers.get("TimeControl")),
        termination=_clean(headers.get("Termination")),
        source=_detect_source(headers),
    )


def _snapshot(board: chess.Board, ply: int) -> GameState:
    """GameState for the position currently on `board`."""
    white_material = _material(board, chess.WHITE)
    black_material = _material(board, chess.BLACK)
    move_number = board.fullmove_number

    return GameState(
        fen=board.fen(),
        ply=ply,
        move_number=move_number,
        side_to_move=Color.WHITE if board.turn == chess.WHITE else Color.BLACK,
        white_material=white_material,
        black_material=black_material,
        material_balance=white_material - black_material,
        phase=_detect_phase(board, move_number),
        is_check=board.is_check(),
        is_checkmate=board.is_checkmate(),
        is_stalemate=board.is_stalemate(),
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_game(game: chess.pgn.Game, game_id: Optional[str] = None) -> ParsedGame:
    """
    Walk a python-chess Game's mainline into a Voxplay event stream.

    Variations and annotation glyphs in the source PGN are ignored on purpose:
    the engine produces our verdicts, so a previous annotator's opinion would
    only be a second, conflicting source of truth.
    """
    meta = _build_meta(game.headers, game_id)
    increment = _parse_increment(meta.time_control)

    board = game.board()
    events: List[ChessEvent] = [
        GameStartEvent(game_id=meta.game_id, meta=meta, state=_snapshot(board, ply=0))
    ]

    # Most recent clock reading per side, for think-time deltas.
    last_clock = {Color.WHITE: None, Color.BLACK: None}
    ply = 0

    for node in game.mainline():
        move = node.move
        if move is None:
            continue

        ply += 1
        mover = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
        move_number = board.fullmove_number
        fen_before = board.fen()

        # Everything that needs the pre-move board, computed before pushing.
        san = board.san(move)
        is_capture = board.is_capture(move)
        is_en_passant = board.is_en_passant(move)
        is_castle = board.is_castling(move)

        moved_piece = board.piece_at(move.from_square)
        piece_name = PIECE_NAMES.get(moved_piece.piece_type, "piece") if moved_piece else "piece"

        captured_piece = None
        if is_en_passant:
            captured_piece = "pawn"
        elif is_capture:
            target = board.piece_at(move.to_square)
            if target is not None:
                captured_piece = PIECE_NAMES.get(target.piece_type)

        board.push(move)

        clock_s = _parse_clock(node.comment)
        think_time_s = None
        previous = last_clock[mover]
        if clock_s is not None and previous is not None:
            # Time spent = what they had, minus what they have, plus whatever
            # the increment gave back after the move.
            think_time_s = max(0.0, previous - clock_s + increment)
        if clock_s is not None:
            last_clock[mover] = clock_s

        state = _snapshot(board, ply=ply)
        if clock_s is not None:
            if mover == Color.WHITE:
                state.white_clock_s = clock_s
                state.black_clock_s = last_clock[Color.BLACK]
            else:
                state.black_clock_s = clock_s
                state.white_clock_s = last_clock[Color.WHITE]

        events.append(
            MoveEvent(
                game_id=meta.game_id,
                ply=ply,
                move_number=move_number,
                color=mover,
                san=san,
                uci=move.uci(),
                from_square=chess.square_name(move.from_square),
                to_square=chess.square_name(move.to_square),
                piece=piece_name,
                captured_piece=captured_piece,
                is_capture=is_capture,
                is_check=state.is_check,
                is_checkmate=state.is_checkmate,
                is_castle=is_castle,
                is_promotion=move.promotion is not None,
                promotion_piece=PIECE_NAMES.get(move.promotion) if move.promotion else None,
                is_en_passant=is_en_passant,
                fen_before=fen_before,
                fen_after=state.fen,
                state=state,
                clock_s=clock_s,
                think_time_s=think_time_s,
            )
        )

    events.append(
        GameEndEvent(
            game_id=meta.game_id,
            result=meta.result,
            termination=meta.termination,
            total_plies=ply,
            state=_snapshot(board, ply=ply),
        )
    )

    return ParsedGame(meta=meta, events=events)


def iter_games(pgn_text: str) -> Iterator[ParsedGame]:
    """Every game in a PGN string. Handles multi-game archive files."""
    stream = io.StringIO(pgn_text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            return
        yield parse_game(game)


def parse_pgn_text(pgn_text: str, game_id: Optional[str] = None) -> ParsedGame:
    """Parse the first game in a PGN string. Raises if there is none."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("no game found in PGN text")
    return parse_game(game, game_id=game_id)


def load_pgn(path: str | Path, game_id: Optional[str] = None) -> ParsedGame:
    """Parse the first game in a .pgn file."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_pgn_text(text, game_id=game_id)


# ---------------------------------------------------------------------------
# Remote sources
# ---------------------------------------------------------------------------

LICHESS_EXPORT_URL = "https://lichess.org/game/export/{game_id}"

#: Lichess asks API clients to identify themselves.
USER_AGENT = "voxplay/0.1 (university capstone; contact via repository)"


def _extract_lichess_id(url_or_id: str) -> str:
    """Accept a bare id, a game URL, or a URL with a colour/ply suffix."""
    text = url_or_id.strip().rstrip("/")
    if "lichess.org" in text:
        text = text.split("lichess.org/", 1)[1]
        text = text.split("?", 1)[0].split("#", 1)[0]
        text = text.split("/", 1)[0]
    # Full game ids are 8 chars; player-specific URLs append a colour suffix.
    return text[:8]


def fetch_lichess_pgn(url_or_id: str, timeout: float = 15.0) -> str:
    """
    Download one public game's PGN from Lichess.

    Clocks are requested explicitly because think time is one of the strongest
    commentary signals we have and most exporters drop it.
    """
    import requests

    game_id = _extract_lichess_id(url_or_id)
    response = requests.get(
        LICHESS_EXPORT_URL.format(game_id=game_id),
        headers={"Accept": "application/x-chess-pgn", "User-Agent": USER_AGENT},
        params={"clocks": "true", "evals": "false", "opening": "true"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def load_lichess(url_or_id: str, timeout: float = 15.0) -> ParsedGame:
    """Fetch and parse a public Lichess game."""
    game_id = _extract_lichess_id(url_or_id)
    return parse_pgn_text(fetch_lichess_pgn(url_or_id, timeout=timeout), game_id=game_id)
