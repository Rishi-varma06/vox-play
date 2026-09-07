"""
Automatic match-state computation.

Given the current MatchState and a BallEvent outcome, returns the next
MatchState. Callers (interactive scorer, CSV ingest) only need to supply
ball outcomes; state is derived here.
"""

from __future__ import annotations

import copy
from typing import Optional

from .models import BallEvent, ExtrasType, MatchState, Outcome


def _total_runs(outcome: Outcome) -> int:
    """Runs that count toward the team score (bat runs + extras)."""
    bat = outcome.runs
    extra = outcome.extras.runs if outcome.extras else 0
    return bat + extra


def _is_legal_delivery(outcome: Outcome) -> bool:
    """Wide and no-ball do not count toward the over tally."""
    if outcome.extras is None:
        return True
    return outcome.extras.type not in (ExtrasType.WIDE, ExtrasType.NO_BALL)


def advance_state(
    prev: MatchState,
    outcome: Outcome,
    over_number: int,
    total_overs: Optional[int] = None,
    striker_changed_by_run: bool = False,
) -> MatchState:
    """
    Return a new MatchState after applying *outcome* to *prev*.

    Args:
        prev:                   State before this delivery.
        outcome:                What happened on the ball.
        over_number:            Current over (0-indexed, for balls_remaining).
        total_overs:            Match length cap (None = unlimited).
        striker_changed_by_run: Caller should set True if an odd number of
                                runs causes a mid-over strike rotation.
    """
    s = copy.deepcopy(prev)

    team_runs = _total_runs(outcome)
    s.score += team_runs
    s.partnership_runs += team_runs
    s.partnership_balls += 1 if _is_legal_delivery(outcome) else 0

    # Wicket
    if outcome.wicket:
        s.wickets += 1
        s.partnership_runs = 0
        s.partnership_balls = 0
        s.striker_runs = 0
        s.striker_balls = 0
    else:
        # Bat runs to striker (not extras)
        s.striker_runs += outcome.runs
        if _is_legal_delivery(outcome):
            s.striker_balls += 1

    # Ball count within over
    if _is_legal_delivery(outcome):
        s.balls_in_over += 1
        if s.balls_in_over >= 6:
            s.overs_complete += 1
            s.balls_in_over = 0

    # Balls remaining
    if total_overs is not None:
        total_balls = total_overs * 6
        bowled = s.overs_complete * 6 + s.balls_in_over
        s.balls_remaining = max(0, total_balls - bowled)

    # Run-rate metrics
    bowled_total = s.overs_complete + s.balls_in_over / 6.0
    if bowled_total > 0:
        s.current_run_rate = round(s.score / bowled_total, 2)
    if s.target is not None and s.balls_remaining and s.balls_remaining > 0:
        needed = s.target - s.score
        overs_left = s.balls_remaining / 6.0
        s.required_run_rate = round(needed / overs_left, 2) if overs_left > 0 else None

    return s


def init_state(target: Optional[int] = None, total_overs: Optional[int] = None) -> MatchState:
    """Fresh MatchState for the start of an innings."""
    balls_remaining = total_overs * 6 if total_overs else None
    return MatchState(target=target, balls_remaining=balls_remaining)
