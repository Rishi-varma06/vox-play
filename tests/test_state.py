"""Tests for automatic state computation."""

import pytest

from voxplay.events.models import Extras, ExtrasType, Outcome, WicketInfo, DismissalType
from voxplay.events.state import advance_state, init_state


def test_init_state_blank():
    s = init_state()
    assert s.score == 0
    assert s.wickets == 0
    assert s.balls_remaining is None


def test_init_state_with_overs():
    s = init_state(total_overs=20)
    assert s.balls_remaining == 120


def test_single_run():
    s0 = init_state(total_overs=20)
    s1 = advance_state(s0, Outcome(runs=1), over_number=0, total_overs=20)
    assert s1.score == 1
    assert s1.striker_runs == 1
    assert s1.balls_in_over == 1
    assert s1.balls_remaining == 119


def test_boundary_four():
    s0 = init_state(total_overs=20)
    s1 = advance_state(s0, Outcome(runs=4, is_boundary=True), over_number=0, total_overs=20)
    assert s1.score == 4
    assert s1.partnership_runs == 4


def test_wide_does_not_advance_ball_count():
    s0 = init_state(total_overs=20)
    outcome = Outcome(runs=0, extras=Extras(type=ExtrasType.WIDE, runs=1))
    s1 = advance_state(s0, outcome, over_number=0, total_overs=20)
    assert s1.score == 1
    assert s1.balls_in_over == 0   # wide doesn't count
    assert s1.balls_remaining == 120


def test_no_ball_extra_runs():
    s0 = init_state(total_overs=20)
    outcome = Outcome(runs=1, extras=Extras(type=ExtrasType.NO_BALL, runs=1))
    s1 = advance_state(s0, outcome, over_number=0, total_overs=20)
    assert s1.score == 2
    assert s1.balls_in_over == 0   # no-ball doesn't count


def test_wicket_resets_partnership():
    s0 = init_state(total_overs=20)
    # Build up a partnership
    s0.score = 50
    s0.partnership_runs = 40
    s0.striker_runs = 25
    outcome = Outcome(runs=0, wicket=WicketInfo(
        dismissal_type=DismissalType.BOWLED,
        dismissed_batsman="Smith",
    ))
    s1 = advance_state(s0, outcome, over_number=5, total_overs=20)
    assert s1.wickets == 1
    assert s1.partnership_runs == 0
    assert s1.striker_runs == 0


def test_over_rollover():
    s0 = init_state(total_overs=20)
    outcome = Outcome(runs=0)
    s = s0
    for _ in range(6):
        s = advance_state(s, outcome, over_number=0, total_overs=20)
    assert s.overs_complete == 1
    assert s.balls_in_over == 0
    assert s.balls_remaining == 114


def test_required_run_rate():
    s0 = init_state(target=200, total_overs=20)
    outcome = Outcome(runs=0)
    s1 = advance_state(s0, outcome, over_number=0, total_overs=20)
    # 200 needed off 119 balls (19 overs + 5 balls)
    assert s1.required_run_rate is not None
    assert s1.required_run_rate > 0


def test_current_run_rate():
    s0 = init_state(total_overs=20)
    # Score 12 in 2 overs
    s = s0
    for _ in range(12):
        s = advance_state(s, Outcome(runs=1), over_number=0, total_overs=20)
    assert s.current_run_rate == pytest.approx(6.0, abs=0.1)


def test_six_balls_complete_over():
    s0 = init_state(total_overs=20)
    s = s0
    for i in range(6):
        s = advance_state(s, Outcome(runs=0), over_number=0, total_overs=20)
    assert s.overs_complete == 1
    assert s.balls_in_over == 0
