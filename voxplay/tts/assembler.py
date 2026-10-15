"""
Audio assembler: concatenates per-event clips into a single commentary track.

Uses pydub (which delegates to ffmpeg) if available; falls back to a pure-Python
wave concatenation for WAV files so the demo works without ffmpeg installed.
"""

from __future__ import annotations

import wave
from pathlib import Path

from .base import TTSError, with_audio_extension
from typing import List


def _concat_wav_pure(clip_paths: List[Path], output_path: Path) -> Path:
    """Pure-Python WAV concatenation — no ffmpeg required."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Read params from first valid clip
    params = None
    frames_list: List[bytes] = []

    for p in clip_paths:
        if not p.exists():
            continue
        with wave.open(str(p), "rb") as wf:
            if params is None:
                params = wf.getparams()
            frames_list.append(wf.readframes(wf.getnframes()))

    if params is None or not frames_list:
        raise ValueError("No valid audio clips to assemble.")

    wav_out = with_audio_extension(output_path, ".wav")
    with wave.open(str(wav_out), "wb") as wf:
        wf.setparams(params)
        for frames in frames_list:
            wf.writeframes(frames)
    return wav_out


def _ensure_ffmpeg() -> bool:
    """
    Point pydub at an ffmpeg it can actually run, and say whether one exists.

    pydub shells out to ffmpeg for everything that is not a WAV, and on a
    machine without it on PATH that failure arrives deep inside a decode as
    something unrelated-looking. The imageio-ffmpeg wheel ships a static
    binary, so when PATH has nothing we use that rather than asking someone
    to install ffmpeg system-wide.
    """
    import shutil

    if shutil.which("ffmpeg"):
        return True
    try:
        import imageio_ffmpeg
        from pydub import AudioSegment
    except ImportError:
        return False
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - the wheel is present but unusable
        return False
    AudioSegment.converter = exe
    AudioSegment.ffmpeg = exe
    AudioSegment.ffprobe = exe
    return True


def _concat_pydub(clip_paths: List[Path], output_path: Path) -> Path:
    from pydub import AudioSegment

    combined = AudioSegment.empty()
    silence = AudioSegment.silent(duration=400)  # 0.4 s gap between clips

    existing = [p for p in clip_paths if p.exists()]
    failures: List[str] = []
    decoded = 0
    for p in existing:
        try:
            # Naming the format keeps pydub from shelling out to ffprobe to
            # sniff it. The bundled ffmpeg has no ffprobe beside it, and the
            # clips were written by a provider that knows its own format, so
            # the probe is both avoidable and the thing that breaks first.
            seg = AudioSegment.from_file(
                str(p), format=p.suffix.lstrip(".").lower() or None
            )
        except Exception as exc:  # noqa: BLE001 - reported in aggregate below
            # One unreadable clip is a gap; every clip unreadable is a broken
            # install, and silently exporting an empty track hides that.
            failures.append(f"{p.name}: {exc}")
            continue
        combined = combined + seg + silence
        decoded += 1

    if existing and decoded == 0:
        raise TTSError(
            "could not decode any of the " + str(len(existing)) + " audio clips. "
            "ffmpeg is usually the cause -- install it, or run "
            "'pip install imageio-ffmpeg' to get a bundled copy.\n  "
            + "\n  ".join(failures[:3])
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lstrip(".") or "mp3"
    out = with_audio_extension(output_path, f".{suffix}")
    combined.export(str(out), format=suffix)
    return out


def assemble(clip_paths: List[Path], output_path: Path) -> Path:
    """
    Concatenate *clip_paths* into a single file at *output_path*.

    Tries pydub first (supports MP3 and any ffmpeg format); falls back to
    pure-Python WAV concatenation if pydub is unavailable or clips are WAV.
    """
    all_wav = all(p.suffix.lower() == ".wav" for p in clip_paths if p.exists())

    # WAV needs neither pydub nor ffmpeg, so take the path with the fewest
    # moving parts when it is available rather than only as a fallback.
    if all_wav and not _ensure_ffmpeg():
        return _concat_wav_pure(clip_paths, output_path)

    try:
        import pydub  # noqa: F401 — just checking availability
    except ImportError:
        if all_wav:
            return _concat_wav_pure(clip_paths, output_path)
        raise ImportError(
            "pydub is required to assemble non-WAV audio clips. "
            "Run: pip install pydub\n"
            "ffmpeg must also be on your PATH."
        )

    if not _ensure_ffmpeg() and not all_wav:
        raise TTSError(
            "these clips are " + (clip_paths[0].suffix if clip_paths else "not WAV")
            + " and joining them needs ffmpeg, which is not installed.\n"
            "Run 'pip install imageio-ffmpeg' for a bundled copy, or use the "
            "'offline' voice, which emits WAV and needs neither."
        )
    return _concat_pydub(clip_paths, output_path)
