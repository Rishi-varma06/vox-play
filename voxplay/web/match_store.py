"""JSON-file-backed match store for the web scorer."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

DATA_DIR = Path(os.environ.get("VOXPLAY_DATA_DIR", "data"))
MATCHES_FILE = DATA_DIR / "matches.json"


class MatchSession(BaseModel):
    match_id: str
    created_at: str = ""

    # Setup
    home_team: str
    away_team: str
    home_xi: List[str] = Field(default_factory=list)
    away_xi: List[str] = Field(default_factory=list)
    overs: Optional[int] = None
    toss_winner: str
    toss_choice: str  # "bat" or "bowl"

    # All events, serialised as plain dicts (event_type discriminator intact)
    events: List[dict] = Field(default_factory=list)

    # Cached live session state (rebuilt from events on undo)
    innings: int = 1
    batting_team: str = ""
    bowling_team: str = ""
    striker: Optional[str] = None
    non_striker: Optional[str] = None
    bowler: Optional[str] = None
    current_over: int = 0          # 0-indexed over number
    balls_in_over: int = 0         # legal deliveries so far in current over
    match_state: dict = Field(default_factory=dict)
    over_complete: bool = False    # True after 6th legal ball until /over called
    innings_complete: bool = False
    match_complete: bool = False
    innings1_score: Optional[int] = None  # stored at end of innings 1 for target computation

    # Commentary (list of {event_index, event_type, text, audio_path})
    commentary: List[dict] = Field(default_factory=list)


class MatchStore:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not MATCHES_FILE.exists():
            MATCHES_FILE.write_text("{}", encoding="utf-8")

    def _load(self) -> Dict[str, dict]:
        try:
            return json.loads(MATCHES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _persist(self, data: Dict[str, dict]) -> None:
        MATCHES_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def new_id(self) -> str:
        return f"match_{uuid.uuid4().hex[:8]}"

    def create(self, session: MatchSession) -> MatchSession:
        data = self._load()
        data[session.match_id] = session.model_dump()
        self._persist(data)
        return session

    def get(self, match_id: str) -> Optional[MatchSession]:
        raw = self._load().get(match_id)
        return MatchSession(**raw) if raw else None

    def save(self, session: MatchSession) -> MatchSession:
        data = self._load()
        data[session.match_id] = session.model_dump()
        self._persist(data)
        return session

    def list_all(self) -> List[MatchSession]:
        return [MatchSession(**v) for v in self._load().values()]
