"""
Tests for opening-theory detection.

Offline: the book is built from a small synthetic TSV in the same format as
the CC0 source, so nothing downloads.

The test that matters most is the one about gaps. The names database records
the terminal position of each line rather than every position along it, so a
game in the middle of mainstream theory can pass through unnamed positions.
Treating the first unnamed position as the departure announces that the Ruy
Lopez left theory on move four.
"""

from __future__ import annotations

import chess
import chess.pgn
import pytest

from voxplay.chessgame.models import OpeningReport
from voxplay.chessgame.openings import (
    OpeningBook,
    OpeningError,
    _index_from,
    _key,
    load_book,
    probe_opening,
)
from voxplay.chessgame.pgn_source import parse_game, parse_pgn_text

#: Same shape as the real a.tsv..e.tsv: eco, name, move sequence.
TSV = "\n".join([
    "eco\tname\tpgn",
    "B00\tKing's Pawn Game\t1. e4",
    "C20\tKing's Pawn Game\t1. e4 e5",
    "C40\tKing's Knight Opening\t1. e4 e5 2. Nf3",
    "C41\tPhilidor Defense\t1. e4 e5 2. Nf3 d6",
    "C60\tRuy Lopez\t1. e4 e5 2. Nf3 Nc6 3. Bb5",
    # Deliberate gap: nothing names the positions after 3...a6 or 4. Ba4,
    # exactly as the real database behaves.
    "C78\tRuy Lopez: Morphy Defense\t1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O",
    "D02\tQueen's Pawn Game\t1. d4 d5 2. Nf3",
])


@pytest.fixture(scope="module")
def book():
    return OpeningBook(_index_from([TSV]))


def game_from(sans):
    game = chess.pgn.Game()
    node, board = game, chess.Board()
    for san in sans:
        move = board.parse_san(san)
        node = node.add_variation(move)
        board.push(move)
    game.headers["Result"] = "*"
    return parse_game(game)


# ---------------------------------------------------------------------------
# Building the index
# ---------------------------------------------------------------------------

def test_every_named_line_is_indexed(book):
    assert len(book) == 7


def test_a_position_resolves_to_its_name(book):
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "d6"):
        board.push_san(san)
    assert book.lookup(board)[:2] == ("C41", "Philidor Defense")


def test_transpositions_resolve_to_the_same_opening(book):
    """
    The reason the index is keyed on position rather than move order. A
    sequence comparison calls a transposed Queen's Pawn Game a novelty.
    """
    direct, transposed = chess.Board(), chess.Board()
    for san in ("d4", "d5", "Nf3"):
        direct.push_san(san)
    for san in ("Nf3", "d5", "d4"):
        transposed.push_san(san)
    assert _key(direct) == _key(transposed)
    assert book.lookup(transposed)[1] == "Queen's Pawn Game"


def test_an_unknown_position_resolves_to_nothing(book):
    board = chess.Board()
    board.push_san("h4")
    assert book.lookup(board) is None


def test_a_malformed_line_is_skipped_not_fatal():
    index = _index_from(["eco\tname\tpgn\nX00\tBroken\t1. zz9 qq8\nB00\tFine\t1. e4"])
    assert len(index) == 1


def test_a_source_with_no_usable_lines_indexes_to_nothing():
    assert _index_from(["eco\tname\tpgn\n"]) == {}


def test_a_book_that_parses_to_nothing_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("voxplay.chessgame.openings._download", lambda *a, **k: [""])
    with pytest.raises(OpeningError, match="parsed to nothing"):
        load_book(tmp_path, rebuild=True)


def test_a_download_failure_names_the_free_source(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr("voxplay.chessgame.openings._download", boom)
    with pytest.raises(OpeningError, match="needs no token"):
        load_book(tmp_path, rebuild=True)


def test_the_built_index_is_cached_so_a_demo_pays_once(tmp_path, monkeypatch):
    calls = []

    def once(*args, **kwargs):
        calls.append(1)
        return [TSV]

    monkeypatch.setattr("voxplay.chessgame.openings._download", once)
    first = load_book(tmp_path, rebuild=True)
    second = load_book(tmp_path)
    assert len(calls) == 1
    assert len(first) == len(second)


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def test_the_deepest_named_position_wins(book):
    report = probe_opening(game_from(["e4", "e5", "Nf3", "d6"]), book)
    assert (report.eco, report.name) == ("C41", "Philidor Defense")


def test_a_departure_is_reported_after_the_last_named_position(book):
    report = probe_opening(game_from(["e4", "e5", "Nf3", "d6", "d4", "Bg4"]), book)
    assert report.book_plies == 4
    assert report.exit_ply == 5
    assert report.exit_san == "d4"


def test_gaps_in_the_book_do_not_count_as_leaving_it(book):
    """
    Regression. Nothing names the positions after a6 or Ba4, but the line is
    squarely in theory and is named again after O-O. Taking the first unnamed
    position would announce that the Ruy Lopez left theory on move four.
    """
    moves = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O"]
    report = probe_opening(game_from(moves), book)
    assert report.name == "Ruy Lopez: Morphy Defense"
    assert report.book_plies == 9
    assert report.exit_ply is None


def test_a_game_still_in_book_reports_no_departure(book):
    report = probe_opening(game_from(["e4", "e5", "Nf3", "d6"]), book)
    assert report.exit_ply is None
    assert "stayed" in report.describe() or "followed theory" in report.describe()


def test_a_game_outside_the_book_entirely(book):
    report = probe_opening(game_from(["h4", "h5", "a4"]), book)
    assert report.name is None
    assert report.exit_ply is None


def test_an_empty_game_is_handled(book):
    assert probe_opening(parse_pgn_text('[Result "*"]\n\n*'), book).name is None


def test_only_the_opening_window_is_examined(book):
    long_game = game_from(["e4", "e5", "Nf3", "d6", "d4", "Bg4", "dxe5", "Bxf3"])
    assert probe_opening(long_game, book, max_plies=4).exit_ply is None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_the_move_number_is_derived_from_the_ply():
    assert OpeningReport(exit_ply=5).exit_move_number == 3     # White's 3rd
    assert OpeningReport(exit_ply=6).exit_move_number == 3     # Black's 3rd


def test_a_missing_opening_says_so():
    assert "not in the book" in OpeningReport().describe()


def test_the_description_names_the_departure():
    report = OpeningReport(name="Philidor Defense", exit_ply=6, exit_san="Bg4")
    text = report.describe()
    assert "Philidor Defense" in text and "move 3" in text and "Bg4" in text


def test_the_description_is_ascii():
    """The project's terminal is CP1252."""
    OpeningReport(name="Reti Opening", exit_ply=4, exit_san="d5").describe().encode("ascii")
