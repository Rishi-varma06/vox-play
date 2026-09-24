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


def with_audio_extension(path: Path | str, extension: str) -> Path:
    """
    Ensure *path* ends in *extension*, appending rather than replacing.

    Path.with_suffix() replaces everything after the last dot, which silently
    mangles any stem that legitimately contains one. A chess game id taken
    from a PGN date -- "Morphy_Duke_1858.11.02" -- turns into
    "Morphy_Duke_1858.wav", so every clip in the game collapses onto a single
    filename and overwrites the one before it. The failure is invisible: you
    get a track of the right length made entirely of the last clip repeated.
    """
    path = Path(path)
    if path.suffix.lower() == extension.lower():
        return path
    return path.with_name(path.name + extension)


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
