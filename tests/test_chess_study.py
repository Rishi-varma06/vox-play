"""
Tests for the listener-study harness.

Offline: no clips are rendered, no engine runs. What is tested is the part
that would quietly ruin the study if it were wrong -- the Latin-square
assignment, and the round trip from a participant's returned code to the rows
the analysis reads.

A balanced design that is not actually balanced produces a complete, ordinary
looking dataset in which condition is confounded with order and game. Nothing
downstream can detect that, so it is tested here.
"""

from __future__ import annotations

import base64
import json

import pytest

from voxplay.chessgame.study import (
    CLIPS_PER_PARTICIPANT,
    CONDITIONS,
    ITEMS,
    StudyError,
    StudyResponse,
    assign,
    collect,
    coverage,
    decode_response,
    generator_for,
    responses_to_rows,
    sheet_for,
    write_csv,
)

GAMES = ["g1", "g2", "g3", "g4"]


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def test_a_participant_gets_one_clip_per_position():
    plan = assign(0, GAMES)
    assert [c.position for c in plan] == list(range(CLIPS_PER_PARTICIPANT))


def test_a_participant_never_hears_the_same_game_twice():
    for participant in range(12):
        plan = assign(participant, GAMES)
        assert len({c.game_id for c in plan}) == len(plan)


def test_a_participant_hears_every_condition_once():
    for participant in range(12):
        plan = assign(participant, GAMES)
        assert {c.condition for c in plan} == set(CONDITIONS)


def test_every_condition_appears_in_every_position_equally():
    """
    The reason for the Latin square. Without it, order effects confound with
    condition and no analysis afterwards can separate them.
    """
    counts = {(pos, cond): 0 for pos in range(4) for cond in CONDITIONS}
    for participant in range(len(CONDITIONS) * 3):
        for clip in assign(participant, GAMES):
            counts[(clip.position, clip.condition)] += 1
    assert len(set(counts.values())) == 1


def test_every_condition_is_paired_with_every_game_equally():
    """
    Regression, and the worst bug this harness has had. The first version
    rotated game and condition together, so `flat` was always heard on game 1
    and `system` always on game 2. Condition was perfectly confounded with
    game: if one game were duller, its condition would lose for a reason that
    has nothing to do with the commentary, and no analysis of the resulting
    dataset could reveal it.
    """
    counts = {(g, c): 0 for g in GAMES for c in CONDITIONS}
    for participant in range(len(CONDITIONS) * len(GAMES)):
        for clip in assign(participant, GAMES):
            counts[(clip.game_id, clip.condition)] += 1
    assert len(set(counts.values())) == 1, counts


def test_conditions_are_evenly_covered_across_the_study():
    counts = coverage(28, GAMES)
    assert len(set(counts.values())) == 1


def test_too_few_games_is_refused_rather_than_repeating_one():
    with pytest.raises(StudyError, match="distinct games"):
        assign(0, ["only", "two"])


def test_no_games_is_refused():
    with pytest.raises(StudyError):
        assign(0, [])


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------

def test_each_condition_maps_to_its_own_beat_selection():
    from voxplay.chessgame.engine import MockAnalysisEngine, analyse_game
    from voxplay.chessgame.pgn_source import load_pgn
    from pathlib import Path

    game = analyse_game(
        load_pgn(Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"),
        MockAnalysisEngine(),
    )
    flat = sheet_for(game, "flat")
    system = sheet_for(game, "system")
    assert len(flat.beats) == game.total_plies
    assert len(system.beats) < len(flat.beats)


def test_the_template_arm_shares_the_systems_pacing():
    """
    It differs only in that nothing wrote the prose, which is what isolates
    the language model's contribution from the facts being correct.
    """
    assert generator_for("template") == "template"
    assert generator_for("system") != "template"


def test_an_unknown_condition_is_refused():
    with pytest.raises(StudyError):
        sheet_for(None, "nonsense")


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

RESPONSE = {
    "participant": "P007",
    "strength": "casual",
    "watched_commentary": "sometimes",
    "ratings": [
        {"position": 0, "gameId": "g1", "condition": "flat", "comment": "long",
         "informativeness": 4, "engagement": 2, "accuracy": 5,
         "pacing": 1, "naturalness": 3},
        {"position": 1, "gameId": "g2", "condition": "system", "comment": "",
         "informativeness": 5, "engagement": 5, "accuracy": 5,
         "pacing": 5, "naturalness": 4},
    ],
    "preferred_position": 1,
    "preference_reason": "it kept moving",
}


def test_a_pasted_code_is_decoded():
    code = base64.b64encode(json.dumps(RESPONSE).encode("utf-8")).decode("ascii")
    assert decode_response(code).participant == "P007"


def test_raw_json_is_also_accepted():
    """A participant may return the downloaded file instead of the code."""
    assert decode_response(json.dumps(RESPONSE)).participant == "P007"


def test_an_empty_response_is_refused():
    with pytest.raises(StudyError):
        decode_response("   ")


def test_a_corrupt_code_is_refused_clearly():
    with pytest.raises(StudyError):
        decode_response("not-base64-and-not-json!!")


def test_one_row_per_rated_clip():
    rows = responses_to_rows([StudyResponse(**RESPONSE)])
    assert len(rows) == 2


def test_the_participant_travels_on_every_row():
    """Within-subjects tests pair on participant, not on file order."""
    rows = responses_to_rows([StudyResponse(**RESPONSE)])
    assert all(r["participant"] == "P007" for r in rows)


def test_every_rated_item_reaches_the_rows():
    row = responses_to_rows([StudyResponse(**RESPONSE)])[0]
    for item in ITEMS:
        assert item in row


def test_the_preferred_clip_is_flagged_on_its_own_row():
    rows = responses_to_rows([StudyResponse(**RESPONSE)])
    assert [r["preferred"] for r in rows] == [0, 1]


def test_the_condition_is_recorded_even_though_it_was_never_shown():
    rows = responses_to_rows([StudyResponse(**RESPONSE)])
    assert {r["condition"] for r in rows} == {"flat", "system"}


def test_rows_become_a_csv_with_a_stable_header(tmp_path):
    rows = responses_to_rows([StudyResponse(**RESPONSE)])
    path = write_csv(rows, tmp_path / "results.csv")
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("participant,strength,watched_commentary,position")
    for item in ITEMS:
        assert item in header


def test_collecting_skips_unreadable_files_rather_than_aborting(tmp_path):
    """Losing a whole collection run to one corrupted paste would be absurd."""
    good = base64.b64encode(json.dumps(RESPONSE).encode()).decode()
    (tmp_path / "ok.txt").write_text(good, encoding="utf-8")
    (tmp_path / "broken.txt").write_text("garbage", encoding="utf-8")
    read, failed, _ = collect(tmp_path, tmp_path / "out.csv")
    assert (read, failed) == (1, 1)


def test_collecting_an_empty_folder_writes_a_header_only_csv(tmp_path):
    read, failed, path = collect(tmp_path, tmp_path / "out.csv")
    assert read == 0 and failed == 0
    assert path.read_text(encoding="utf-8").strip().count("\n") == 0
