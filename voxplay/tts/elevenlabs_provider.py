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

#: "Rachel" from the stock library: neutral, clear, unhurried. Chess
#: commentary spends most of its time explaining rather than shouting, so a
#: voice that stays level reads better across a whole game than a dramatic
#: one does.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

#: Turbo is roughly half the credits of the multilingual model and the
#: difference is inaudible on a single-language script. A full match is
#: thirty-three clips, so halving the per-clip cost is the difference
#: between a month of narration and a fortnight of it.
DEFAULT_MODEL = "eleven_turbo_v2_5"


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs voice synthesis. Produces MP3; needs a key and a network."""

    def __init__(
        self,
        voice_id: str | None = None,
        model_id: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.voice_id = voice_id or os.environ.get(
            "ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID
        )
        self.model_id = model_id
        self._api_key = api_key
        self.timeout = timeout

    def _key(self) -> str:
        key = self._api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            raise TTSError(
                "ELEVENLABS_API_KEY is not set. Put it in your .env, or use "
                "the 'gtts' voice, which needs no key."
            )
        return key.strip()

    def synthesize(self, text: str, output_path: Path) -> Path:
        import requests

        mp3_path = with_audio_extension(output_path, ".mp3")
        mp3_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = requests.post(
                f"{API_ROOT}/{self.voice_id}",
                headers={
                    "xi-api-key": self._key(),
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                params={"output_format": "mp3_44100_128"},
                json={"text": text, "model_id": self.model_id},
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
