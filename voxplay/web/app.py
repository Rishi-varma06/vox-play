"""FastAPI backend for the VoxPlay web scorer."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, TypeAdapter

from voxplay.events.models import (
    BallEvent,
    DismissalType,
    Extras,
    ExtrasType,
    InningsStartEvent,
    MatchState,
    Outcome,
    OverStartEvent,
    WicketInfo,
)
from voxplay.events.state import advance_state, init_state
from voxplay.web.chess_api import router as chess_router
from voxplay.web.match_store import MatchSession, MatchStore

app = FastAPI(title="VoxPlay")
store = MatchStore()

_STATIC = Path(__file__).parent / "static"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

# The chess pipeline lives behind its own router so the two sports do not
# share a URL namespace or a request model.
app.include_router(chess_router)

_event_adapter: TypeAdapter = TypeAdapter(
    __import__("voxplay.events.models", fromlist=["Event"]).Event
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CreateMatchRequest(BaseModel):
    home_team: str
    away_team: str
    home_xi: List[str] = Field(default_factory=list)
    away_xi: List[str] = Field(default_factory=list)
    overs: Optional[int] = None
    toss_winner: str
    toss_choice: Literal["bat", "bowl"]


class StartInningsRequest(BaseModel):
    striker: str
    non_striker: str
    bowler: str
    batting_team: str
    bowling_team: str
    target: Optional[int] = None  # set for 2nd innings


class RecordBallRequest(BaseModel):
    # Outcome
    runs: int = 0
    extras_type: Optional[str] = None   # "wide" | "no_ball" | "bye" | "leg_bye"
    extras_runs: int = 1
    # Dismissal
    dismissal_type: Optional[str] = None
    dismissed_batsman: Optional[str] = None
    fielder: Optional[str] = None
    bowler_credited: bool = True
    # Flags
    is_boundary: bool = False
    is_six: bool = False
    shot_tag: Optional[str] = None
    # New schema fields
    delivery_type: Optional[str] = None
    note: Optional[str] = None
    video_offset: Optional[float] = None
    # After a wicket the frontend supplies the new batsman
    new_batsman: Optional[str] = None


class NewOverRequest(BaseModel):
    bowler: str


class GenerateCommentaryRequest(BaseModel):
    mock: bool = False


class GenerateVoiceRequest(BaseModel):
    provider: str = "mock"   # "mock" | "gtts" | "elevenlabs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_legal(extras_type: Optional[str]) -> bool:
    return extras_type not in ("wide", "no_ball")


def _recompute(session: MatchSession) -> MatchSession:
    """Replay all events to rebuild cached session state (used after undo)."""
    innings = 1
    batting_team = ""
    bowling_team = ""
    striker: Optional[str] = None
    non_striker: Optional[str] = None
    bowler: Optional[str] = None
    current_over = 0
    balls_in_over = 0
    over_complete = False
    innings_complete = False
    match_complete = False
    cur_state = MatchState()

    for ev_dict in session.events:
        ev = _event_adapter.validate_python(ev_dict)

        if isinstance(ev, InningsStartEvent):
            innings = ev.innings
            batting_team = ev.batting_team or ""
            bowling_team = ev.bowling_team or ""
            striker = None
            non_striker = None
            bowler = None
            current_over = 0
            balls_in_over = 0
            over_complete = False
            innings_complete = False
            cur_state = init_state(target=ev.target, total_overs=session.overs)

        elif isinstance(ev, OverStartEvent):
            bowler = ev.bowler
            current_over = ev.over
            balls_in_over = 0
            over_complete = False

        elif isinstance(ev, BallEvent):
            legal = ev.outcome.extras is None or ev.outcome.extras.type.value not in (
                "wide", "no_ball"
            )
            cur_state = advance_state(cur_state, ev.outcome, ev.over, session.overs)

            # Batsmen at time of delivery are in the event itself
            striker = ev.batsman_striker
            non_striker = ev.batsman_non_striker

            if legal:
                balls_in_over += 1

            if ev.outcome.wicket:
                # Dismissed batsman is out; new batsman isn't known yet from
                # this event alone — it will appear as striker in the next event.
                dismissed = ev.outcome.wicket.dismissed_batsman
                if dismissed == striker:
                    striker = None
                elif dismissed == non_striker:
                    non_striker = None
                if ev.outcome.runs % 2 == 1 and legal:
                    striker, non_striker = non_striker, striker
            else:
                if ev.outcome.runs % 2 == 1 and legal:
                    striker, non_striker = non_striker, striker

            if balls_in_over >= 6:
                striker, non_striker = non_striker, striker
                over_complete = True

            if cur_state.wickets >= 10:
                innings_complete = True
            if session.overs is not None and cur_state.overs_complete >= session.overs:
                innings_complete = True
            if cur_state.target is not None and cur_state.score >= cur_state.target:
                match_complete = True
                innings_complete = True

    session.innings = innings
    session.batting_team = batting_team
    session.bowling_team = bowling_team
    session.striker = striker
    session.non_striker = non_striker
    session.bowler = bowler
    session.current_over = current_over
    session.balls_in_over = balls_in_over
    session.match_state = cur_state.model_dump()
    session.over_complete = over_complete
    session.innings_complete = innings_complete
    session.match_complete = match_complete
    return session


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    """The chess studio is the front door; the cricket scorer lives at /cricket."""
    page = _STATIC / "chess.html"
    if page.exists():
        return FileResponse(str(page))
    return {"status": "VoxPlay running", "docs": "/docs"}


@app.get("/cricket")
async def cricket_index():
    index_html = _STATIC / "index.html"
    if index_html.exists():
        return FileResponse(str(index_html))
    return {"status": "VoxPlay web scorer running", "docs": "/docs"}


@app.post("/matches", response_model=MatchSession)
async def create_match(req: CreateMatchRequest):
    match_id = store.new_id()
    session = MatchSession(
        match_id=match_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        home_team=req.home_team,
        away_team=req.away_team,
        home_xi=req.home_xi,
        away_xi=req.away_xi,
        overs=req.overs,
        toss_winner=req.toss_winner,
        toss_choice=req.toss_choice,
    )
    return store.create(session)


@app.get("/matches", response_model=List[MatchSession])
async def list_matches():
    return store.list_all()


@app.get("/matches/{match_id}", response_model=MatchSession)
async def get_match(match_id: str):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")
    return session


@app.post("/matches/{match_id}/innings", response_model=MatchSession)
async def start_innings(match_id: str, req: StartInningsRequest):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")

    innings_num = sum(1 for e in session.events if e.get("event_type") == "innings_start") + 1

    innings_ev = InningsStartEvent(
        match_id=match_id,
        innings=innings_num,
        batting_team=req.batting_team,
        bowling_team=req.bowling_team,
        target=req.target,
        overs=session.overs,
        state=init_state(target=req.target, total_overs=session.overs),
    )
    over_ev = OverStartEvent(
        match_id=match_id,
        innings=innings_num,
        over=0,
        bowler=req.bowler,
        state=innings_ev.state,
    )

    session.events.append(innings_ev.model_dump(mode="json"))
    session.events.append(over_ev.model_dump(mode="json"))

    session.innings = innings_num
    session.batting_team = req.batting_team
    session.bowling_team = req.bowling_team
    session.striker = req.striker
    session.non_striker = req.non_striker
    session.bowler = req.bowler
    session.current_over = 0
    session.balls_in_over = 0
    session.match_state = innings_ev.state.model_dump()
    session.over_complete = False
    session.innings_complete = False

    return store.save(session)


@app.post("/matches/{match_id}/ball", response_model=MatchSession)
async def record_ball(match_id: str, req: RecordBallRequest):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")
    if session.over_complete:
        raise HTTPException(400, "Over complete — call POST /matches/{id}/over first")
    if session.innings_complete:
        raise HTTPException(400, "Innings complete")
    if session.match_complete:
        raise HTTPException(400, "Match complete")

    # Build extras
    extras = None
    if req.extras_type:
        extras = Extras(type=ExtrasType(req.extras_type), runs=req.extras_runs)

    # Build wicket
    wicket = None
    if req.dismissal_type:
        wicket = WicketInfo(
            dismissal_type=DismissalType(req.dismissal_type),
            dismissed_batsman=req.dismissed_batsman or session.striker or "",
            fielder=req.fielder,
            bowler_credited=req.bowler_credited,
        )

    outcome = Outcome(
        runs=req.runs,
        extras=extras,
        wicket=wicket,
        shot_tag=req.shot_tag,
        is_boundary=req.is_boundary,
        is_six=req.is_six,
    )

    cur_state = MatchState(**session.match_state) if session.match_state else MatchState()
    new_state = advance_state(cur_state, outcome, session.current_over, session.overs)

    legal = _is_legal(req.extras_type)
    legal_ball_num = session.balls_in_over + (1 if legal else 0)

    ev = BallEvent(
        match_id=match_id,
        innings=session.innings,
        over=session.current_over,
        ball=legal_ball_num,
        bowler=session.bowler,
        batsman_striker=session.striker,
        batsman_non_striker=session.non_striker,
        outcome=outcome,
        state=new_state,
        delivery_type=req.delivery_type,
        note=req.note,
        video_offset=req.video_offset,
    )

    session.events.append(ev.model_dump(mode="json"))
    session.match_state = new_state.model_dump()

    if legal:
        session.balls_in_over += 1

    # Strike rotation
    if wicket:
        dismissed = wicket.dismissed_batsman
        if dismissed == session.striker:
            session.striker = req.new_batsman  # may be None until frontend provides it
        elif dismissed == session.non_striker:
            session.non_striker = req.new_batsman
        # Odd runs with a wicket (e.g. run out + 1 run)
        if req.runs % 2 == 1 and legal:
            session.striker, session.non_striker = session.non_striker, session.striker
    else:
        if req.runs % 2 == 1 and legal:
            session.striker, session.non_striker = session.non_striker, session.striker

    # Over completion
    if session.balls_in_over >= 6:
        session.striker, session.non_striker = session.non_striker, session.striker
        session.over_complete = True

    # Innings / match completion
    if new_state.wickets >= 10:
        session.innings_complete = True
    if session.overs and new_state.overs_complete >= session.overs:
        session.innings_complete = True
    if new_state.target is not None and new_state.score >= new_state.target:
        session.match_complete = True
        session.innings_complete = True

    return store.save(session)


@app.delete("/matches/{match_id}/ball", response_model=MatchSession)
async def undo_ball(match_id: str):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")

    # Pop the last BallEvent (skip non-ball trailing events)
    events = session.events
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("event_type") == "ball":
            events.pop(i)
            break
    else:
        raise HTTPException(400, "No ball events to undo")

    session.events = events
    # Also trim any commentary that referenced removed events
    remaining_count = len([e for e in events if e.get("event_type") == "ball"])
    session.commentary = [c for c in session.commentary if c.get("event_index", 0) < remaining_count]

    session = _recompute(session)
    return store.save(session)


@app.post("/matches/{match_id}/over", response_model=MatchSession)
async def new_over(match_id: str, req: NewOverRequest):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")
    if not session.over_complete:
        raise HTTPException(400, "Current over is not yet complete")
    if session.innings_complete:
        raise HTTPException(400, "Innings complete")

    next_over = session.current_over + 1
    cur_state = MatchState(**session.match_state) if session.match_state else MatchState()

    ev = OverStartEvent(
        match_id=match_id,
        innings=session.innings,
        over=next_over,
        bowler=req.bowler,
        state=cur_state,
    )
    session.events.append(ev.model_dump(mode="json"))
    session.bowler = req.bowler
    session.current_over = next_over
    session.balls_in_over = 0
    session.over_complete = False

    return store.save(session)


@app.post("/matches/{match_id}/commentary", response_model=MatchSession)
async def generate_commentary(match_id: str, req: GenerateCommentaryRequest):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")

    from voxplay.commentary.engine import CommentaryEngine

    events = [_event_adapter.validate_python(e) for e in session.events]
    engine = CommentaryEngine(mock=req.mock or not os.environ.get("ANTHROPIC_API_KEY"))
    lines = list(engine.commentate(events))

    session.commentary = [
        {
            "event_index": ln.event_index,
            "event_type": ln.event_type,
            "text": ln.text,
            "audio_path": ln.audio_path,
        }
        for ln in lines
    ]
    return store.save(session)


@app.post("/matches/{match_id}/voice", response_model=MatchSession)
async def generate_voice(match_id: str, req: GenerateVoiceRequest):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")
    if not session.commentary:
        raise HTTPException(400, "Generate commentary first")

    from voxplay.tts.gtts_provider import GttsTTSProvider
    from voxplay.tts.elevenlabs_provider import ElevenLabsTTSProvider
    from voxplay.tts.mock_provider import MockTTSProvider

    audio_dir = Path(os.environ.get("VOXPLAY_DATA_DIR", "data")) / match_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    if req.provider == "elevenlabs":
        api_key = os.environ.get("ELEVENLABS_API_KEY", "")
        provider = ElevenLabsTTSProvider(api_key=api_key)
    elif req.provider == "gtts":
        provider = GttsTTSProvider()
    else:
        provider = MockTTSProvider()

    for entry in session.commentary:
        if entry.get("audio_path"):
            continue  # already synthesised
        idx = entry["event_index"]
        out_path = str(audio_dir / f"ball_{idx:04d}.mp3")
        provider.synthesize(entry["text"], out_path)
        entry["audio_path"] = out_path

    return store.save(session)


# ---------------------------------------------------------------------------
# Scoring-screen specific endpoints
# ---------------------------------------------------------------------------

class OffsetUpdate(BaseModel):
    offset: float


@app.post("/matches/{match_id}/swap-strike", response_model=MatchSession)
async def swap_strike(match_id: str):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")
    session.striker, session.non_striker = session.non_striker, session.striker
    return store.save(session)


@app.patch("/matches/{match_id}/ball/{event_index}/offset", response_model=MatchSession)
async def update_ball_offset(match_id: str, event_index: int, body: OffsetUpdate):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")
    if event_index < 0 or event_index >= len(session.events):
        raise HTTPException(404, "Event index out of range")
    ev = session.events[event_index]
    if ev.get("event_type") != "ball":
        raise HTTPException(400, "Event is not a ball")
    ev["video_offset"] = body.offset
    session.events[event_index] = ev
    return store.save(session)


@app.post("/matches/{match_id}/end-innings", response_model=MatchSession)
async def end_innings(match_id: str):
    session = store.get(match_id)
    if not session:
        raise HTTPException(404, "Match not found")
    if session.innings == 1:
        session.innings1_score = session.match_state.get("score", 0)
    session.innings_complete = True
    return store.save(session)
