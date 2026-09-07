"""
Automated quality checks for commentary transcripts.

All checks are HEURISTIC — they flag potential issues for human review.
None should be treated as a pass/fail score. False positives are expected
and acceptable; the goal is to surface patterns a human reader should look at.

Checks:
  repeated_opening    - consecutive ball lines starting with the same word(s)
  phrase_repetition   - 4-gram phrases reused within a 5-ball window
  length_violation    - ball commentary exceeding 2 sentences
  grounding_number    - numbers in commentary not traceable to event data or derived quantities
  grounding_shot      - shot descriptions when event has no shot_tag
  name_grounding      - all-caps entity names in commentary not found in match event data
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from voxplay.commentary.engine import CommentaryLine
from voxplay.events.models import (
    BallEvent,
    Event,
    EventType,
    InningsStartEvent,
    MilestoneEvent,
    OverStartEvent,
)


# ---------------------------------------------------------------------------
# Flag dataclass
# ---------------------------------------------------------------------------

@dataclass
class QualityFlag:
    check: str            # e.g. "repeated_opening"
    event_index: int      # 0-based index into the event/line list
    event_type: str       # e.g. "ball"
    message: str          # human-readable description of the issue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sentences(text: str) -> list[str]:
    """Split text into non-empty sentences by terminal punctuation."""
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def _opening(text: str, words: int = 2) -> str:
    """Return the first N words of text, lowercased, stripped of punctuation."""
    raw = " ".join(text.split()[:words])
    return re.sub(r"[^\w\s]", "", raw).lower().strip()


def _ngrams(text: str, n: int = 4) -> set[str]:
    """Return all n-grams of content words from text."""
    tokens = re.findall(r"\b[a-z]{3,}\b", text.lower())
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


# Cricket-playing nations — used to catch hallucinated country/team names that
# appear in title-case (where the all-caps heuristic cannot apply).
_CRICKET_COUNTRIES: frozenset[str] = frozenset({
    "India", "Pakistan", "England", "Australia", "Bangladesh",
    "Zimbabwe", "Afghanistan", "Ireland",
    "South Africa", "New Zealand", "West Indies", "Sri Lanka",
})

# Context filter: "push(ed) [optional words] to [fielding position]" describes
# direction of a defensive push, NOT a named shot.  Applied only to push/pushed.
_PUSH_TO_FIELDER = re.compile(
    r'\bpush(?:ed)?\b[^.!?]{0,50}\bto\s+(?:the\s+)?'
    r'(?:mid[-\s]?on|mid[-\s]?off|mid[-\s]?wicket|'
    r'covers?|point|slips?|gully|fine\s+leg|square\s+leg|'
    r'third\s+man|long[-\s]?on|long[-\s]?off|'
    r'deep(?:\s+\w+)?|short(?:\s+\w+)?|backward\s+\w+|bowler)',
    re.IGNORECASE,
)

# Phrases so universal in cricket commentary they should not flag as repetition
_FILLER_NGRAMS: set[str] = {
    "back to the bowler",
    "into the crowd",
    "into the stands",
    "that is out of",
    "through the off side",
    "through the covers for",
    "down the ground for",
}


def _is_filler(phrase: str) -> bool:
    return phrase in _FILLER_NGRAMS


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_repeated_openings(
    lines: list[CommentaryLine],
    events: list[Event],
    window: int = 4,
) -> list[QualityFlag]:
    """
    Flag ball events whose first 2 words match a recent ball event's opening.
    Window: how many preceding ball lines to compare against.
    """
    flags: list[QualityFlag] = []
    # work only on ball lines
    ball_pairs = [
        (line, ev)
        for line, ev in zip(lines, events)
        if line.event_type == EventType.BALL.value
    ]

    recent: list[tuple[str, int]] = []  # (opening, event_index)

    for line, ev in ball_pairs:
        op = _opening(line.text, 2)
        for prev_op, prev_idx in recent:
            if op == prev_op and op:
                flags.append(QualityFlag(
                    check="repeated_opening",
                    event_index=line.event_index,
                    event_type=line.event_type,
                    message=(
                        f'Opens with "{op}" -- same as event #{prev_idx + 1}. '
                        f'Vary sentence starts to avoid metronome rhythm.'
                    ),
                ))
                break
        recent.append((op, line.event_index))
        if len(recent) > window:
            recent.pop(0)

    return flags


def check_phrase_repetition(
    lines: list[CommentaryLine],
    events: list[Event],
    window: int = 5,
    ngram_size: int = 4,
) -> list[QualityFlag]:
    """
    Flag ball events that share a 4-gram phrase with a recent ball event.
    Ignores known filler phrases that appear universally in cricket commentary.
    """
    flags: list[QualityFlag] = []
    ball_pairs = [
        (line, ev)
        for line, ev in zip(lines, events)
        if line.event_type == EventType.BALL.value
    ]

    recent: list[tuple[set[str], int]] = []  # (ngrams, event_index)

    for line, ev in ball_pairs:
        current = _ngrams(line.text, ngram_size)
        for prev_ngrams, prev_idx in recent:
            shared = {p for p in (current & prev_ngrams) if not _is_filler(p)}
            if shared:
                example = sorted(shared)[0]
                flags.append(QualityFlag(
                    check="phrase_repetition",
                    event_index=line.event_index,
                    event_type=line.event_type,
                    message=(
                        f'Phrase "{example}" also appears in event #{prev_idx + 1}. '
                        f'Repetition within {window} balls breaks commentary flow.'
                    ),
                ))
                break
        recent.append((current, line.event_index))
        if len(recent) > window:
            recent.pop(0)

    return flags


def check_length_violations(
    lines: list[CommentaryLine],
    events: list[Event],
    max_sentences: int = 2,
) -> list[QualityFlag]:
    """Flag ball commentary with more than max_sentences sentences."""
    flags: list[QualityFlag] = []
    for line, ev in zip(lines, events):
        if line.event_type != EventType.BALL.value:
            continue
        sents = _sentences(line.text)
        if len(sents) > max_sentences:
            flags.append(QualityFlag(
                check="length_violation",
                event_index=line.event_index,
                event_type=line.event_type,
                message=(
                    f"{len(sents)} sentences detected (max {max_sentences} for ball events). "
                    f'First excess: "{sents[max_sentences][:60]}..."'
                ),
            ))
    return flags


def check_factual_grounding(
    lines: list[CommentaryLine],
    events: list[Event],
) -> list[QualityFlag]:
    """
    Heuristic: flag commentary that mentions numbers or shots not traceable
    to the event data. Intended to catch hallucinated stats or invented details.

    Two sub-checks:
      grounding_number - a number appears in commentary that isn't in the event
      grounding_shot   - a shot-type word appears when event has no shot_tag
    """
    flags: list[QualityFlag] = []

    # Numbers so small/common they appear legitimately in any commentary
    _COMMON_NUMS = {"0", "1", "2", "3", "4", "5", "6", "50", "100"}

    # Shot words we scan for. "cover" removed (ambiguous fielding position).
    # "push/pushed" re-added but filtered below via _PUSH_TO_FIELDER context check.
    _SHOT_WORDS = {
        "drive", "driven", "pull", "pulled", "sweep", "swept",
        "cut", "flick", "flicked", "glance", "hook", "hooked", "slog",
        "ramp", "scoop", "nudge", "nudged", "dab",
        "push", "pushed",
    }

    for line, ev in zip(lines, events):
        if not isinstance(ev, BallEvent):
            continue

        # --- Number check ---
        known_nums: set[str] = set()
        s = ev.state
        for val in [
            s.score, s.wickets, s.overs_complete, s.balls_in_over,
            ev.outcome.runs, ev.over + 1, ev.ball,
            s.balls_remaining, s.target, s.striker_runs,
            s.striker_balls, s.partnership_runs,
        ]:
            if val is not None:
                known_nums.add(str(val))
        if s.required_run_rate is not None:
            known_nums.add(str(int(s.required_run_rate)))
        if s.current_run_rate is not None:
            known_nums.add(str(int(s.current_run_rate)))
        if ev.outcome.extras:
            known_nums.add(str(ev.outcome.extras.runs))
        # Derived cricket quantities — accept values the model can correctly
        # compute from the raw fields (e.g. "52 needed off 33 balls" where
        # 52 = target - score is correct cricket math).
        if s.target is not None:
            runs_needed = s.target - s.score
            if runs_needed >= 0:
                known_nums.add(str(runs_needed))
        if s.balls_remaining is not None:
            known_nums.add(str(s.balls_remaining // 6))   # full overs remaining

        text_nums = set(re.findall(r"\b\d+\b", line.text))
        mystery = text_nums - known_nums - _COMMON_NUMS
        if mystery:
            flags.append(QualityFlag(
                check="grounding_number",
                event_index=line.event_index,
                event_type=line.event_type,
                message=(
                    f"Number(s) {sorted(mystery)} in commentary not found in event data. "
                    f"Verify: score={s.score}, wkts={s.wickets}, over={ev.over+1}.{ev.ball}."
                ),
            ))

        # --- Shot check ---
        text_lower = line.text.lower()
        mentioned_shots = _SHOT_WORDS & set(re.findall(r"\b\w+\b", text_lower))
        # push/pushed used as direction ("pushed to mid-on") is NOT an invented shot
        if ("push" in mentioned_shots or "pushed" in mentioned_shots) \
                and _PUSH_TO_FIELDER.search(line.text):
            mentioned_shots.discard("push")
            mentioned_shots.discard("pushed")
        has_shot_tag = ev.outcome.shot_tag is not None
        is_wicket = ev.outcome.wicket is not None

        if mentioned_shots and not has_shot_tag and not is_wicket:
            flags.append(QualityFlag(
                check="grounding_shot",
                event_index=line.event_index,
                event_type=line.event_type,
                message=(
                    f"Shot word(s) {sorted(mentioned_shots)} in commentary "
                    f"but event has no shot_tag. "
                    f"Commentary may be inventing a shot not recorded in the data."
                ),
            ))

    return flags


# ---------------------------------------------------------------------------
# check_name_grounding
# ---------------------------------------------------------------------------

def _collect_team_names(events: list[Event]) -> set[str]:
    """Return lowercased full team name strings from InningsStartEvent."""
    teams: set[str] = set()
    for ev in events:
        if isinstance(ev, InningsStartEvent):
            if ev.batting_team:
                teams.add(ev.batting_team.lower())
            if ev.bowling_team:
                teams.add(ev.bowling_team.lower())
    return teams


def check_name_grounding(
    lines: list[CommentaryLine],
    events: list[Event],
) -> list[QualityFlag]:
    """
    Flag ball commentary that mentions a real-world cricket nation not present
    as a team in the current match.

    Uses a curated closed list of international cricket teams/nations
    (_CRICKET_COUNTRIES).  Matching is case-insensitive, so it catches
    "INDIA WIN", "South Africa win", and "as India build momentum" alike.
    All-caps emphasis words (COMPLETE, GAME, LAUNCHES, etc.) never appear in
    _CRICKET_COUNTRIES and therefore never fire — no whitelist needed.
    """
    known_teams_lower = _collect_team_names(events)
    flags: list[QualityFlag] = []

    for line, ev in zip(lines, events):
        if not isinstance(ev, BallEvent):
            continue

        text_lower = line.text.lower()
        hits: set[str] = set()
        for country in _CRICKET_COUNTRIES:
            cl = country.lower()
            if cl in known_teams_lower:
                continue  # it's one of the actual match teams
            words = cl.split()
            pat = r'\b' + r'\s+'.join(re.escape(w) for w in words) + r'\b'
            if re.search(pat, text_lower):
                hits.add(country)

        if hits:
            flags.append(QualityFlag(
                check="name_grounding",
                event_index=line.event_index,
                event_type=line.event_type,
                message=(
                    f"Real-world cricket nation(s) {sorted(hits)} mentioned in commentary "
                    f"but not a team in this match. Possible hallucinated team reference."
                ),
            ))

    return flags


# ---------------------------------------------------------------------------
# Combined runner
# ---------------------------------------------------------------------------

def run_all_checks(
    lines: list[CommentaryLine],
    events: list[Event],
) -> list[QualityFlag]:
    """Run all quality checks and return flags sorted by event index."""
    flags: list[QualityFlag] = []
    flags.extend(check_repeated_openings(lines, events))
    flags.extend(check_phrase_repetition(lines, events))
    flags.extend(check_length_violations(lines, events))
    flags.extend(check_factual_grounding(lines, events))
    flags.extend(check_name_grounding(lines, events))
    return sorted(flags, key=lambda f: (f.event_index, f.check))
