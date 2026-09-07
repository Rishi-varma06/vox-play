"""Tests for the three new event-schema fields: delivery_type, note, video_offset."""

import json
import pytest
from pydantic import TypeAdapter

from voxplay.events.models import (
    BallEvent,
    Event,
    InningsStartEvent,
    MatchState,
    Outcome,
)

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


# ---------------------------------------------------------------------------
# video_offset — on _BaseEvent (all event types)
# ---------------------------------------------------------------------------

def test_video_offset_on_ball_event():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=1,
                   outcome=Outcome(runs=4, is_boundary=True),
                   video_offset=12.5)
    assert ev.video_offset == 12.5


def test_video_offset_on_innings_start():
    ev = InningsStartEvent(match_id="m1", innings=1,
                           batting_team="A", bowling_team="B",
                           video_offset=0.0)
    assert ev.video_offset == 0.0


def test_video_offset_defaults_none():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=1)
    assert ev.video_offset is None


def test_video_offset_roundtrip():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=1,
                   video_offset=73.2)
    restored = _adapter.validate_json(ev.model_dump_json())
    assert restored.video_offset == pytest.approx(73.2)


def test_video_offset_absent_in_json_parses_fine():
    data = {"event_type": "ball", "match_id": "m1", "innings": 1,
            "over": 0, "ball": 1}
    ev = _adapter.validate_python(data)
    assert ev.video_offset is None


# ---------------------------------------------------------------------------
# delivery_type — on BallEvent
# ---------------------------------------------------------------------------

def test_delivery_type_stored():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=1,
                   delivery_type="yorker")
    assert ev.delivery_type == "yorker"


def test_delivery_type_defaults_none():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=1)
    assert ev.delivery_type is None


def test_delivery_type_roundtrip():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=2,
                   delivery_type="bouncer")
    restored = _adapter.validate_json(ev.model_dump_json())
    assert restored.delivery_type == "bouncer"


def test_delivery_type_from_dict():
    data = {"event_type": "ball", "match_id": "m1", "innings": 1,
            "over": 0, "ball": 1, "delivery_type": "good_length"}
    ev = _adapter.validate_python(data)
    assert ev.delivery_type == "good_length"


# ---------------------------------------------------------------------------
# note — on BallEvent
# ---------------------------------------------------------------------------

def test_note_stored():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=1,
                   note="Batter was late on the drive")
    assert ev.note == "Batter was late on the drive"


def test_note_defaults_none():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=1)
    assert ev.note is None


def test_note_roundtrip():
    ev = BallEvent(match_id="m1", innings=1, over=0, ball=3,
                   note="Wind affecting swing")
    restored = _adapter.validate_json(ev.model_dump_json())
    assert restored.note == "Wind affecting swing"


# ---------------------------------------------------------------------------
# All three together — full event with new fields round-trips correctly
# ---------------------------------------------------------------------------

def test_all_new_fields_together():
    ev = BallEvent(
        match_id="full_test",
        innings=2,
        over=14,
        ball=3,
        bowler="Khan",
        batsman_striker="Patel",
        outcome=Outcome(runs=6, is_six=True, is_boundary=True,
                        shot_tag="slog_sweep"),
        delivery_type="full_toss",
        note="Patel was waiting for this one",
        video_offset=842.1,
    )
    data = json.loads(ev.model_dump_json())
    assert data["delivery_type"] == "full_toss"
    assert data["note"] == "Patel was waiting for this one"
    assert data["video_offset"] == pytest.approx(842.1)

    restored = _adapter.validate_json(ev.model_dump_json())
    assert restored.delivery_type == "full_toss"
    assert restored.note == "Patel was waiting for this one"
    assert restored.video_offset == pytest.approx(842.1)


def test_new_fields_absent_does_not_break_existing_events():
    """A minimal BallEvent (no new fields) still parses and has correct defaults."""
    data = {"event_type": "ball", "match_id": "m1", "innings": 1,
            "over": 0, "ball": 1, "outcome": {"runs": 4, "is_boundary": True}}
    ev = _adapter.validate_python(data)
    assert isinstance(ev, BallEvent)
    assert ev.delivery_type is None
    assert ev.note is None
    assert ev.video_offset is None
    assert ev.outcome.runs == 4
