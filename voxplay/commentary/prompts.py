"""System prompt and per-event prompt builders for the commentary engine."""

from __future__ import annotations

from voxplay.events.models import (
    BallEvent,
    DrinksEvent,
    Event,
    InningsStartEvent,
    MatchState,
    MilestoneEvent,
    OverStartEvent,
)

SYSTEM_PROMPT = """You are a live cricket commentator — energetic, knowledgeable, and natural.
Your voice is warm and conversational, never robotic or overly formal.

RULES YOU MUST FOLLOW:
1. Ball commentary: exactly 1-2 sentences. Never more.
2. Proportion your reaction to the event:
   - Dot ball in a normal situation → brief, factual ("Good length, pushed back to the bowler.")
   - Dot ball under heavy chase pressure → lean into the tension ("Dot ball! The required rate climbs further.")
   - Single or two → conversational, varied ("nudged away for a single", "rotated well")
   - Boundary four → genuine pleasure, describe the shot if shot_tag is provided
   - Six → real excitement, keep it vivid but not overwrought
   - Wicket → punchy and impactful; state the dismissal clearly; don't invent dramatic backstory
3. Vary your sentence openings AND phrasing. Before writing, check the recent
   commentary lines shown in your prompt: do not start with the same word as any
   of those lines, and do not reuse any run of 4 or more consecutive words from them.
4. Use the match situation (score, wickets, overs remaining, run rate) naturally.
5. STRICT: every number you write must come directly from the event data given to
   you. This includes scores, wickets, ball counts, run rates, and individual
   batsman figures. If a value is not explicitly provided, omit it entirely —
   do not estimate, round, or invent. An omitted number is always better than a
   wrong one.
6. For over_start and milestone events you have 2-4 sentences; use the extra space for
   context, statistics, or personality — this is what keeps commentary alive.
7. Never refer to yourself as an AI or mention any tool or model.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_summary(state: MatchState) -> str:
    parts = [f"{state.score}/{state.wickets}"]
    if state.overs_complete is not None and state.balls_in_over is not None:
        parts.append(f"after {state.overs_complete}.{state.balls_in_over} overs")
    if state.target:
        needed = state.target - state.score
        parts.append(f"chasing {state.target} — need {needed} more")
    if state.balls_remaining is not None:
        overs_rem = state.balls_remaining // 6
        balls_rem = state.balls_remaining % 6
        if balls_rem:
            parts.append(f"{overs_rem} ov {balls_rem} balls remaining")
        else:
            parts.append(f"{overs_rem} overs remaining")
    if state.required_run_rate:
        parts.append(f"RRR {state.required_run_rate}")
    if state.current_run_rate:
        parts.append(f"CRR {state.current_run_rate}")
    return ", ".join(parts)


def _outcome_summary(ev: BallEvent) -> str:
    o = ev.outcome
    parts = []
    if o.wicket:
        parts.append(f"WICKET — {o.wicket.dismissed_batsman} {o.wicket.dismissal_type.value}")
        if o.wicket.fielder:
            parts.append(f"c {o.wicket.fielder}")
        parts.append(f"b {ev.bowler}" if ev.bowler else "")
    elif o.is_six:
        parts.append("SIX")
    elif o.is_boundary:
        parts.append(f"FOUR")
    elif o.extras:
        parts.append(f"{o.extras.type.value.upper()} +{o.extras.runs}")
    else:
        parts.append(f"{o.runs} run{'s' if o.runs != 1 else ''}" if o.runs else "dot ball")

    if o.shot_tag:
        parts.append(f"[{o.shot_tag.replace('_', ' ')}]")
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Per-event prompt builders
# ---------------------------------------------------------------------------

def build_ball_prompt(ev: BallEvent, window: list[str]) -> str:
    context_lines = ""
    if window:
        context_lines = (
            "Recent commentary — read before writing:\n"
            "  DO NOT open with the same word as any line below.\n"
            "  DO NOT reuse any phrase of 4+ consecutive words from any line below.\n"
        )
        context_lines += "\n".join(f"  • {line}" for line in window)
        context_lines += "\n\n"

    bowler = ev.bowler or "the bowler"
    striker = ev.batsman_striker or "the batsman"
    non_striker = ev.batsman_non_striker or "the non-striker"
    ball_ref = f"Over {ev.over + 1}, ball {ev.ball}"
    striker_info = f"{striker} on {ev.state.striker_runs} off {ev.state.striker_balls} balls" if ev.state.striker_balls else striker

    wickets_in_hand = 10 - ev.state.wickets

    return (
        f"{context_lines}"
        f"Ball: {ball_ref}\n"
        f"Bowler: {bowler} | Striker: {striker_info} | Non-striker: {non_striker}\n"
        f"Outcome: {_outcome_summary(ev)}\n"
        f"Match state: {_state_summary(ev.state)} | {wickets_in_hand} wickets in hand\n"
        f"Partnership: {ev.state.partnership_runs} runs off {ev.state.partnership_balls} balls\n"
        f"\nWrite 1-2 sentences of live ball-by-ball commentary."
    )


def build_over_start_prompt(ev: OverStartEvent, window: list[str]) -> str:
    bowler = ev.bowler or "the next bowler"
    context = f"Match state: {_state_summary(ev.state)}\n" if ev.state else ""
    return (
        f"Over {ev.over + 1} is about to begin. {bowler} comes in to bowl.\n"
        f"{context}"
        f"Write 2-4 sentences of over-start commentary — set the scene, mention the bowler's "
        f"role, the match situation, any tactical interest. Keep it vivid and conversational."
    )


def build_milestone_prompt(ev: MilestoneEvent, window: list[str]) -> str:
    return (
        f"Milestone: {ev.description}\n"
        f"Player: {ev.player or 'unknown'} | Value: {ev.value}\n"
        f"Match state: {_state_summary(ev.state)}\n"
        f"Write 2-4 sentences celebrating this milestone — acknowledge the achievement, "
        f"give brief context (how it came, the innings situation), and keep the energy up."
    )


def build_innings_start_prompt(ev: InningsStartEvent) -> str:
    batting = ev.batting_team or "the batting side"
    bowling = ev.bowling_team or "the bowling side"
    target_str = f" chasing {ev.target}" if ev.target else ""
    overs_str = f" in {ev.overs} overs" if ev.overs else ""
    return (
        f"Innings {ev.innings} is about to start. {batting} bat{target_str}{overs_str} "
        f"against {bowling}.\n"
        f"Write 2-3 sentences setting the scene for this innings. Mention the target if chasing, "
        f"the match format, and build anticipation."
    )


def build_drinks_prompt(ev: DrinksEvent) -> str:
    return (
        f"Drinks break at over {ev.over}. Match state: {_state_summary(ev.state)}\n"
        f"Write 1-2 sentences — a brief pause, perhaps reflect on how the innings is going."
    )


def event_to_prompt(ev: Event, window: list[str]) -> str:
    """Dispatch to the right prompt builder for any event type."""
    if isinstance(ev, BallEvent):
        return build_ball_prompt(ev, window)
    elif isinstance(ev, OverStartEvent):
        return build_over_start_prompt(ev, window)
    elif isinstance(ev, MilestoneEvent):
        return build_milestone_prompt(ev, window)
    elif isinstance(ev, InningsStartEvent):
        return build_innings_start_prompt(ev)
    elif isinstance(ev, DrinksEvent):
        return build_drinks_prompt(ev)
    else:
        return f"Describe this cricket event briefly: {ev.model_dump_json()}"
