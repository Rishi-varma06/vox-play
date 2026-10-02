"""
Opening theory: naming the line, and finding where the players left it.

"They were out of book on move seven" is one of the most natural things a
commentator says, and the system could not say it. This closes that gap.

Why a position index rather than a move list
--------------------------------------------
The source data is a list of move sequences, and the obvious implementation
compares the game's moves against them prefix by prefix. That misses every
transposition: a game reaching a Queen's Gambit position by a different move
order is in exactly the same opening, and a sequence comparison calls it a
novelty on move three. Replaying each line and indexing the resulting
POSITION costs a few seconds once and gets transpositions right for free.

What this can and cannot say
----------------------------
It names the deepest known position and the move that left it. It cannot say
how often a move has been played, because Lichess's opening explorer now
requires an API token (it answers HTTP 401 unauthenticated), so popularity
figures like "played in 0.3% of games at this level" are out of reach without
one. The names database is CC0 and needs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import chess
from .models import OpeningReport
from .pgn_source import ParsedGame

#: lichess-org/chess-openings, CC0. One file per ECO letter, about 390KB in
#: total, covering roughly 3,500 named openings.
BOOK_URL = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/{part}.tsv"
BOOK_PARTS = ("a", "b", "c", "d", "e")

USER_AGENT = "voxplay/0.1 (university capstone; opening book)"

#: Lichess's opening explorer, which would add popularity figures. Requires
#: an API token since 2026; kept here so the limitation is documented in the
#: code rather than only in prose.
EXPLORER_URL = "https://explorer.lichess.ovh/masters"


class OpeningError(RuntimeError):
    """Raised when the opening book cannot be loaded."""


# ---------------------------------------------------------------------------
# Building the book
# ---------------------------------------------------------------------------

def _download(cache_dir: Path, progress: bool = False) -> List[str]:
    """Fetch the TSV parts, caching each to disk."""
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    texts: List[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for part in BOOK_PARTS:
        path = cache_dir / f"{part}.tsv"
        if path.exists() and path.stat().st_size > 0:
            texts.append(path.read_text(encoding="utf-8"))
            continue
        if progress:
            print(f"      downloading {part}.tsv")
        response = session.get(BOOK_URL.format(part=part), timeout=30)
        response.raise_for_status()
        path.write_text(response.text, encoding="utf-8")
        texts.append(response.text)
    return texts


def _index_from(texts: Iterable[str]) -> Dict[str, Tuple[str, str, int]]:
    """
    Board position -> (eco, name, depth in half-moves).

    Keyed on the board layout plus side to move, which is what makes
    transpositions work. Where two lines reach the same position, the deeper
    one wins: it is the more specific name for that position.
    """
    index: Dict[str, Tuple[str, str, int]] = {}
    for text in texts:
        for line in text.splitlines():
            if not line.strip() or line.startswith("eco\t"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            eco, name, moves = parts[0], parts[1], parts[2]

            board = chess.Board()
            depth = 0
            try:
                for token in moves.split():
                    if token.endswith("."):
                        continue
                    board.push_san(token)
                    depth += 1
            except ValueError:
                continue

            key = _key(board)
            existing = index.get(key)
            if existing is None or depth > existing[2]:
                index[key] = (eco, name, depth)
    return index


def _key(board: chess.Board) -> str:
    """Position identity for book lookup: layout plus side to move."""
    return f"{board.board_fen()} {'w' if board.turn else 'b'}"


class OpeningBook:
    """Named opening positions, indexed for transposition-safe lookup."""

    def __init__(self, index: Dict[str, Tuple[str, str, int]]) -> None:
        self.index = index

    def __len__(self) -> int:
        return len(self.index)

    def lookup(self, board: chess.Board) -> Optional[Tuple[str, str, int]]:
        return self.index.get(_key(board))


def load_book(
    cache_dir: Path | str = "data/openings",
    rebuild: bool = False,
    progress: bool = False,
) -> OpeningBook:
    """
    Load the opening book, building and caching the position index once.

    The built index is cached as JSON because replaying three and a half
    thousand lines takes a few seconds, and a demo should not pay that twice.
    """
    cache_dir = Path(cache_dir)
    built = cache_dir / "positions.json"

    if built.exists() and not rebuild:
        try:
            raw = json.loads(built.read_text(encoding="utf-8"))
            return OpeningBook({k: tuple(v) for k, v in raw.items()})
        except Exception:  # noqa: BLE001 - a bad cache is rebuilt, not fatal
            pass

    try:
        texts = _download(cache_dir, progress=progress)
    except Exception as exc:  # noqa: BLE001 - surfaced as OpeningError
        raise OpeningError(
            f"could not fetch the opening book: {exc}. It is CC0 at "
            f"{BOOK_URL.format(part='a')} and needs no token."
        ) from exc

    if progress:
        print("      indexing positions ...")
    index = _index_from(texts)
    if not index:
        raise OpeningError("the opening book parsed to nothing")

    cache_dir.mkdir(parents=True, exist_ok=True)
    built.write_text(
        json.dumps({k: list(v) for k, v in index.items()}), encoding="utf-8"
    )
    return OpeningBook(index)


# ---------------------------------------------------------------------------
# Probing a game
# ---------------------------------------------------------------------------

#: Openings do not run past about move 15 in the names database, and querying
#: beyond that only finds positions nobody named.
MAX_BOOK_PLIES = 30


def probe_opening(
    game: ParsedGame, book: OpeningBook, max_plies: int = MAX_BOOK_PLIES
) -> OpeningReport:
    """
    Find the deepest named position, and the move that left it behind.

    The departure is the move after the LAST named position, not the first
    unnamed one, and the difference matters. The database names the terminal
    position of each line rather than every position along it, so a line in
    the middle of mainstream theory can have gaps: in the Ruy Lopez, the
    positions after Ba4 and Nf6 are unnamed while the ones after O-O and Be7
    are named again. Taking the first miss would announce that the Ruy Lopez
    left theory on move four.

    What this measures is therefore "the last position anyone bothered to
    name", not "the last position ever played". Those are close enough for
    commentary and not the same thing.
    """
    report = OpeningReport()
    if not game.moves:
        return report

    board = chess.Board(game.moves[0].fen_before)
    opening = book.lookup(board)
    deepest_index = -1

    examined = game.moves[:max_plies]
    for index, move in enumerate(examined):
        board.push(chess.Move.from_uci(move.uci))
        found = book.lookup(board)
        if found is None:
            continue
        deepest_index = index
        report.book_plies = move.ply
        if opening is None or found[2] >= opening[2]:
            opening = found

    if opening is not None:
        report.eco, report.name = opening[0], opening[1]

    # The move after the deepest named position is the departure. If that is
    # the last move we looked at, the game was still in book when the window
    # ran out, and there is nothing to report.
    departure = deepest_index + 1
    if report.name is not None and departure < len(examined):
        leaving = examined[departure]
        report.exit_ply = leaving.ply
        report.exit_san = leaving.san
        report.exit_color = leaving.color.value

    return report
