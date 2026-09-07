"""
Commentary engine: converts an event stream into natural English commentary.

Calls the Anthropic Claude API (or a mock) to generate 1-4 sentences per event.
Rolling context window of the last 3 commentary lines is passed with each request
so commentary has rhythm and continuity across balls.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

from voxplay.events.models import Event
from .prompts import SYSTEM_PROMPT, event_to_prompt


@dataclass
class CommentaryLine:
    """One generated commentary snippet tied to its source event."""
    event_index: int
    event_type: str
    text: str
    # Populated by caller after audio synthesis
    audio_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Mock LLM - used when ANTHROPIC_API_KEY is absent or --mock flag is set
# ---------------------------------------------------------------------------

_MOCK_RESPONSES: dict[str, list[str]] = {
    "ball": [
        "Pushed firmly back to the bowler - a quiet start to proceedings.",
        "Lovely delivery, driven beautifully through the covers for four!",
        "Gone! Crashes into the stumps - the bowler is pumped!",
        "Nudged off the hip for a quick single, smart cricket.",
        "A regulation dot ball - the bowler is right on the money.",
        "That's a SIX! Clears the rope with ease - the crowd erupts!",
        "Full and straight, defended solidly back down the pitch.",
        "Clipped away on the leg side for a couple, good running.",
        "Shorter delivery, pulled hard but straight to mid-wicket.",
        "Flighted outside off, driven on the up - four more runs!",
    ],
    "over_start": [
        "A new over begins - the field spreads, the tension builds.",
        "The bowler marks out their run-up as the batsmen take guard. This should be an interesting over.",
        "Change of ends - a key moment in this innings. Let's see what the captain is planning.",
    ],
    "milestone": [
        "What a moment! A superb individual achievement, and the crowd is on its feet.",
        "Deserved recognition - this has been a fine, composed innings under pressure.",
        "History being made here today. A landmark achievement at this level of the game.",
    ],
    "innings_start": [
        "Here we go - the players take the field as a new innings begins. Plenty still to play for.",
        "The second innings gets underway. This is what cricket is all about.",
    ],
    "drinks": [
        "Players head off for drinks. A good moment to reflect on what's been a fascinating session.",
        "Drinks break - a brief pause in what has been an absorbing contest.",
    ],
}

_mock_counters: dict[str, int] = {}


def _mock_generate(event_type: str) -> str:
    key = event_type.replace("_", "")
    # normalise to one of the known keys
    for k in _MOCK_RESPONSES:
        if event_type.startswith(k) or k in event_type:
            key = k
            break
    responses = _MOCK_RESPONSES.get(key, _MOCK_RESPONSES["ball"])
    idx = _mock_counters.get(key, 0) % len(responses)
    _mock_counters[key] = idx + 1
    return responses[idx]


# ---------------------------------------------------------------------------
# Real LLM via Anthropic SDK
# ---------------------------------------------------------------------------

def _real_generate(system: str, user_prompt: str) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError(
            "anthropic SDK not installed. Run: pip install anthropic"
        ) from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Use --mock for offline mode."
        )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Commentary engine
# ---------------------------------------------------------------------------

class CommentaryEngine:
    """
    Consumes an event stream and yields CommentaryLine objects.

    Args:
        mock:         Use mock responses instead of calling the Anthropic API.
        window_size:  Number of previous commentary lines to pass as context.
    """

    def __init__(self, mock: bool = False, window_size: int = 5) -> None:
        self.mock = mock or not os.environ.get("ANTHROPIC_API_KEY")
        self.window_size = window_size
        self._window: deque[str] = deque(maxlen=window_size)

    def _generate(self, event_type: str, prompt: str) -> str:
        if self.mock:
            return _mock_generate(event_type)
        return _real_generate(SYSTEM_PROMPT, prompt)

    def commentate(self, events: List[Event]) -> Iterator[CommentaryLine]:
        """Yield a CommentaryLine for each event in *events*."""
        for idx, ev in enumerate(events):
            event_type = ev.event_type.value
            prompt = event_to_prompt(ev, list(self._window))
            text = self._generate(event_type, prompt)
            self._window.append(text)
            yield CommentaryLine(
                event_index=idx,
                event_type=event_type,
                text=text,
            )

    def reset(self) -> None:
        """Clear the rolling context window (call between innings)."""
        self._window.clear()
        _mock_counters.clear()
