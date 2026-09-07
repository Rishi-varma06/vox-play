"""
Extension point: EventSource interface.

Any component that produces a stream of Event objects — a scorecard file
parser, an interactive scorer, or a FUTURE vision-model input — should
implement this interface.

To add a vision-model event source:
  1. Create voxplay/ingest/vision_source.py
  2. Subclass EventSource, implement produce()
  3. Wire it into cli/main.py under a new subcommand (e.g. `voxplay live`)

No changes to the commentary engine, TTS layer, or assembler are required.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from voxplay.events.models import Event


class EventSource(ABC):
    """
    Abstract base for all event producers.

    Implementations must be lazy iterators — do not buffer the entire match
    in memory before yielding; yield each event as soon as it is known.
    """

    @abstractmethod
    def produce(self) -> Iterator[Event]:
        """
        Yield Event objects in chronological order.

        Raises:
            IngestionError: on unrecoverable parse/IO failure.
        """
        ...

    def __iter__(self) -> Iterator[Event]:
        return self.produce()


class IngestionError(RuntimeError):
    """Raised by EventSource implementations on unrecoverable failure."""
