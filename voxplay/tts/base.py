"""
TTSProvider interface — the single contract for all voice synthesis backends.

To add a new TTS backend:
  1. Create voxplay/tts/<name>_provider.py
  2. Subclass TTSProvider and implement synthesize()
  3. Register it in voxplay/tts/__init__.py's get_provider() factory
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    """Abstract base for all TTS backends."""

    @abstractmethod
    def synthesize(self, text: str, output_path: Path) -> Path:
        """
        Convert *text* to speech and write an audio file to *output_path*.

        Args:
            text:        The commentary line to speak.
            output_path: Desired file path (provider may adjust extension).

        Returns:
            The actual path of the written audio file.
        """
        ...


class TTSError(RuntimeError):
    """Raised by TTSProvider implementations on synthesis failure."""
