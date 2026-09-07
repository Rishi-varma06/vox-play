"""
Interactive terminal scorer.

Ball-by-ball CLI loop for live match scoring. Each delivery prompts for:
  - runs (0-6, or w=wide, n=no_ball, b=bye, l=leg_bye)
  - wicket (optional, triggered by 'W' run key)
  - shot tag (optional free text)

Special commands:
  u / undo   — remove last ball
  s / save   — save event stream and exit
  q / quit   — quit without saving
  ?          — show help

Match state is computed automatically after each ball via advance_state().
"""

from __future__ import annotations

import copy
import sys
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from voxplay.events.models import (
    BallEvent,
    DismissalType,
    DrinksEvent,
    Event,
    Extras,
    ExtrasType,
    InningsStartEvent,
    MatchState,
    MilestoneEvent,
    Outcome,
    OverStartEvent,
    WicketInfo,
)
from voxplay.events.state import advance_state, init_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = _input(f"  {prompt}{suffix}: ")
    return val if val else default


def _fmt_state(s: MatchState, innings: int) -> str:
    score_str = f"{s.score}/{s.wickets}"
    over_str = f"{s.overs_complete}.{s.balls_in_over}"
    parts = [f"Innings {innings}  {score_str}  ({over_str} ov)"]
    if s.target:
        needed = s.target - s.score
        parts.append(f"  need {needed} off {s.balls_remaining} balls")
    if s.required_run_rate:
        parts.append(f"  RRR {s.required_run_rate:.2f}")
    if s.current_run_rate:
        parts.append(f"  CRR {s.current_run_rate:.2f}")
    return "".join(parts)


_DISMISSAL_SHORTCUTS = {
    "b": DismissalType.BOWLED,
    "c": DismissalType.CAUGHT,
    "l": DismissalType.LBW,
    "r": DismissalType.RUN_OUT,
    "s": DismissalType.STUMPED,
    "h": DismissalType.HIT_WICKET,
    "bowled": DismissalType.BOWLED,
    "caught": DismissalType.CAUGHT,
    "lbw": DismissalType.LBW,
    "run_out": DismissalType.RUN_OUT,
    "stumped": DismissalType.STUMPED,
    "hit_wicket": DismissalType.HIT_WICKET,
}

_EXTRAS_SHORTCUTS = {
    "w": ExtrasType.WIDE,
    "n": ExtrasType.NO_BALL,
    "b": ExtrasType.BYE,
    "l": ExtrasType.LEG_BYE,
    "wide": ExtrasType.WIDE,
    "no_ball": ExtrasType.NO_BALL,
    "bye": ExtrasType.BYE,
    "leg_bye": ExtrasType.LEG_BYE,
}

_HELP = """
Commands
--------
  0-6         Runs scored off the bat
  w           Wide (+1, ball not counted)
  n           No-ball (+1, ball not counted)
  b <runs>    Byes (e.g. "b 4")
  l <runs>    Leg byes
  W           Wicket (will prompt for details)
  u / undo    Remove the last ball
  d           Add drinks break event
  s / save    Save and finish
  q / quit    Quit without saving
  ?           Show this help
"""


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _setup_innings(innings_num: int) -> Tuple[str, str, Optional[int], Optional[int], str, str]:
    """Interactive setup for a new innings. Returns (batting, bowling, target, overs, bowler, striker)."""
    _print(f"\n=== Innings {innings_num} setup ===")
    batting = _ask("Batting team", "Team A")
    bowling = _ask("Bowling team", "Team B")
    target_str = _ask("Target (leave blank for first innings)", "")
    target = int(target_str) if target_str.isdigit() else None
    overs_str = _ask("Max overs (leave blank for unlimited)", "20")
    overs = int(overs_str) if overs_str.isdigit() else None
    bowler = _ask("Opening bowler", "Bowler")
    striker = _ask("Opening batsman (striker)", "Batsman A")
    non_striker = _ask("Opening batsman (non-striker)", "Batsman B")
    return batting, bowling, target, overs, bowler, striker, non_striker


# ---------------------------------------------------------------------------
# Main scorer class
# ---------------------------------------------------------------------------

