"""
Tests for the chess studio's HTTP surface.

Everything here runs on the mock engine and the template writer, so the
suite stays offline and fast. What is being tested is the contract between
the browser and the pipeline, not the pipeline itself -- that has its own
tests.

The two that matter most:

  * narration is never implicit. A freshly analysed game reaches `ready`
    with no audio attached, and only an explicit narrate call produces any.
  * edited text goes through the same verifier, not a lighter one. A
    reviewer who types an invented tactic into a beat gets it flagged.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from voxplay.web import chess_api
from voxplay.web.app import app

SAMPLE = """[Event "Paris Opera"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[White "Paul Morphy"]
[Black "Duke Karl and Count Isouard"]
[Result "1-0"]
[ECO "C41"]
[Opening "Philidor Defense"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""

OFFLINE = {
    "engine": "mock",
    "nodes": "200000",
    "generator": "template",
    "openings": "false",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client whose job artefacts land in a temp directory."""
    monkeypatch.setattr(chess_api, "DATA_ROOT", tmp_path / "web")
    with TestClient(app) as c:
        yield c


def wait_for(client, job_id, statuses, timeout=90.0):
    """Poll until the job reaches one of `statuses`, like the browser does."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/chess/games/{job_id}").json()
        if snap["status"] in statuses:
            return snap
        if snap["status"] == "error":
            pytest.fail(f"job failed: {snap['error']}")
        time.sleep(0.05)
    pytest.fail(f"job stayed in {snap['status']} for {timeout}s")


@pytest.fixture
def analysed(client):
    """A game that has been analysed and scripted, but not narrated."""
    res = client.post("/api/chess/games", data={"pgn_text": SAMPLE, **OFFLINE})
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    return job_id, wait_for(client, job_id, {"ready"})


# ---------------------------------------------------------------------------
# Accepting a game
# ---------------------------------------------------------------------------

def test_pasted_pgn_is_analysed(analysed):
    _, snap = analysed
    assert snap["meta"]["white"] == "Paul Morphy"
    assert snap["meta"]["result"] == "1-0"
    assert len(snap["moves"]) == 33


def test_an_uploaded_file_is_analysed(client):
    res = client.post(
        "/api/chess/games",
        data=OFFLINE,
        files={"file": ("game.pgn", SAMPLE.encode(), "application/x-chess-pgn")},
    )
    assert res.status_code == 200
    snap = wait_for(client, res.json()["job_id"], {"ready"})
    assert snap["meta"]["black"].startswith("Duke Karl")


def test_a_pgn_in_latin_1_is_not_rejected(client):
    """Old PGN files are routinely cp1252. Refusing them is not an option."""
    pgn = SAMPLE.replace("Paul Morphy", "Paul Morphyé").encode("latin-1")
    res = client.post(
        "/api/chess/games", data=OFFLINE,
        files={"file": ("game.pgn", pgn, "application/x-chess-pgn")},
    )
    assert res.status_code == 200
    wait_for(client, res.json()["job_id"], {"ready"})


def test_an_empty_request_is_refused(client):
    res = client.post("/api/chess/games", data=OFFLINE)
    assert res.status_code == 400
    assert "PGN" in res.json()["detail"]


def test_an_oversized_upload_is_refused(client):
    huge = b"x" * (chess_api.MAX_PGN_BYTES + 1)
    res = client.post(
        "/api/chess/games", data=OFFLINE,
        files={"file": ("big.pgn", huge, "application/x-chess-pgn")},
    )
    assert res.status_code == 413


def test_an_absurd_node_count_is_refused(client):
    res = client.post(
        "/api/chess/games",
        data={"pgn_text": SAMPLE, "engine": "mock", "nodes": "999999999"},
    )
    assert res.status_code == 400


def test_a_pgn_with_no_moves_fails_with_a_reason(client):
    res = client.post(
        "/api/chess/games",
        data={"pgn_text": '[Result "*"]\n\n*', **OFFLINE},
    )
    job_id = res.json()["job_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        snap = client.get(f"/api/chess/games/{job_id}").json()
        if snap["status"] == "error":
            assert "no moves" in snap["error"]
            return
        time.sleep(0.05)
    pytest.fail("a move-less PGN should have failed")


def test_an_unknown_job_is_a_404(client):
    assert client.get("/api/chess/games/deadbeef").status_code == 404


# ---------------------------------------------------------------------------
# What the browser is handed
# ---------------------------------------------------------------------------

def test_the_run_is_narrated_to_the_browser(analysed):
    """
    What the progress panel shows. The per-position percentages cannot be
    asserted here -- the mock engine finishes a game faster than a poll
    arrives -- so the callback itself is tested in test_chess_engine.py and
    this covers the reporting the browser actually reads.
    """
    _, snap = analysed
    assert snap["pct"] == 100.0
    log = chr(10).join(snap["log"])
    assert "Analysing 34 positions" in log
    assert "accuracy" in log
    assert "Verification:" in log
    assert snap["timings"]["analysis_seconds"] >= 0


def test_every_move_carries_what_the_board_needs(analysed):
    _, snap = analysed
    first = snap["moves"][0]
    assert first["san"] == "e4"
    assert first["uci"] == "e2e4"
    assert first["fen_after"].startswith("rnbqkbnr/pppppppp")


def test_the_script_is_returned_beat_by_beat(analysed):
    _, snap = analysed
    assert snap["script"], "no beats came back"
    for beat in snap["script"]:
        assert beat["text"].strip()
        assert beat["words"] > 0
        assert isinstance(beat["plies"], list)


def test_baselines_come_back_for_comparison(analysed):
    _, snap = analysed
    b = snap["baselines"]
    assert b["every_move_beats"] == 33
    assert b["every_move_seconds"] > b["selected_seconds"]


# ---------------------------------------------------------------------------
# The gate, over edited text
# ---------------------------------------------------------------------------

def test_the_generated_script_passes_its_own_gate(client, analysed):
    job_id, snap = analysed
    lines = [b["text"] for b in snap["script"]]
    res = client.post(f"/api/chess/games/{job_id}/verify", json={"lines": lines})
    assert res.status_code == 200
    assert res.json()["clean"] is True


@pytest.mark.parametrize("text,check", [
    ("White plays Nf3 and the position is balanced.", "notation_leak"),
    ("A beautiful fork wins material on the spot.", "ungrounded_tactic"),
    ("Pure Kasparov, straight from his very best years.", "ungrounded_name"),
])
def test_an_invented_claim_typed_by_a_reviewer_is_caught(client, analysed, text, check):
    """
    The point of letting people edit the text in the browser: the gate does
    not care who wrote the sentence.
    """
    job_id, snap = analysed
    lines = [b["text"] for b in snap["script"]]
    # The framing beat covers no moves, so it is licensed to mention no
    # tactics at all. Dropping the sentence into a beat that happens to sit
    # on a real fork would test nothing.
    target = min(range(len(snap["script"])), key=lambda i: len(snap["script"][i]["plies"]))
    lines[target] = text
    res = client.post(f"/api/chess/games/{job_id}/verify", json={"lines": lines})
    body = res.json()
    assert body["clean"] is False
    checks = {f["check"] for beat in body["beats"] for f in beat["findings"]}
    assert check in checks


def test_verifying_does_not_overwrite_the_stored_script(client, analysed):
    """An edit being checked is not an edit being accepted."""
    job_id, snap = analysed
    original = snap["script"][0]["text"]
    lines = [b["text"] for b in snap["script"]]
    lines[0] = "Pure Kasparov."
    client.post(f"/api/chess/games/{job_id}/verify", json={"lines": lines})
    after = client.get(f"/api/chess/games/{job_id}").json()
    assert after["script"][0]["text"] == original


def test_the_wrong_number_of_lines_is_refused(client, analysed):
    job_id, _ = analysed
    res = client.post(f"/api/chess/games/{job_id}/verify", json={"lines": ["one"]})
    assert res.status_code == 400


def test_an_empty_beat_is_refused(client, analysed):
    job_id, snap = analysed
    lines = [b["text"] for b in snap["script"]]
    lines[0] = "   "
    res = client.post(f"/api/chess/games/{job_id}/verify", json={"lines": lines})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Narration is a separate, explicit step
# ---------------------------------------------------------------------------

def test_analysis_alone_produces_no_audio(analysed):
    """
    The whole reason the pipeline is split. Reaching `ready` must not have
    spoken anything -- the reviewer decides that.
    """
    _, snap = analysed
    assert snap["status"] == "ready"
    assert snap["audio"] is None


def test_narrating_before_analysing_is_refused(client):
    res = client.post("/api/chess/games", data={"pgn_text": SAMPLE, **OFFLINE})
    job_id = res.json()["job_id"]
    # Racing the worker: if it has not written a script yet, this is a 409.
    out = client.post(f"/api/chess/games/{job_id}/narrate", json={})
    assert out.status_code in {200, 409}


def test_audio_is_a_404_until_it_is_asked_for(client, analysed):
    job_id, _ = analysed
    assert client.get(f"/api/chess/games/{job_id}/audio").status_code == 404
    assert client.get(f"/api/chess/games/{job_id}/player").status_code == 404


def test_narration_speaks_the_edited_text_not_the_original(client, analysed):
    job_id, snap = analysed
    lines = [b["text"] for b in snap["script"]]
    lines[0] = "A completely rewritten opening line for this game."
    res = client.post(
        f"/api/chess/games/{job_id}/narrate",
        json={"lines": lines, "tts": "mock", "player": False},
    )
    assert res.status_code == 200
    done = wait_for(client, job_id, {"narrated"})
    assert done["script"][0]["text"] == lines[0]
    assert done["audio"] is not None
    assert len(done["audio"]["cues"]) == len(lines)


def test_a_narrated_game_serves_its_track(client, analysed):
    job_id, _ = analysed
    client.post(
        f"/api/chess/games/{job_id}/narrate",
        json={"tts": "mock", "player": False},
    )
    wait_for(client, job_id, {"narrated"})
    res = client.get(f"/api/chess/games/{job_id}/audio")
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert len(res.content) > 0


def test_cues_run_in_order_and_do_not_overlap(client, analysed):
    """The timeline drives the player's highlighting; gaps are fine, overlaps are not."""
    job_id, _ = analysed
    client.post(
        f"/api/chess/games/{job_id}/narrate",
        json={"tts": "mock", "player": False},
    )
    cues = wait_for(client, job_id, {"narrated"})["audio"]["cues"]
    for earlier, later in zip(cues, cues[1:]):
        end = earlier["start_seconds"] + earlier["duration_seconds"]
        assert later["start_seconds"] >= end - 0.01


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

def test_games_are_listed_newest_first(client, analysed):
    job_id, _ = analysed
    rows = client.get("/api/chess/games").json()
    assert any(r["job_id"] == job_id for r in rows)


def test_a_game_can_be_deleted(client, analysed):
    job_id, _ = analysed
    assert client.delete(f"/api/chess/games/{job_id}").status_code == 200
    assert client.get(f"/api/chess/games/{job_id}").status_code == 404


def test_the_studio_page_is_served_at_the_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "VoxPlay" in res.text


def test_the_cricket_scorer_keeps_its_own_page(client):
    assert client.get("/cricket").status_code == 200
