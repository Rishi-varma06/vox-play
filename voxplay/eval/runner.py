"""
Evaluation runner: loads a match, generates commentary, applies quality checks,
and writes a human-readable side-by-side transcript.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from voxplay.commentary.engine import CommentaryEngine, CommentaryLine
from voxplay.events.models import (
    BallEvent,
    DrinksEvent,
    Event,
    EventType,
    InningsStartEvent,
    MilestoneEvent,
    OverStartEvent,
)
from voxplay.ingest.json_parser import JSONEventSource
from .checks import QualityFlag, run_all_checks


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TranscriptLine:
    event_index: int
    event_type: str
    event_summary: str    # one-line human-readable description of the event
    state_line: str       # compact state string (score/wkts, overs)
    commentary: str
    flags: list[QualityFlag] = field(default_factory=list)


@dataclass
class MatchEvalResult:
    match_id: str
    description: str
    transcript_lines: list[TranscriptLine]
    all_flags: list[QualityFlag]
    event_count: int
    ball_event_count: int
    mock: bool
    transcript_path: Path | None = None

    def flag_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.all_flags:
            counts[f.check] = counts.get(f.check, 0) + 1
        return counts

    def flag_rate(self, check: str) -> float:
        """Fraction of ball events flagged for *check*."""
        if not self.ball_event_count:
            return 0.0
        return self.flag_counts().get(check, 0) / self.ball_event_count


# ---------------------------------------------------------------------------
# Event summariser
# ---------------------------------------------------------------------------

def _event_summary(ev: Event) -> tuple[str, str]:
    """Return (one-line summary, compact state) for an event."""
    if isinstance(ev, BallEvent):
        bowler = ev.bowler or "?"
        striker = ev.batsman_striker or "?"
        over_label = f"{ev.over + 1}.{ev.ball}"
        if ev.outcome.wicket:
            wt = ev.outcome.wicket.dismissal_type.value
            dismissed = ev.outcome.wicket.dismissed_batsman
            fielder = f" c {ev.outcome.wicket.fielder}" if ev.outcome.wicket.fielder else ""
            result = f"WICKET -- {dismissed} {wt}{fielder}"
        elif ev.outcome.is_six:
            result = "SIX"
            if ev.outcome.shot_tag:
                result += f" [{ev.outcome.shot_tag.replace('_', ' ')}]"
        elif ev.outcome.is_boundary:
            result = "FOUR"
            if ev.outcome.shot_tag:
                result += f" [{ev.outcome.shot_tag.replace('_', ' ')}]"
        elif ev.outcome.extras:
            result = f"{ev.outcome.extras.type.value.upper()} +{ev.outcome.extras.runs}"
        else:
            runs = ev.outcome.runs
            result = f"{runs} run{'s' if runs != 1 else ''}" if runs else "dot"
            if ev.outcome.shot_tag:
                result += f" [{ev.outcome.shot_tag.replace('_', ' ')}]"
        summary = f"BALL {over_label}  {bowler} -> {striker}  |  {result}"
        s = ev.state
        state_str = f"Score {s.score}/{s.wickets}  ({s.overs_complete}.{s.balls_in_over} ov)"
        if s.target:
            needed = s.target - s.score
            state_str += f"  need {needed} off {s.balls_remaining}"
        if s.required_run_rate:
            state_str += f"  RRR {s.required_run_rate:.1f}"
        return summary, state_str

    elif isinstance(ev, InningsStartEvent):
        batting = ev.batting_team or "?"
        bowling = ev.bowling_team or "?"
        target = f" chasing {ev.target}" if ev.target else ""
        overs = f" ({ev.overs} overs)" if ev.overs else ""
        return (
            f"INNINGS {ev.innings} START -- {batting} bat{target} vs {bowling}{overs}",
            f"Score 0/0  (0.0 ov)",
        )
    elif isinstance(ev, OverStartEvent):
        bowler = ev.bowler or "?"
        s = ev.state
        state_str = f"Score {s.score}/{s.wickets}  ({s.overs_complete}.{s.balls_in_over} ov)"
        if s.target:
            state_str += f"  need {s.target - s.score} off {s.balls_remaining}"
        return f"OVER {ev.over + 1} START -- {bowler} bowls", state_str
    elif isinstance(ev, MilestoneEvent):
        return f"MILESTONE -- {ev.description}", f"Score {ev.state.score}/{ev.state.wickets}"
    elif isinstance(ev, DrinksEvent):
        s = ev.state
        return f"DRINKS (over {ev.over})", f"Score {s.score}/{s.wickets}  ({s.overs_complete}.{s.balls_in_over} ov)"
    return f"[{ev.event_type.value}]", ""


# ---------------------------------------------------------------------------
# Transcript writer
# ---------------------------------------------------------------------------

_LINE_WIDTH = 80
_SEP = "=" * _LINE_WIDTH
_SEP_THIN = "-" * _LINE_WIDTH


def _wrap(text: str, prefix: str = "      ") -> str:
    return textwrap.fill(
        text,
        width=_LINE_WIDTH,
        initial_indent=prefix,
        subsequent_indent=prefix,
    )


def _write_transcript(
    result: MatchEvalResult,
    path: Path,
) -> None:
    mode = "mock (offline)" if result.mock else "live Anthropic API"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    out: list[str] = []
    out.append(_SEP)
    out.append(f"MATCH:     {result.match_id}")
    out.append(f"           {result.description}")
    out.append(f"Generated: {ts}  |  Mode: {mode}")
    out.append(f"Events:    {result.event_count}  |  Ball events: {result.ball_event_count}")
    out.append(_SEP)
    out.append("")

    for t in result.transcript_lines:
        num = f"[{t.event_index + 1:03d}]"
        out.append(f"{num} {t.event_summary}")
        if t.state_line:
            out.append(f"       {t.state_line}")
        out.append(_wrap(f'"{t.commentary}"'))
        for f in t.flags:
            out.append(f"       >> WARN [{f.check}]: {f.message}")
        out.append("")

    # Summary section
    out.append(_SEP)
    out.append("QUALITY FLAGS  (heuristic warnings for human review -- not pass/fail)")
    out.append(_SEP_THIN)

    counts = result.flag_counts()
    n_ball = result.ball_event_count

    check_labels = {
        "repeated_opening": "Repeated sentence openings",
        "phrase_repetition": "Repeated 4-gram phrases",
        "length_violation":  "Length violations (>2 sentences)",
        "grounding_number":  "Ungrounded numbers",
        "grounding_shot":    "Ungrounded shot descriptions",
        "name_grounding":    "Hallucinated entity names",
    }

    for check, label in check_labels.items():
        count = counts.get(check, 0)
        pct = (count / n_ball * 100) if n_ball else 0.0
        bar_len = int(pct / 5)  # 5% per char, max 20
        bar = "#" * bar_len + "." * (20 - bar_len)
        out.append(f"  {label:<35} {count:3d}  [{bar}] {pct:5.1f}% of ball events")

    total = sum(counts.values())
    out.append(_SEP_THIN)
    out.append(f"  Total flags: {total}  across {n_ball} ball events")
    out.append("")
    out.append("  These are heuristic warnings for human review, not a score.")
    out.append("  False positives are expected -- read the transcript to judge quality.")
    out.append(_SEP)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_match_eval(
    source_path: Path,
    output_dir: Path,
    mock: bool = True,
) -> MatchEvalResult:
    """
    Load *source_path*, generate commentary (mock or live), run quality checks,
    write a readable transcript to *output_dir*, and return the result.
    """
    # Load events
    source = JSONEventSource(source_path)
    events = list(source.produce())

    raw = json.loads(source_path.read_text(encoding="utf-8"))
    description = raw.get("description", source_path.stem)
    match_id = raw.get("match_id", source_path.stem)

    # Generate commentary
    engine = CommentaryEngine(mock=mock)
    lines = list(engine.commentate(events))

    # Run quality checks
    flags = run_all_checks(lines, events)

    # Map flags by event index
    flags_by_idx: dict[int, list[QualityFlag]] = {}
    for f in flags:
        flags_by_idx.setdefault(f.event_index, []).append(f)

    # Build transcript lines
    transcript: list[TranscriptLine] = []
    for line, ev in zip(lines, events):
        summary, state_str = _event_summary(ev)
        transcript.append(TranscriptLine(
            event_index=line.event_index,
            event_type=line.event_type,
            event_summary=summary,
            state_line=state_str,
            commentary=line.text,
            flags=flags_by_idx.get(line.event_index, []),
        ))

    ball_count = sum(1 for e in events if e.event_type == EventType.BALL)

    result = MatchEvalResult(
        match_id=match_id,
        description=description,
        transcript_lines=transcript,
        all_flags=flags,
        event_count=len(events),
        ball_event_count=ball_count,
        mock=mock,
    )

    # Write transcript file
    transcript_path = output_dir / f"{source_path.stem}_eval.txt"
    _write_transcript(result, transcript_path)
    result.transcript_path = transcript_path

    return result