class InteractiveScorer:
    """
    Manages the live scoring session and produces an Event stream.

    Usage:
        scorer = InteractiveScorer(match_id="my_match")
        scorer.run()
        events = scorer.events
    """

    def __init__(self, match_id: Optional[str] = None) -> None:
        self.match_id = match_id or f"match_{uuid.uuid4().hex[:8]}"
        self.events: List[Event] = []
        self._state = init_state()
        self._innings = 1
        self._over = 0
        self._ball = 0          # balls bowled in current over (legal deliveries)
        self._bowler: Optional[str] = None
        self._striker: Optional[str] = None
        self._non_striker: Optional[str] = None
        self._total_overs: Optional[int] = None
        self._undo_stack: List[Tuple[List[Event], MatchState, int, int, int]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> List[Event]:
        """Launch the interactive scoring loop. Returns the completed event list."""
        _print("\n+==================================+")
        _print("|   VoxPlay Interactive Scorer     |")
        _print("+==================================+")
        _print('Type ? for help, "s" to save, "q" to quit.\n')

        match_id_input = _ask("Match ID", self.match_id)
        if match_id_input:
            self.match_id = match_id_input

        innings_count_str = _ask("How many innings to score?", "2")
        total_innings = int(innings_count_str) if innings_count_str.isdigit() else 2

        for inn_num in range(1, total_innings + 1):
            if not self._setup_new_innings(inn_num):
                break
            done = self._score_innings()
            if done == "quit":
                _print("\nQuitting without saving.")
                return self.events

        return self.events

    # ------------------------------------------------------------------
    # Innings management
    # ------------------------------------------------------------------

    def _setup_new_innings(self, inn_num: int) -> bool:
        result = _setup_innings(inn_num)
        batting, bowling, target, overs, bowler, striker, non_striker = result
        self._innings = inn_num
        self._over = 0
        self._ball = 0
        self._bowler = bowler
        self._striker = striker
        self._non_striker = non_striker
        self._total_overs = overs
        self._state = init_state(target=target, total_overs=overs)

        innings_ev = InningsStartEvent(
            match_id=self.match_id,
            innings=inn_num,
            batting_team=batting,
            bowling_team=bowling,
            target=target,
            overs=overs,
            state=copy.deepcopy(self._state),
            timestamp=datetime.now(timezone.utc),
        )
        self.events.append(innings_ev)
        self._emit_over_start()
        return True

    def _emit_over_start(self) -> None:
        ev = OverStartEvent(
            match_id=self.match_id,
            innings=self._innings,
            over=self._over,
            bowler=self._bowler,
            state=copy.deepcopy(self._state),
            timestamp=datetime.now(timezone.utc),
        )
        self.events.append(ev)

    # ------------------------------------------------------------------
    # Scoring loop
    # ------------------------------------------------------------------

    def _score_innings(self) -> str:
        """Score balls until innings end. Returns "done" or "quit"."""
        while True:
            _print(f"\n  {_fmt_state(self._state, self._innings)}")
            ball_label = f"Over {self._over + 1}.{self._ball + 1}"
            _print(f"  {ball_label}  {self._bowler or '?'} → {self._striker or '?'}")

            raw = _input("  > ").lower().strip()

            if not raw:
                continue

            if raw in ("?", "help"):
                _print(_HELP)
                continue

            if raw in ("s", "save"):
                _print("\nSaved. Exiting scorer.")
                return "done"

            if raw in ("q", "quit"):
                return "quit"

            if raw in ("u", "undo"):
                self._undo()
                continue

            if raw == "d":
                self._add_drinks()
                continue

            result = self._parse_input(raw)
            if result is None:
                _print("  ✗  Unrecognised input. Type ? for help.")
                continue

            outcome = result
            self._record_ball(outcome)

            # Innings over?
            if self._state.wickets >= 10:
                _print("\n  All out!")
                return "done"
            if self._total_overs and self._state.overs_complete >= self._total_overs:
                _print(f"\n  {self._total_overs} overs complete.")
                return "done"
            if self._state.target and self._state.score >= self._state.target:
                _print(f"\n  Target reached! {self._state.score}/{self._state.wickets}")
                return "done"

        return "done"

    # ------------------------------------------------------------------
    # Input parsing
    # ------------------------------------------------------------------

    def _parse_input(self, raw: str) -> Optional[Outcome]:
        parts = raw.split()
        first = parts[0]

        # Extras
        if first in _EXTRAS_SHORTCUTS:
            extras_type = _EXTRAS_SHORTCUTS[first]
            runs = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            return Outcome(runs=0, extras=Extras(type=extras_type, runs=runs))

        # Wicket
        if first == "w" and len(parts) > 1 and not parts[1].isdigit():
            # "w" alone = wide; "w bowled" etc would be weird, handle below
            pass

        if first in ("wicket", "wkt", "out") or (first == "w" and len(parts) == 1 and False):
            return self._prompt_wicket(runs=0)

        # Capital W in raw input (user typed W)
        if raw.startswith("W") or raw.upper() == "W":
            runs_str = parts[1] if len(parts) > 1 and parts[1].isdigit() else "0"
            return self._prompt_wicket(runs=int(runs_str))

        # Numeric runs (0-6)
        if first.isdigit():
            runs = int(first)
            if runs > 6:
                _print("  ✗  Maximum runs per ball is 6.")
                return None
            is_boundary = runs == 4
            is_six = runs == 6
            shot_tag = None
            if len(parts) > 1:
                shot_tag = "_".join(parts[1:])
            outcome = Outcome(
                runs=runs,
                is_boundary=is_boundary,
                is_six=is_six,
                shot_tag=shot_tag,
            )
            return outcome

        return None

    def _prompt_wicket(self, runs: int = 0) -> Outcome:
        _print("  Wicket! Dismissal details:")
        dismissed = _ask("Dismissed batsman", self._striker or "")
        dt_raw = _ask("Dismissal type (b=bowled, c=caught, l=lbw, r=run_out, s=stumped, h=hit_wicket)", "b")
        dt = _DISMISSAL_SHORTCUTS.get(dt_raw.lower(), DismissalType.BOWLED)
        fielder = _ask("Fielder (leave blank if none)", "")
        new_batsman = _ask("New batsman", "")

        wicket = WicketInfo(
            dismissal_type=dt,
            dismissed_batsman=dismissed or (self._striker or "unknown"),
            fielder=fielder or None,
            bowler_credited=dt not in (DismissalType.RUN_OUT,),
        )
        # Update striker name for next ball
        if new_batsman:
            self._striker = new_batsman

        shot_tag_raw = _ask("Shot tag (optional)", "")
        return Outcome(
            runs=runs,
            wicket=wicket,
            shot_tag=shot_tag_raw or None,
        )

    # ------------------------------------------------------------------
    # Ball recording + state update
    # ------------------------------------------------------------------

    def _record_ball(self, outcome: Outcome) -> None:
        # Snapshot for undo
        self._undo_stack.append((
            list(self.events),
            copy.deepcopy(self._state),
            self._over,
            self._ball,
            self._innings,
        ))

        prev_overs = self._state.overs_complete
        new_state = advance_state(
            self._state,
            outcome,
            over_number=self._over,
            total_overs=self._total_overs,
        )

        # Which ball number within the over?
        from voxplay.events.state import _is_legal_delivery
        if _is_legal_delivery(outcome):
            self._ball += 1

        ball_ev = BallEvent(
            match_id=self.match_id,
            innings=self._innings,
            over=self._over,
            ball=self._ball,
            bowler=self._bowler,
            batsman_striker=self._striker,
            batsman_non_striker=self._non_striker,
            outcome=outcome,
            state=copy.deepcopy(new_state),
            timestamp=datetime.now(timezone.utc),
        )
        self.events.append(ball_ev)
        self._state = new_state

        # Feedback line
        self._print_ball_result(outcome)

        # Over complete?
        if new_state.overs_complete > prev_overs:
            self._ball = 0
            self._over += 1
            self._prompt_new_over()

        # Rotate strike on odd runs
        if outcome.runs % 2 == 1 and not outcome.wicket:
            self._striker, self._non_striker = self._non_striker, self._striker

    def _print_ball_result(self, outcome: Outcome) -> None:
        if outcome.wicket:
            _print(f"  ->WICKET! {outcome.wicket.dismissed_batsman} {outcome.wicket.dismissal_type.value}")
        elif outcome.is_six:
            _print(f"  ->SIX! {self._state.score}/{self._state.wickets}")
        elif outcome.is_boundary:
            _print(f"  ->FOUR! {self._state.score}/{self._state.wickets}")
        elif outcome.extras:
            _print(f"  ->{outcome.extras.type.value.upper()} +{outcome.extras.runs}  {self._state.score}/{self._state.wickets}")
        else:
            _print(f"  ->{outcome.runs}  {self._state.score}/{self._state.wickets}")

    def _prompt_new_over(self) -> None:
        _print(f"\n  --- End of over {self._over}. Score: {self._state.score}/{self._state.wickets} ---")
        new_bowler = _ask("Next bowler", self._bowler or "Bowler")
        self._bowler = new_bowler
        # Swap ends — non-striker becomes striker
        self._striker, self._non_striker = self._non_striker, self._striker
        self._emit_over_start()

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _undo(self) -> None:
        if not self._undo_stack:
            _print("  Nothing to undo.")
            return
        events, state, over, ball, innings = self._undo_stack.pop()
        self.events = events
        self._state = state
        self._over = over
        self._ball = ball
        self._innings = innings
        _print("  Undo successful.")

    # ------------------------------------------------------------------
    # Drinks
    # ------------------------------------------------------------------

    def _add_drinks(self) -> None:
        ev = DrinksEvent(
            match_id=self.match_id,
            innings=self._innings,
            over=self._over,
            state=copy.deepcopy(self._state),
            timestamp=datetime.now(timezone.utc),
        )
        self.events.append(ev)
        _print("  Drinks break recorded.")
