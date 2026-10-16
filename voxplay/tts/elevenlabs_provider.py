"""
ElevenLabs TTS provider.

The paid voice. gTTS and the offline voice both read a sentence; this one
performs it, which is the difference between commentary you can follow and
commentary you want to keep listening to.

Talks to the REST endpoint directly with requests, which the project already
depends on, rather than pulling in the official SDK for one POST.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import TTSProvider, TTSError, with_audio_extension

API_ROOT = "https://api.elevenlabs.io/v1/text-to-speech"

#: "Daniel", a British broadcast voice. Chess commentary has a sound, and
#: it is this one: measured, unhurried, warming at the moment the game
#: turns. A neutral audiobook voice reads the words correctly and lands
#: every sentence with the same weight, which is what makes it obvious
#: nobody is behind it.
DEFAULT_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"

#: v4 is the expressive model. Turbo is cheaper and faster, and was the
#: wrong trade here -- it is tuned for latency, and latency does not matter
#: when the whole track is rendered before anyone presses play.
DEFAULT_MODEL = "eleven_v4"

#: Stability is how little the delivery varies between generations. High
#: stability reads a quiet developing move and the losing blunder in the
#: same tone, which is the strongest cue that the speaker is synthetic.
#: Lowering it lets the read follow the material; below about 0.3 it starts
#: wandering within a sentence.
DEFAULT_STABILITY = 0.35
DEFAULT_SIMILARITY = 0.75


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs voice synthesis. Produces MP3; needs a key and a network."""

    def __init__(
        self,
        voice_id: str | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        stability: float = DEFAULT_STABILITY,
        similarity_boost: float = DEFAULT_SIMILARITY,
    ) -> None:
        self.voice_id = voice_id or os.environ.get(
            "ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID
        )
        self.model_id = model_id or os.environ.get(
            "ELEVENLABS_MODEL", DEFAULT_MODEL
        )
        self._api_key = api_key
        self.timeout = timeout
        self.stability = stability
        self.similarity_boost = similarity_boost

    def _key(self) -> str:
        key = self._api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            raise TTSError(
                "ELEVENLABS_API_KEY is not set. Put it in your .env, or use "
                "the 'gtts' voice, which needs no key."
            )
        return key.strip()

    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> Path:
        import requests

        mp3_path = with_audio_extension(output_path, ".mp3")
        mp3_path.parent.mkdir(parents=True, exist_ok=True)

        body = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
            },
        }
        # Neither is spoken. They tell the model where this line sits in the
        # passage, so it can finish a thought rather than closing every clip
        # as though the game ended there.
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text

        try:
            response = requests.post(
                f"{API_ROOT}/{self.voice_id}",
                headers={
                    "xi-api-key": self._key(),
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                params={"output_format": "mp3_44100_128"},
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TTSError(f"could not reach ElevenLabs: {exc}") from exc

        if response.status_code != 200:
            raise TTSError(_explain(response))

        # A JSON body with a 200 means an error envelope rather than audio;
        # writing it would leave a file that is the right size and silent.
        if "application/json" in response.headers.get("content-type", ""):
            raise TTSError(
                "ElevenLabs returned a message instead of audio: "
                + response.text[:200]
            )
        if not response.content:
            raise TTSError("ElevenLabs returned an empty response")

        mp3_path.write_bytes(response.content)
        return mp3_path


def _explain(response) -> str:
    """Turn an HTTP failure into something a person can act on."""
    detail = ""
    try:
        body = response.json()
        detail = str(
            body.get("detail", {}).get("message")
            or body.get("detail")
            or body
        )
    except Exception:  # noqa: BLE001 - not JSON, fall back to the raw text
        detail = response.text[:200]

    if response.status_code == 401:
        return (
            "ElevenLabs rejected the API key (401). Check ELEVENLABS_API_KEY "
            "in your .env is the whole key, with no quotes. " + detail
        )
    if response.status_code == 402:
        return (
            "ElevenLabs is out of credits (402) for this key or workspace. "
            + detail
        )
    if response.status_code == 404:
        return (
            "No such voice: " + detail + " Set ELEVENLABS_VOICE_ID to a voice "
            "id from your library, or leave it unset for the default."
        )
    if response.status_code == 422:
        return "ElevenLabs rejected the request (422): " + detail
    if response.status_code == 429:
        return (
            "ElevenLabs rate-limited this key (429). Your plan allows a "
            "limited number of requests at once. " + detail
        )
    return f"ElevenLabs error {response.status_code}: {detail}"
