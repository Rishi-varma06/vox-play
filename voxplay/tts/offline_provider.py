"""
Offline TTS provider -- the system voice, no network and no ffmpeg.

Why this exists alongside gTTS: gTTS needs the network and emits MP3, and MP3
cannot be assembled without ffmpeg. This provider emits WAV, which the
assembler concatenates with the standard library alone, so the whole audio
path runs on a machine with no network, no API key, and nothing to install
beyond a pip package. That is also what makes the near-zero-cost-per-game
claim testable rather than asserted.

Two backends, and the choice is not cosmetic:

    SAPI (Windows)  driven directly through COM
    pyttsx3         everywhere else

pyttsx3 is the obvious library and it is what this used first, but its SAPI5
driver deadlocks on the SECOND runAndWait() in a process -- measured here,
clip 0 lands in 0.1s and clip 1 never returns, with or without a fresh engine
per clip. A game needs one clip per beat, so on Windows that bug is fatal.
Driving SAPI through COM instead synthesised five clips in 0.02s each with no
hang. pyttsx3 stays as the fallback for platforms whose drivers are fine.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .base import TTSProvider, TTSError, with_audio_extension

#: Words per minute. Matches beats.WORDS_PER_SECOND (2.5/s = 150 wpm), so the
#: pacing model's duration estimates and the actual spoken audio agree. Change
#: one and you must change the other or every beat budget silently drifts.
DEFAULT_RATE_WPM = 150

#: SAPI's SpeechStreamFileMode for "create for write". Hardcoded rather than
#: imported from comtypes.gen, which only exists after the type library has
#: been generated and is therefore not importable at module load.
_SSFM_CREATE_FOR_WRITE = 3

#: SAPI rate is a -10..10 dial, not words per minute. This table is MEASURED
#: on Microsoft David by synthesising a 73-word passage at each setting -- not
#: taken from the documentation, which says 0 is 200 wpm and the ends are a
#: threefold change. That description is wrong enough to matter: it put the
#: dial at -3 (138 wpm) when asked for 150, and every beat budget in the
#: system inherited the error.
#:
#: A lookup beats a fitted curve here. The real response is not quite
#: geometric, and with nine measured points there is no reason to approximate
#: them -- a log fit picks the wrong neighbour around 180 wpm.
_MEASURED_WPM = {
    -5: 110.6, -4: 123.5, -3: 138.3, -2: 154.8, -1: 172.8,
    0: 188.5, 1: 217.1, 2: 241.2, 3: 272.4,
}

#: Only for extrapolating past the measured range, where the curve is roughly
#: geometric at about 1.12x per step.
_SAPI_STEP_FACTOR = 1.12


def _wpm_to_sapi_rate(wpm: float) -> int:
    """Words per minute -> the SAPI dial setting that comes closest."""
    import math

    slowest, fastest = min(_MEASURED_WPM), max(_MEASURED_WPM)

    if wpm < _MEASURED_WPM[slowest]:
        steps = math.log(max(0.001, wpm / _MEASURED_WPM[slowest]), _SAPI_STEP_FACTOR)
        return max(-10, slowest + round(steps))
    if wpm > _MEASURED_WPM[fastest]:
        steps = math.log(wpm / _MEASURED_WPM[fastest], _SAPI_STEP_FACTOR)
        return min(10, fastest + round(steps))

    return min(_MEASURED_WPM, key=lambda dial: abs(_MEASURED_WPM[dial] - wpm))


class OfflineTTSProvider(TTSProvider):
    """System speech synthesis. No network, no API key, WAV output."""

    def __init__(
        self,
        rate: int = DEFAULT_RATE_WPM,
        voice: Optional[str] = None,
        volume: float = 1.0,
    ) -> None:
        self.rate = rate
        self.voice = voice
        self.volume = volume
        self._sapi = None
        self._pyttsx3 = None

    # -- Windows: SAPI over COM -------------------------------------------

    def _get_sapi(self):
        if self._sapi is not None:
            return self._sapi
        import comtypes.client

        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        voice.Rate = _wpm_to_sapi_rate(self.rate)
        voice.Volume = max(0, min(100, int(self.volume * 100)))
        if self.voice:
            for token in voice.GetVoices():
                if self.voice.lower() in token.GetDescription().lower():
                    voice.Voice = token
                    break
        self._sapi = voice
        return voice

    def _synthesize_sapi(self, text: str, wav_path: Path) -> None:
        import comtypes.client

        voice = self._get_sapi()
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        stream.Open(str(wav_path), _SSFM_CREATE_FOR_WRITE)
        try:
            voice.AudioOutputStream = stream
            voice.Speak(text)
        finally:
            stream.Close()

    # -- Everywhere else: pyttsx3 -----------------------------------------

    def _get_pyttsx3(self):
        if self._pyttsx3 is not None:
            return self._pyttsx3
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        engine.setProperty("volume", self.volume)
        if self.voice:
            for candidate in engine.getProperty("voices"):
                haystack = (candidate.id + getattr(candidate, "name", "")).lower()
                if self.voice.lower() in haystack:
                    engine.setProperty("voice", candidate.id)
                    break
        self._pyttsx3 = engine
        return engine

    def _synthesize_pyttsx3(self, text: str, wav_path: Path) -> None:
        engine = self._get_pyttsx3()
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()

    # -- Public interface --------------------------------------------------

    def available_voices(self) -> List[Tuple[str, str]]:
        """(id, description) for every installed voice."""
        if sys.platform == "win32":
            try:
                return [(t.Id, t.GetDescription()) for t in self._get_sapi().GetVoices()]
            except Exception:  # noqa: BLE001 - fall through to pyttsx3
                pass
        try:
            return [
                (v.id, getattr(v, "name", ""))
                for v in self._get_pyttsx3().getProperty("voices")
            ]
        except Exception as exc:  # noqa: BLE001 - surfaced as TTSError
            raise TTSError(f"could not list voices: {exc}") from exc

    def synthesize(self, text: str, output_path: Path) -> Path:
        wav_path = with_audio_extension(output_path, ".wav")
        wav_path.parent.mkdir(parents=True, exist_ok=True)

        errors = []
        backends = []
        if sys.platform == "win32":
            backends.append(("SAPI", self._synthesize_sapi))
        backends.append(("pyttsx3", self._synthesize_pyttsx3))

        for name, backend in backends:
            try:
                backend(text, wav_path)
            except ImportError as exc:
                errors.append(f"{name}: not installed ({exc})")
                continue
            except Exception as exc:  # noqa: BLE001 - try the next backend
                errors.append(f"{name}: {exc}")
                continue

            # Drivers report success even when nothing lands on disk.
            if wav_path.exists() and wav_path.stat().st_size > 0:
                return wav_path
            errors.append(f"{name}: wrote no audio")

        raise TTSError(
            "no speech backend could synthesise audio. Tried -- "
            + "; ".join(errors)
            + ". Install pyttsx3, or use --tts mock to check the rest of the pipeline."
        )
