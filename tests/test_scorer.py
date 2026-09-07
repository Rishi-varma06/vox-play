"""Tests for the interactive scorer's event output and state management."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from voxplay.events.models import (
    BallEvent,
    DismissalType,
    DrinksEvent,
    EventType,
    Extras,
    ExtrasType,
    InningsStartEvent,
    OverStartEvent,
    WicketInfo,
)
from voxplay.scorer.scorer import InteractiveScorer


# ---------------------------------------------------------------------------
# Helper: drive the scorer with canned input lines
# ---------------------------------------------------------------------------

def _run_scorer(inputs: list[str], match_id: str = "test_match") -> list:
    """Patch input() to feed *inputs* and return the event list."""
    inp_iter = iter(inputs)

    def fake_input(prompt=""):
        try:
            return next(inp_iter)
        except StopIteration:
            return "s"  # save & exit if we run out

    scorer = InteractiveScorer(match_id=match_id)
    with patch("builtins.input", side_effect=fake_input):
        scorer.run()
    return scorer.events


# ---------------------------------------------------------------------------
# Basic innings setup produces InningsStart + OverStart
# ---------------------------------------------------------------------------

def test_first_events_are_innings_and_over_start():
    inputs = [
        "test_match",   # match ID
        "1",            # 1 innings
        "Team A",       # batting
        "Team B",       # bowling
        "",             # no target
        "20",           # 20 overs
        "Bumrah",       # bowler
        "Kohli",        # striker
        "Rohit",        # non-striker
        "s",            # save
    ]
    events = _run_scorer(inputs)
    types = [e.event_type for e in events]
    assert EventType.INNINGS_START in types
    assert EventType.OVER_START in types


# ---------------------------------------------------------------------------
# Numeric input records BallEvents with correct runs
# ---------------------------------------------------------------------------

def test_single_run_recorded():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "1",    # 1 run
        "s",
    ]
    events = _run_scorer(inputs)
    balls = [e for e in events if isinstance(e, BallEvent)]
    assert len(balls) == 1
    assert balls[0].outcome.runs == 1


def test_boundary_four():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "4",
        "s",
    ]
    events = _run_scorer(inputs)
    balls = [e for e in events if isinstance(e, BallEvent)]
    assert balls[0].outcome.runs == 4
    assert balls[0].outcome.is_boundary is True


def test_six():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "6",
        "s",
    ]
    events = _run_scorer(inputs)
    balls = [e for e in events if isinstance(e, BallEvent)]
    assert balls[0].outcome.runs == 6
    assert balls[0].outcome.is_six is True


# ---------------------------------------------------------------------------
# Wide — extras recorded, ball count not incremented
# ---------------------------------------------------------------------------

def test_wide_records_extra():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "w",
        "s",
    ]
    events = _run_scorer(inputs)
    balls = [e for e in events if isinstance(e, BallEvent)]
    assert balls[0].outcome.extras is not None
    assert balls[0].outcome.extras.type == ExtrasType.WIDE
    # Wide doesn't advance ball count — still ball 0 in over
    assert balls[0].state.balls_in_over == 0


# ---------------------------------------------------------------------------
# Dot ball
# ---------------------------------------------------------------------------

def test_dot_ball():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "0",
        "s",
    ]
    events = _run_scorer(inputs)
    balls = [e for e in events if isinstance(e, BallEvent)]
    assert balls[0].outcome.runs == 0
    assert balls[0].outcome.wicket is None
    assert balls[0].outcome.extras is None


# ---------------------------------------------------------------------------
# State is automatically updated
# ---------------------------------------------------------------------------

def test_state_score_accumulates():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "2",
        "3",
        "s",
    ]
    events = _run_scorer(inputs)
    balls = [e for e in events if isinstance(e, BallEvent)]
    assert len(balls) == 2
    assert balls[1].state.score == 5


# ---------------------------------------------------------------------------
# Undo removes last ball
# ---------------------------------------------------------------------------

def test_undo_removes_last_ball():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "4",
        "u",    # undo the 4
        "1",    # replace with 1
        "s",
    ]
    events = _run_scorer(inputs)
    balls = [e for e in events if isinstance(e, BallEvent)]
    assert len(balls) == 1
    assert balls[0].outcome.runs == 1


# ---------------------------------------------------------------------------
# Drinks event
# ---------------------------------------------------------------------------

def test_drinks_event_added():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "d",    # drinks
        "s",
    ]
    events = _run_scorer(inputs)
    drinks = [e for e in events if isinstance(e, DrinksEvent)]
    assert len(drinks) == 1


# ---------------------------------------------------------------------------
# Event stream validity — all events have correct match_id and innings
# ---------------------------------------------------------------------------

def test_all_events_have_match_id():
    inputs = [
        "my_game", "1",
        "India", "Australia", "", "20", "Shami", "Virat", "Rohit",
        "1", "2",
        "s",
    ]
    events = _run_scorer(inputs, match_id="my_game")
    for ev in events:
        assert ev.match_id == "my_game"


def test_all_events_have_innings_number():
    inputs = [
        "test_match", "1",
        "Team A", "Team B", "", "20", "Bumrah", "Kohli", "Rohit",
        "0",
        "s",
    ]
    events = _run_scorer(inputs)
    for ev in events:
        assert ev.innings == 1
