"""
Commentary script -> spoken audio, with a timeline.

The timeline is the point. Anyone can concatenate clips; what the board
renderer needs is to know exactly when each beat starts and which half-moves
it is talking about, so the board can be on the right position while the
sentence about it is playing. That mapping is produced here, measured from the
rendered audio rather than estimated, and it is the contract the renderer will
consume.

This module assembles WAV itself rather than calling voxplay.tts.assemble.
That function inserts a 400ms gap on its pydub path and none on its pure-WAV
path, so the offsets would depend on whether ffmpeg happened to be installed.
A timeline that silently drifts when a dependency appears is worse than no
timeline, so the gap is explicit here and the offsets are computed from the
same numbers used to write the track.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from ..tts import TTSError, get_provider
from ..tts.base import TTSProvider
from .beats import BeatType
from .script import CommentaryScript

#: Silence between beats. Long enough to hear a boundary, short enough that a
#: two-minute recap does not turn into three.
DEFAULT_GAP_MS = 450


class AudioError(RuntimeError):
    """Raised when a commentary track cannot be produced."""


class AudioCue(BaseModel):
    """
    One spoken line, placed on the timeline.

    `plies` is what makes this useful to a renderer: it says which half-moves
    this audio is about, so the board can be driven from the same structure.
    """
    index: int
    beat_type: BeatType
    plies: List[int] = Field(default_factory=list)
    text: str
    audio_path: str
    start_ms: int
    duration_ms: int

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


class CommentaryAudio(BaseModel):
    """A finished track plus the timeline that describes it."""
    game_id: str
    voice: str
    track_path: str
    cues: List[AudioCue] = Field(default_factory=list)
    total_ms: int = 0
    estimated_ms: int = 0

    @property
    def drift_ratio(self) -> float:
        """
        Actual duration over the word-count estimate.

        The pacing model budgets airtime in words; this says whether that
        assumption survives contact with a real voice. A ratio far from 1.0
        means every beat budget in the system is wrong by that factor.
        """
        return self.total_ms / self.estimated_ms if self.estimated_ms else 0.0


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

class TranscodeError(RuntimeError):
    """Raised when a clip cannot be converted to WAV."""


def _ffmpeg_exe() -> Optional[str]:
    """Path to a usable ffmpeg, preferring one already on PATH."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - absent or unusable, same answer
        return None


