"""JSON scorecard / event-stream parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from pydantic import TypeAdapter

from voxplay.events.models import Event
from .base import EventSource, IngestionError

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


class JSONEventSource(EventSource):
    """
    Load a VoxPlay JSON event file.

    Accepted formats
    ----------------
    1. A JSON array of event objects:
         [{"event_type": "ball", ...}, ...]

    2. A wrapper object with an "events" key (as produced by the demo
       data file):
         {"match_id": "...", "events": [...]}
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def produce(self) -> Iterator[Event]:
        if not self.path.exists():
            raise IngestionError(f"File not found: {self.path}")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise IngestionError(f"Invalid JSON in {self.path}: {exc}") from exc

        records: list[dict]
        if isinstance(raw, list):
            records = raw
        elif isinstance(raw, dict) and "events" in raw:
            records = raw["events"]
        else:
            raise IngestionError(
                f"{self.path} must be a JSON array or an object with an 'events' key"
            )

        for i, record in enumerate(records):
            try:
                yield _adapter.validate_python(record)
            except Exception as exc:
                raise IngestionError(
                    f"Event #{i} in {self.path} failed validation: {exc}"
                ) from exc
