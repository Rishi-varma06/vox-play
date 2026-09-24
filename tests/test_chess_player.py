"""
Tests for the standalone HTML player.

The page is one file that has to work when double-clicked, so the things
worth pinning are structural: the payload describes every position and cue,
the audio is actually embedded, and nothing in the data can break out of the
script tag it is embedded in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from voxplay.chessgame import beats as B
from voxplay.chessgame.audio import narrate
from voxplay.chessgame.engine import MockAnalysisEngine, analyse_game
from voxplay.chessgame.pgn_source import load_pgn, parse_pgn_text
from voxplay.chessgame.player import PlayerError, TEMPLATE, build_player
from voxplay.chessgame.script import TemplateGenerator

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"


@pytest.fixture(scope="module")
def game():
    return analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())


@pytest.fixture(scope="module")
def sheet(game):
    return B.select_beats(game)


@pytest.fixture
def narrated(game, sheet, tmp_path):
    script = TemplateGenerator().generate(game, sheet)
    return narrate(script, tmp_path, provider="mock")


def payload_of(html: str) -> dict:
    match = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S
    )
    assert match, "payload script block not found"
    return json.loads(match.group(1).replace("\\u003c", "<"))


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def test_the_template_ships_with_the_package():
    assert TEMPLATE.exists()


def test_a_page_is_written(game, sheet, narrated, tmp_path):
    page = build_player(game, sheet, narrated, tmp_path / "player.html")
    assert page.exists() and page.stat().st_size > 0


def test_no_placeholders_survive(game, sheet, narrated, tmp_path):
    html = build_player(game, sheet, narrated, tmp_path / "p.html").read_text(encoding="utf-8")
    assert "__PAYLOAD__" not in html
    assert "__AUDIO_SRC__" not in html
    assert "__HEADLINE__" not in html
    assert "__TITLE__" not in html


def test_a_missing_track_is_refused(game, sheet, narrated, tmp_path):
    broken = narrated.model_copy(update={"track_path": str(tmp_path / "gone.wav")})
    with pytest.raises(PlayerError, match="not found"):
        build_player(game, sheet, broken, tmp_path / "p.html")


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def test_every_position_is_in_the_payload(game, sheet, narrated, tmp_path):
    """One entry per ply plus the starting position, so ply numbers index it."""
    data = payload_of(build_player(game, sheet, narrated, tmp_path / "p.html").read_text(encoding="utf-8"))
    assert len(data["plies"]) == game.total_plies + 1
    assert data["plies"][0]["san"] is None
    assert data["plies"][1]["san"] == game.moves[0].san


def test_ply_entries_carry_what_the_board_needs(game, sheet, narrated, tmp_path):
    data = payload_of(build_player(game, sheet, narrated, tmp_path / "p.html").read_text(encoding="utf-8"))
    node = data["plies"][1]
    for field in ("fen", "from", "to", "moveNumber", "color", "winPct"):
        assert field in node, f"missing {field}"


def test_every_cue_is_in_the_payload(game, sheet, narrated, tmp_path):
    data = payload_of(build_player(game, sheet, narrated, tmp_path / "p.html").read_text(encoding="utf-8"))
    assert len(data["cues"]) == len(narrated.cues)
    for cue, source in zip(data["cues"], narrated.cues):
        assert cue["startMs"] == source.start_ms
        assert cue["plies"] == source.plies


def test_cue_plies_index_into_the_ply_list(game, sheet, narrated, tmp_path):
    """The player looks positions up by ply number; out of range breaks it."""
    data = payload_of(build_player(game, sheet, narrated, tmp_path / "p.html").read_text(encoding="utf-8"))
    count = len(data["plies"])
    for cue in data["cues"]:
        assert all(0 <= ply < count for ply in cue["plies"])


def test_a_checking_move_records_the_king_square(tmp_path):
    from voxplay.chessgame.player import _check_square

    checking = parse_pgn_text('[Result "*"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 g6 4. Qxe5+ *')
    assert _check_square(checking.moves[-1].fen_after) == "e8"


def test_a_quiet_move_records_no_check():
    from voxplay.chessgame.player import _check_square

    assert _check_square(parse_pgn_text('[Result "*"]\n\n1. e4 *').moves[-1].fen_after) is None


# ---------------------------------------------------------------------------
# Self-containment
# ---------------------------------------------------------------------------

def test_audio_is_embedded_by_default(game, sheet, narrated, tmp_path):
    """One file you can email; a relative path breaks the moment it is moved."""
    html = build_player(game, sheet, narrated, tmp_path / "p.html").read_text(encoding="utf-8")
    assert 'src="data:audio/wav;base64,' in html


def test_audio_can_be_left_external(game, sheet, narrated, tmp_path):
    html = build_player(
        game, sheet, narrated, tmp_path / "p.html", inline_audio=False
    ).read_text(encoding="utf-8")
    assert "data:audio/wav" not in html
    assert ".wav" in html


def test_the_payload_cannot_break_out_of_its_script_tag(game, sheet, narrated, tmp_path):
    """
    Commentary text is arbitrary; a literal "</script>" in it would end the
    block early and leave the rest of the payload loose in the document.
    """
    poisoned = narrated.model_copy()
    poisoned.cues[0].text = 'before </script><script>window.__pwned=1</script> after'
    html = build_player(game, sheet, poisoned, tmp_path / "p.html").read_text(encoding="utf-8")
    body = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S
    ).group(1)
    assert "</script>" not in body
    assert "\\u003c/script>" in body
    assert payload_of(html)["cues"][0]["text"].startswith("before </script>")
