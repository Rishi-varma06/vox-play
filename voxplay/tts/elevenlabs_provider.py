"""
ElevenLabs TTS provider — STUB.

TODO: Replace this stub with a real ElevenLabs implementation when ready.

Steps to implement:
  1. pip install elevenlabs
  2. Set ELEVENLABS_API_KEY in your .env
  3. Choose a voice_id from the ElevenLabs voice library (or clone one)
  4. Replace the raise NotImplementedError below with the actual API call:

     from elevenlabs.client import ElevenLabs
     client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
     audio = client.text_to_speech.convert(
         voice_id=self.voice_id,
         text=text,
         model_id="eleven_turbo_v2",
         output_format="mp3_44100_128",
     )
     with open(output_path, "wb") as f:
         for chunk in audio:
             f.write(chunk)

The rest of the pipeline (assembler, CLI) requires no changes — just swap
the provider via the --tts flag or the get_provider() factory.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import TTSProvider, TTSError


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs voice synthesis (stub — not yet implemented)."""

    def __init__(self, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> None:
        # Default voice_id: "Rachel" (neutral, clear English)
        self.voice_id = voice_id

    def synthesize(self, text: str, output_path: Path) -> Path:
        # TODO: implement ElevenLabs synthesis (see module docstring)
        raise NotImplementedError(
            "ElevenLabs provider is not yet implemented. "
            "See voxplay/tts/elevenlabs_provider.py for instructions."
        )
