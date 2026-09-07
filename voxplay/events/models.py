"""
Voxplay event schema — the single contract through the entire pipeline.

Every input source (scorecard file, interactive scorer, future vision model)
produces a stream of these events. Every downstream component (commentary
engine, TTS, assembler) consumes only these events.

Extension point: see ingest/base.py for the EventSource interface where a
future vision-model input would plug in.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    BALL = "ball"
    INNINGS_START = "innings_start"
    OVER_START = "over_start"
    MILESTONE = "milestone"
    DRINKS = "drinks"


class DismissalType(str, Enum):
    BOWLED = "bowled"
    CAUGHT = "caught"
    LBW = "lbw"
    RUN_OUT = "run_out"
    STUMPED = "stumped"
    HIT_WICKET = "hit_wicket"
    HANDLED_BALL = "handled_ball"
    OBSTRUCTING = "obstructing"
    TIMED_OUT = "timed_out"
    RETIRED_HURT = "retired_hurt"


class ExtrasType(str, Enum):
    WIDE = "wide"
    NO_BALL = "no_ball"
    BYE = "bye"
    LEG_BYE = "leg_bye"
    PENALTY = "penalty"


# ---------------------------------------------------------------------------
# Sub-objects
# ---------------------------------------------------------------------------

class WicketInfo(BaseModel):
    """Dismissal details embedded inside an Outcome."""
    dismissal_type: DismissalType
    dismissed_batsman: str
    fielder: Optional[str] = None  # catcher, run-out fielder, etc.
    bowler_credited: bool = True   # False for run-outs where bowler isn't credited


class Extras(BaseModel):
    type: ExtrasType
    runs: int = 1


class Outcome(BaseModel):
    """What happened on a single delivery."""
    runs: int = 0                       # runs scored off the bat
    extras: Optional[Extras] = None
    wicket: Optional[WicketInfo] = None
    shot_tag: Optional[str] = None      # e.g. "cover_drive", "pull", "sweep", "leave"
    is_boundary: bool = False           # set True for 4s/6s for quick flag
    is_six: bool = False


class MatchState(BaseModel):
    """
    Snapshot of match state AFTER this event.

    All fields are optional so that partially-populated events from a scorecard
    file parse without errors. The interactive scorer and state engine populate
    every field.
    """
    score: int = 0
    wickets: int = 0
    overs_complete: int = 0
    balls_in_over: int = 0             # 0-5 within current over
    target: Optional[int] = None       # second innings only
    balls_remaining: Optional[int] = None
    striker_runs: int = 0
    striker_balls: int = 0
    non_striker_runs: int = 0
    non_striker_balls: int = 0
    partnership_runs: int = 0
    partnership_balls: int = 0
    required_run_rate: Optional[float] = None
    current_run_rate: Optional[float] = None


# ---------------------------------------------------------------------------
# Event models
# ---------------------------------------------------------------------------

class _BaseEvent(BaseModel):
    """Fields common to every event type."""
    match_id: str
    innings: int
    timestamp: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    video_offset: Optional[float] = None  # seconds from match-clock start; for AV sync


class BallEvent(_BaseEvent):
    event_type: Literal[EventType.BALL] = EventType.BALL
    over: int
    ball: int  # 1-based within the over
    bowler: Optional[str] = None
    batsman_striker: Optional[str] = None
    batsman_non_striker: Optional[str] = None
    outcome: Outcome = Field(default_factory=Outcome)
    state: MatchState = Field(default_factory=MatchState)
    delivery_type: Optional[str] = None  # e.g. "yorker", "bouncer", "good_length"
    note: Optional[str] = None           # free-text scorer annotation


class InningsStartEvent(_BaseEvent):
    event_type: Literal[EventType.INNINGS_START] = EventType.INNINGS_START
    batting_team: Optional[str] = None
    bowling_team: Optional[str] = None
    target: Optional[int] = None       # present for 2nd/4th innings
    overs: Optional[int] = None        # max overs (None = unlimited / Test)
    state: MatchState = Field(default_factory=MatchState)


class OverStartEvent(_BaseEvent):
    event_type: Literal[EventType.OVER_START] = EventType.OVER_START
    over: int
    bowler: Optional[str] = None
    state: MatchState = Field(default_factory=MatchState)


class MilestoneEvent(_BaseEvent):
    """Batsman fifty/hundred, team 100/200, partnership record, etc."""
    event_type: Literal[EventType.MILESTONE] = EventType.MILESTONE
    description: str                   # human-readable, e.g. "Virat Kohli reaches 50"
    player: Optional[str] = None
    value: Optional[int] = None        # the numeric milestone (50, 100, …)
    state: MatchState = Field(default_factory=MatchState)


class DrinksEvent(_BaseEvent):
    event_type: Literal[EventType.DRINKS] = EventType.DRINKS
    over: Optional[int] = None
    state: MatchState = Field(default_factory=MatchState)


# ---------------------------------------------------------------------------
# Discriminated union — the single type the rest of the pipeline handles
# ---------------------------------------------------------------------------

Event = Annotated[
    Union[
        BallEvent,
        InningsStartEvent,
        OverStartEvent,
        MilestoneEvent,
        DrinksEvent,
    ],
    Field(discriminator="event_type"),
]
