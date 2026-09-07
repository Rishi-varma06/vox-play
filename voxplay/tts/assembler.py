"""
Audio assembler: concatenates per-event clips into a single commentary track.

Uses pydub (which delegates to ffmpeg) if available; falls back to a pure-Python
wave concatenation for WAV files so the demo works without ffmpeg installed.
"""

from __future__ import annotations

import wave
from pathlib import Path
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

    wav_out = output_path.with_suffix(".wav")
    with wave.open(str(wav_out), "wb") as wf:
        wf.setparams(params)
        for frames in frames_list:
            wf.writeframes(frames)
    return wav_out


def _concat_pydub(clip_paths: List[Path], output_path: Path) -> Path:
    from pydub import AudioSegment

    combined = AudioSegment.empty()
    silence = AudioSegment.silent(duration=400)  # 0.4 s gap between clips

    for p in clip_paths:
        if not p.exists():
            continue
        try:
            seg = AudioSegment.from_file(str(p))
        except Exception:
            continue
        combined = combined + seg + silence

    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lstrip(".") or "mp3"
    out = output_path.with_suffix(f".{suffix}")
    combined.export(str(out), format=suffix)
    return out


def assemble(clip_paths: List[Path], output_path: Path) -> Path:
    """
    Concatenate *clip_paths* into a single file at *output_path*.

    Tries pydub first (supports MP3 and any ffmpeg format); falls back to
    pure-Python WAV concatenation if pydub is unavailable or clips are WAV.
    """
    # If all clips are WAV and pydub is not available, use pure Python
    all_wav = all(p.suffix.lower() == ".wav" for p in clip_paths if p.exists())

    try:
        import pydub  # noqa: F401 — just checking availability
        return _concat_pydub(clip_paths, output_path)
    except ImportError:
        if all_wav:
            return _concat_wav_pure(clip_paths, output_path)
        raise ImportError(
            "pydub is required to assemble non-WAV audio clips. "
            "Run: pip install pydub\n"
            "ffmpeg must also be on your PATH."
        )
