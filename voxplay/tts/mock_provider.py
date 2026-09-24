"""
Mock TTS provider — writes a silent WAV stub instead of calling any API.

Used by the `demo` command and tests so the full pipeline runs without
network access or API keys.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from .base import TTSProvider, with_audio_extension


# Duration in seconds for the silent placeholder clip
_SILENCE_SECONDS = 1
_SAMPLE_RATE = 22050


def _write_silent_wav(path: Path, duration: float = _SILENCE_SECONDS) -> None:
    """Write a WAV file containing pure silence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(_SAMPLE_RATE * duration)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(b"\x00\x00" * n_frames)


class MockTTSProvider(TTSProvider):
    """Produces silent WAV stubs — no network calls, no API keys required."""

    def synthesize(self, text: str, output_path: Path) -> Path:
        wav_path = with_audio_extension(output_path, ".wav")
        _write_silent_wav(wav_path)
        return wav_path
