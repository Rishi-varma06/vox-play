"""
CSV scorecard parser.

Expected CSV columns
--------------------
Required:
  match_id, innings, over, ball, runs

Optional (leave blank to omit):
  bowler, batsman_striker, batsman_non_striker,
  extras_type   (wide | no_ball | bye | leg_bye | penalty)
  extras_runs   (integer, defaults to 1 if extras_type set)
  dismissal_type (bowled | caught | lbw | run_out | stumped | hit_wicket | ...)
  dismissed_batsman
  fielder
  shot_tag
  is_boundary   (1 / true / yes)
  is_six        (1 / true / yes)
  timestamp     (ISO-8601 or blank)
  tags          (pipe-separated, e.g. "key_moment|pressure_over")

State columns (all optional — if absent, state defaults to zero):
  score, wickets, overs_complete, balls_in_over, balls_remaining,
  target, striker_runs, striker_balls, partnership_runs,
  required_run_rate, current_run_rate

The parser yields BallEvents only; if you need InningsStart/OverStart events
in the stream, add them manually or use JSON format.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from voxplay.events.models import (
    BallEvent,
    DismissalType,
    Event,
    Extras,
    ExtrasType,
    MatchState,
    Outcome,
    WicketInfo,
)
from .base import EventSource, IngestionError

_TRUTHY = {"1", "true", "yes", "y"}


def _bool(val: str) -> bool:
    return val.strip().lower() in _TRUTHY


def _opt(val: str) -> str | None:
    stripped = val.strip()
    return stripped if stripped else None


def _float_opt(val: str) -> float | None:
    stripped = val.strip()
    try:
        return float(stripped) if stripped else None
    except ValueError:
        return None


def _int_opt(val: str) -> int | None:
    stripped = val.strip()
    try:
        return int(stripped) if stripped else None
    except ValueError:
        return None


def _parse_row(row: dict[str, str], lineno: int) -> BallEvent:
    try:
        match_id = row["match_id"].strip()
        innings = int(row["innings"])
        over = int(row["over"])
        ball = int(row["ball"])
        runs = int(row["runs"])
    except (KeyError, ValueError) as exc:
        raise IngestionError(f"Line {lineno}: missing required field — {exc}") from exc

    # Extras
    extras: Extras | None = None
    extras_type_raw = _opt(row.get("extras_type", ""))
    if extras_type_raw:
        try:
            et = ExtrasType(extras_type_raw.lower())
        except ValueError:
            raise IngestionError(f"Line {lineno}: unknown extras_type '{extras_type_raw}'")
        extras_runs = _int_opt(row.get("extras_runs", "")) or 1
        extras = Extras(type=et, runs=extras_runs)

    # Wicket
    wicket: WicketInfo | None = None
    dismissal_raw = _opt(row.get("dismissal_type", ""))
    if dismissal_raw:
        try:
            dt = DismissalType(dismissal_raw.lower())
        except ValueError:
            raise IngestionError(f"Line {lineno}: unknown dismissal_type '{dismissal_raw}'")
        dismissed = _opt(row.get("dismissed_batsman", "")) or _opt(row.get("batsman_striker", "")) or "unknown"
        wicket = WicketInfo(
            dismissal_type=dt,
            dismissed_batsman=dismissed,
            fielder=_opt(row.get("fielder", "")),
        )

    outcome = Outcome(
        runs=runs,
        extras=extras,
        wicket=wicket,
        shot_tag=_opt(row.get("shot_tag", "")),
        is_boundary=_bool(row.get("is_boundary", "")),
        is_six=_bool(row.get("is_six", "")),
    )

    state = MatchState(
        score=_int_opt(row.get("score", "")) or 0,
        wickets=_int_opt(row.get("wickets", "")) or 0,
        overs_complete=_int_opt(row.get("overs_complete", "")) or 0,
        balls_in_over=_int_opt(row.get("balls_in_over", "")) or 0,
        target=_int_opt(row.get("target", "")),
        balls_remaining=_int_opt(row.get("balls_remaining", "")),
        striker_runs=_int_opt(row.get("striker_runs", "")) or 0,
        striker_balls=_int_opt(row.get("striker_balls", "")) or 0,
        partnership_runs=_int_opt(row.get("partnership_runs", "")) or 0,
        required_run_rate=_float_opt(row.get("required_run_rate", "")),
        current_run_rate=_float_opt(row.get("current_run_rate", "")),
    )

    tags_raw = _opt(row.get("tags", ""))
    tags = [t.strip() for t in tags_raw.split("|") if t.strip()] if tags_raw else []

    return BallEvent(
        match_id=match_id,
        innings=innings,
        over=over,
        ball=ball,
        bowler=_opt(row.get("bowler", "")),
        batsman_striker=_opt(row.get("batsman_striker", "")),
        batsman_non_striker=_opt(row.get("batsman_non_striker", "")),
        outcome=outcome,
        state=state,
        tags=tags,
    )


class CSVEventSource(EventSource):
    """Parse a ball-by-ball CSV into BallEvents."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def produce(self) -> Iterator[Event]:
        if not self.path.exists():
            raise IngestionError(f"File not found: {self.path}")
        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for lineno, row in enumerate(reader, start=2):
                yield _parse_row(row, lineno)
