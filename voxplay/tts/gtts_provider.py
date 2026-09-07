"""Google TTS provider using the gTTS library."""

from __future__ import annotations

from pathlib import Path

from .base import TTSProvider, TTSError


class GTTSProvider(TTSProvider):
    """
    Uses the gTTS (Google Text-to-Speech) library.

    Produces MP3 files. Requires an internet connection.
    Install: pip install gtts
    """

    def __init__(self, lang: str = "en", tld: str = "co.uk") -> None:
        self.lang = lang
        # tld="co.uk" gives a British accent — appropriate for cricket commentary
        self.tld = tld

    def synthesize(self, text: str, output_path: Path) -> Path:
        try:
            from gtts import gTTS, gTTSError as _gTTSError
        except ImportError as exc:
            raise TTSError(
                "gTTS is not installed. Run: pip install gtts"
            ) from exc

        # Ensure .mp3 extension
        mp3_path = output_path.with_suffix(".mp3")
        mp3_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            tts = gTTS(text=text, lang=self.lang, tld=self.tld, slow=False)
            tts.save(str(mp3_path))
        except Exception as exc:
            raise TTSError(f"gTTS synthesis failed: {exc}") from exc

        return mp3_path
