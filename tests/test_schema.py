"""Tests for event schema parsing and validation."""

import json

import pytest
from pydantic import TypeAdapter

from voxplay.events.models import (
    BallEvent,
    DismissalType,
    DrinksEvent,
    Event,
    EventType,
    Extras,
    ExtrasType,
    InningsStartEvent,
    MatchState,
    MilestoneEvent,
    Outcome,
    OverStartEvent,
    WicketInfo,
)

_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: dict) -> Event:
    return _event_adapter.validate_python(data)


# ---------------------------------------------------------------------------
# BallEvent
# ---------------------------------------------------------------------------

def test_ball_event_minimal():
    ev = parse_event({"event_type": "ball", "match_id": "m1", "innings": 1,
                      "over": 0, "ball": 1})
    assert isinstance(ev, BallEvent)
    assert ev.outcome.runs == 0
    assert ev.outcome.wicket is None


def test_ball_event_six():
    ev = parse_event({
        "event_type": "ball",
        "match_id": "m1",
        "innings": 1,
        "over": 2,
        "ball": 4,
        "bowler": "Bumrah",
        "batsman_striker": "Root",
        "batsman_non_striker": "Bairstow",
        "outcome": {"runs": 6, "is_boundary": True, "is_six": True,
                    "shot_tag": "slog_sweep"},
    })
    assert ev.outcome.runs == 6
    assert ev.outcome.is_six is True
    assert ev.outcome.shot_tag == "slog_sweep"


def test_ball_event_wicket():
    ev = parse_event({
        "event_type": "ball",
        "match_id": "m1",
        "innings": 1,
        "over": 3,
        "ball": 2,
        "outcome": {
            "runs": 0,
            "wicket": {
                "dismissal_type": "caught",
                "dismissed_batsman": "Warner",
                "fielder": "Root",
            },
        },
    })
    assert ev.outcome.wicket is not None
    assert ev.outcome.wicket.dismissal_type == DismissalType.CAUGHT
    assert ev.outcome.wicket.fielder == "Root"


def test_ball_event_wide():
    ev = parse_event({
        "event_type": "ball",
        "match_id": "m1",
        "innings": 1,
        "over": 0,
        "ball": 3,
        "outcome": {"runs": 0, "extras": {"type": "wide", "runs": 1}},
    })
    assert ev.outcome.extras.type == ExtrasType.WIDE


def test_ball_event_optional_fields_absent():
    """All optional fields must default gracefully — no validation error."""
    ev = parse_event({"event_type": "ball", "match_id": "x", "innings": 2,
                      "over": 0, "ball": 1})
    assert ev.bowler is None
    assert ev.batsman_striker is None
    assert ev.tags == []


# ---------------------------------------------------------------------------
# Non-ball events
# ---------------------------------------------------------------------------

def test_innings_start():
    ev = parse_event({
        "event_type": "innings_start",
        "match_id": "m1",
        "innings": 2,
        "batting_team": "England",
        "bowling_team": "Australia",
        "target": 287,
        "overs": 50,
    })
    assert isinstance(ev, InningsStartEvent)
    assert ev.target == 287


def test_over_start():
    ev = parse_event({"event_type": "over_start", "match_id": "m1",
                      "innings": 1, "over": 5, "bowler": "Cummins"})
    assert isinstance(ev, OverStartEvent)
    assert ev.bowler == "Cummins"


def test_milestone():
    ev = parse_event({
        "event_type": "milestone",
        "match_id": "m1",
        "innings": 1,
        "description": "Kohli reaches 50",
        "player": "Kohli",
        "value": 50,
    })
    assert isinstance(ev, MilestoneEvent)
    assert ev.value == 50


def test_drinks():
    ev = parse_event({"event_type": "drinks", "match_id": "m1",
                      "innings": 1, "over": 10})
    assert isinstance(ev, DrinksEvent)


# ---------------------------------------------------------------------------
# Discriminated union round-trip
# ---------------------------------------------------------------------------

def test_roundtrip_json():
    ball = BallEvent(
        match_id="m1", innings=1, over=0, ball=1,
        outcome=Outcome(runs=4, is_boundary=True),
    )
    data = ball.model_dump_json()
    restored = _event_adapter.validate_json(data)
    assert isinstance(restored, BallEvent)
    assert restored.outcome.runs == 4


def test_discriminator_rejects_unknown():
    with pytest.raises(Exception):
        parse_event({"event_type": "unknown_type", "match_id": "m1", "innings": 1})
