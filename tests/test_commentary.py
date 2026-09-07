"""Tests for the commentary engine: mock generation and rolling-window logic."""

from __future__ import annotations

from collections import deque
from typing import List

import pytest

from voxplay.commentary.engine import CommentaryEngine, CommentaryLine, _mock_generate
from voxplay.commentary.prompts import event_to_prompt, build_ball_prompt
from voxplay.events.models import (
    BallEvent,
    InningsStartEvent,
    MatchState,
    MilestoneEvent,
    Outcome,
    OverStartEvent,
)


# ---------------------------------------------------------------------------
# Mock generation
# ---------------------------------------------------------------------------

def test_mock_generates_string():
    text = _mock_generate("ball")
    assert isinstance(text, str)
    assert len(text) > 0


def test_mock_cycles_through_responses():
    """Mock should not repeat the same response every time (it cycles)."""
    responses = [_mock_generate("ball") for _ in range(12)]
    # At least two distinct responses across 12 calls
    assert len(set(responses)) >= 2


def test_mock_generates_for_all_event_types():
    for et in ("ball", "over_start", "milestone", "innings_start", "drinks"):
        text = _mock_generate(et)
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# CommentaryEngine.commentate — event stream → CommentaryLine list
# ---------------------------------------------------------------------------

def _make_ball(over: int = 0, ball: int = 1, runs: int = 0) -> BallEvent:
    return BallEvent(
        match_id="t1",
        innings=1,
        over=over,
        ball=ball,
        bowler="Bowler",
        batsman_striker="Batsman",
        outcome=Outcome(runs=runs),
        state=MatchState(score=runs, wickets=0),
    )


def test_commentate_returns_one_line_per_event():
    engine = CommentaryEngine(mock=True)
    events = [_make_ball(runs=r) for r in range(5)]
    lines = list(engine.commentate(events))
    assert len(lines) == 5


def test_commentate_line_has_text():
    engine = CommentaryEngine(mock=True)
    events = [_make_ball()]
    line = list(engine.commentate(events))[0]
    assert isinstance(line.text, str)
    assert line.text


def test_commentate_event_index_sequential():
    engine = CommentaryEngine(mock=True)
    events = [_make_ball(ball=i) for i in range(1, 7)]
    lines = list(engine.commentate(events))
    for i, line in enumerate(lines):
        assert line.event_index == i


def test_commentate_event_type_set():
    engine = CommentaryEngine(mock=True)
    ball = _make_ball()
    over = OverStartEvent(match_id="t1", innings=1, over=0, bowler="X", state=MatchState())
    lines = list(engine.commentate([ball, over]))
    assert lines[0].event_type == "ball"
    assert lines[1].event_type == "over_start"


# ---------------------------------------------------------------------------
# Rolling context window
# ---------------------------------------------------------------------------

def test_window_grows_up_to_max():
    engine = CommentaryEngine(mock=True, window_size=3)
    events = [_make_ball(ball=i) for i in range(1, 6)]
    list(engine.commentate(events))
    # After 5 events, window should hold at most 3
    assert len(engine._window) == 3


def test_window_reset_clears():
    engine = CommentaryEngine(mock=True, window_size=3)
    events = [_make_ball()]
    list(engine.commentate(events))
    engine.reset()
    assert len(engine._window) == 0


def test_prompt_includes_recent_commentary():
    """build_ball_prompt should embed recent commentary lines in the prompt text."""
    ev = _make_ball()
    window = ["Great delivery!", "Good running between the wickets."]
    prompt = build_ball_prompt(ev, window)
    assert "Great delivery!" in prompt
    assert "Good running between the wickets." in prompt


def test_prompt_empty_window_has_no_context_section():
    ev = _make_ball()
    prompt = build_ball_prompt(ev, [])
    assert "Recent commentary" not in prompt


# ---------------------------------------------------------------------------
# Mixed event stream
# ---------------------------------------------------------------------------

def test_full_mixed_stream():
    engine = CommentaryEngine(mock=True)
    events = [
        InningsStartEvent(match_id="t1", innings=1, batting_team="A", bowling_team="B", overs=20, state=MatchState()),
        OverStartEvent(match_id="t1", innings=1, over=0, bowler="X", state=MatchState()),
        _make_ball(runs=4),
        _make_ball(runs=6),
        MilestoneEvent(match_id="t1", innings=1, description="Fifty up", player="Batsman", value=50, state=MatchState()),
    ]
    lines = list(engine.commentate(events))
    assert len(lines) == 5
    assert all(isinstance(l, CommentaryLine) for l in lines)