def to_wav(clip: Path) -> Path:
    """
    Convert *clip* to a 16-bit mono WAV beside it, returning the new path.

    Only the voices that emit compressed audio reach this -- gTTS returns
    MP3, and the timeline cannot measure an MP3 without decoding it anyway.
    Converting once here is cheaper than teaching every later step a second
    format, and keeps one rule: past this point everything is WAV.
    """
    if clip.suffix.lower() == ".wav":
        return clip
    exe = _ffmpeg_exe()
    if exe is None:
        raise TranscodeError(
            "ffmpeg is not installed, so " + clip.suffix + " clips cannot be "
            "converted. Run 'pip install imageio-ffmpeg' for a bundled copy, "
            "or use the 'offline' voice, which already emits WAV."
        )
    target = clip.with_suffix(".wav")
    result = subprocess.run(
        [exe, "-y", "-loglevel", "error", "-i", str(clip),
         "-ac", "1", "-ar", "22050", "-sample_fmt", "s16", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.exists():
        raise TranscodeError(
            "ffmpeg could not convert " + clip.name + ": "
            + (result.stderr or "no output").strip()[:200]
        )
    return target


def wav_duration_ms(path: Path) -> int:
    """Exact duration of a WAV file, in milliseconds."""
    with wave.open(str(path), "rb") as handle:
        return round(1000 * handle.getnframes() / handle.getframerate())


def _silence_frames(params, milliseconds: int) -> bytes:
    frame_count = int(params.framerate * milliseconds / 1000)
    return b"\x00" * (frame_count * params.sampwidth * params.nchannels)


def concat_wavs(clips: List[Path], output: Path, gap_ms: int = DEFAULT_GAP_MS) -> List[int]:
    """
    Concatenate WAV clips with a fixed gap, returning each clip's start offset.

    Returns the offsets rather than just writing the file, because the caller
    needs the two to agree exactly -- they are computed from the same frame
    counts that get written.
    """
    if not clips:
        raise AudioError("no audio clips to assemble")

    output.parent.mkdir(parents=True, exist_ok=True)
    params = None
    starts: List[int] = []
    cursor_ms = 0

    with wave.open(str(output), "wb") as out:
        for index, clip in enumerate(clips):
            with wave.open(str(clip), "rb") as source:
                clip_params = source.getparams()
                if params is None:
                    params = clip_params
                    out.setparams(params)
                elif (clip_params.framerate, clip_params.sampwidth,
                      clip_params.nchannels) != (params.framerate,
                                                 params.sampwidth,
                                                 params.nchannels):
                    raise AudioError(
                        f"{clip.name} has different audio parameters from the "
                        "first clip; all clips must come from one provider"
                    )
                frames = source.readframes(source.getnframes())

            if index and gap_ms:
                out.writeframes(_silence_frames(params, gap_ms))
                cursor_ms += gap_ms

            starts.append(cursor_ms)
            out.writeframes(frames)
            cursor_ms += round(1000 * (len(frames) //
                                       (params.sampwidth * params.nchannels)) /
                               params.framerate)

    return starts


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

def narrate(
    script: CommentaryScript,
    out_dir: Path | str,
    provider: TTSProvider | str = "offline",
    gap_ms: int = DEFAULT_GAP_MS,
) -> CommentaryAudio:
    """
    Speak every line of `script` and assemble a track with a timeline.

    Per-beat clips are kept, not just the final track: regenerating one line
    should not mean re-synthesising the whole game, and the renderer may want
    to play a single beat.
    """
    if not script.lines:
        raise AudioError("the script has no lines to speak")

    backend = get_provider(provider) if isinstance(provider, str) else provider
    out_dir = Path(out_dir)
    clip_dir = out_dir / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)

    clips: List[Path] = []
    for index, line in enumerate(script.lines):
        target = clip_dir / f"{script.game_id}_{index:03d}_{line.beat_type.value}"
        try:
            clips.append(backend.synthesize(line.text, target))
        except TTSError as exc:
            raise AudioError(f"beat {index} ({line.beat_type.value}): {exc}") from exc

    # Everything below measures durations out of the WAV header, because the
    # board sync is only as honest as those offsets. A provider that emits
    # something else is transcoded here rather than rejected, so the choice
    # of voice stays a question of how it sounds.
    if any(clip.suffix.lower() != ".wav" for clip in clips):
        try:
            clips = [to_wav(clip) for clip in clips]
        except TranscodeError as exc:
            raise AudioError(
                "the timeline needs WAV clips so durations can be measured "
                "exactly, and these could not be converted: " + str(exc)
            ) from exc

    track = out_dir / f"{script.game_id}_commentary.wav"
    starts = concat_wavs(clips, track, gap_ms=gap_ms)

    cues = [
        AudioCue(
            index=index,
            beat_type=line.beat_type,
            plies=list(line.plies),
            text=line.text,
            audio_path=str(clip),
            start_ms=start,
            duration_ms=wav_duration_ms(clip),
        )
        for index, (line, clip, start) in enumerate(zip(script.lines, clips, starts))
    ]

    return CommentaryAudio(
        game_id=script.game_id,
        voice=type(backend).__name__,
        track_path=str(track),
        cues=cues,
        total_ms=wav_duration_ms(track),
        estimated_ms=round(script.total_seconds * 1000),
    )


def write_timeline(audio: CommentaryAudio, path: Optional[Path | str] = None) -> Path:
    """
    Persist the timeline as JSON, next to the track by default.

    This file is the renderer's input: every cue carries its start, its
    duration, and the half-moves it describes.
    """
    target = Path(path) if path else Path(audio.track_path).with_suffix(".timeline.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(audio.model_dump_json(indent=2), encoding="utf-8")
    return target
