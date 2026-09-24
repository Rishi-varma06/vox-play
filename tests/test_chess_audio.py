"""
Tests for the audio layer and the filename handling it depends on.

Runs on MockTTSProvider so the suite needs no speech engine. The mock writes
silent WAV, which is enough to test every structural claim: clip count,
timeline offsets, and that distinct beats produce distinct files.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from voxplay.chessgame import beats as B
from voxplay.chessgame.audio import (
    AudioError,
    DEFAULT_GAP_MS,
    concat_wavs,
    narrate,
    wav_duration_ms,
    write_timeline,
)
from voxplay.chessgame.engine import MockAnalysisEngine, analyse_game
from voxplay.chessgame.pgn_source import load_pgn
from voxplay.chessgame.script import TemplateGenerator
from voxplay.tts.base import with_audio_extension
from voxplay.tts.offline_provider import _wpm_to_sapi_rate

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"


@pytest.fixture(scope="module")
def script():
    game = analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())
    return TemplateGenerator().generate(game, B.select_beats(game))


def write_tone(path: Path, seconds: float, framerate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(framerate)
        handle.writeframes(b"\x00\x01" * int(framerate * seconds))
    return path


# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------

def test_extension_is_appended_not_substituted():
    """
    Regression. Path.with_suffix() replaces everything after the LAST dot, so
    a PGN-derived game id like "Morphy_Duke_1858.11.02_000_intro" became
    "Morphy_Duke_1858.11.wav" -- every clip in the game collapsed onto one
    filename, each overwriting the last. The resulting track was the right
    length and made entirely of the final clip repeated.
    """
    mangled = "clips/Morphy_Duke_1858.11.02_000_intro"
    assert with_audio_extension(mangled, ".wav").name == (
        "Morphy_Duke_1858.11.02_000_intro.wav"
    )


def test_an_existing_extension_is_not_doubled():
    assert with_audio_extension("a/b.wav", ".wav").name == "b.wav"


def test_extension_matching_is_case_insensitive():
    assert with_audio_extension("a/b.WAV", ".wav").name == "b.WAV"


def test_a_plain_name_still_gets_its_extension():
    assert with_audio_extension("a/plain", ".mp3").name == "plain.mp3"


# ---------------------------------------------------------------------------
# WAV handling
# ---------------------------------------------------------------------------

def test_duration_is_read_from_the_file(tmp_path):
    clip = write_tone(tmp_path / "a.wav", 1.5)
    assert wav_duration_ms(clip) == pytest.approx(1500, abs=5)


def test_concatenation_reports_where_each_clip_starts(tmp_path):
    clips = [
        write_tone(tmp_path / "a.wav", 1.0),
        write_tone(tmp_path / "b.wav", 2.0),
        write_tone(tmp_path / "c.wav", 0.5),
    ]
    starts = concat_wavs(clips, tmp_path / "track.wav", gap_ms=500)
    assert starts == [0, 1500, 4000]


def test_the_track_is_as_long_as_its_parts_plus_the_gaps(tmp_path):
    clips = [write_tone(tmp_path / f"{i}.wav", 1.0) for i in range(3)]
    track = tmp_path / "track.wav"
    concat_wavs(clips, track, gap_ms=400)
    assert wav_duration_ms(track) == pytest.approx(3800, abs=20)


def test_offsets_and_track_agree_exactly(tmp_path):
    """The renderer trusts these to line up; they are computed together."""
    clips = [write_tone(tmp_path / f"{i}.wav", 0.7) for i in range(4)]
    track = tmp_path / "track.wav"
    starts = concat_wavs(clips, track, gap_ms=DEFAULT_GAP_MS)
    last_end = starts[-1] + wav_duration_ms(clips[-1])
    assert wav_duration_ms(track) == pytest.approx(last_end, abs=20)


def test_mismatched_clips_are_refused(tmp_path):
    clips = [
        write_tone(tmp_path / "a.wav", 0.5, framerate=22050),
        write_tone(tmp_path / "b.wav", 0.5, framerate=44100),
    ]
    with pytest.raises(AudioError, match="different audio parameters"):
        concat_wavs(clips, tmp_path / "track.wav")


def test_concatenating_nothing_is_an_error(tmp_path):
    with pytest.raises(AudioError):
        concat_wavs([], tmp_path / "track.wav")


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

def test_every_line_becomes_a_cue(script, tmp_path):
    audio = narrate(script, tmp_path, provider="mock")
    assert len(audio.cues) == len(script.lines)


def test_each_beat_gets_its_own_file(script, tmp_path):
    """The dotted-filename bug made all of these the same path."""
    audio = narrate(script, tmp_path, provider="mock")
    paths = [cue.audio_path for cue in audio.cues]
    assert len(set(paths)) == len(paths)
    assert all(Path(p).exists() for p in paths)


def test_cues_carry_the_plies_they_describe(script, tmp_path):
    """This is what lets a renderer put the right position on the board."""
    audio = narrate(script, tmp_path, provider="mock")
    for cue, line in zip(audio.cues, script.lines):
        assert cue.plies == line.plies


def test_cues_are_laid_out_end_to_end_without_overlap(script, tmp_path):
    audio = narrate(script, tmp_path, provider="mock")
    for earlier, later in zip(audio.cues, audio.cues[1:]):
        assert later.start_ms >= earlier.end_ms


def test_the_first_cue_starts_at_zero(script, tmp_path):
    assert narrate(script, tmp_path, provider="mock").cues[0].start_ms == 0


def test_the_track_is_written(script, tmp_path):
    audio = narrate(script, tmp_path, provider="mock")
    assert Path(audio.track_path).exists()
    assert audio.total_ms > 0


def test_narrating_an_empty_script_is_an_error(script, tmp_path):
    empty = script.model_copy(update={"lines": []})
    with pytest.raises(AudioError):
        narrate(empty, tmp_path, provider="mock")


def test_the_timeline_is_written_next_to_the_track(script, tmp_path):
    audio = narrate(script, tmp_path, provider="mock")
    timeline = write_timeline(audio)
    assert timeline.exists()
    assert timeline.suffix == ".json"
    assert str(timeline).endswith(".timeline.json")


def test_drift_compares_the_estimate_against_the_audio(script, tmp_path):
    """
    The honesty check on the pacing model. It was 1.56 before
    WORDS_PER_SECOND was calibrated against measured audio.
    """
    audio = narrate(script, tmp_path, provider="mock")
    assert audio.drift_ratio > 0


# ---------------------------------------------------------------------------
# Rate mapping
# ---------------------------------------------------------------------------

#: Measured on Microsoft David: SAPI dial setting -> actual words per minute.
MEASURED_WPM = {
    -5: 110.6, -4: 123.5, -3: 138.3, -2: 154.8, -1: 172.8,
    0: 188.5, 1: 217.1, 2: 241.2, 3: 272.4,
}


@pytest.mark.parametrize("wpm,expected_dial", [(110, -5), (138, -3), (150, -2), (188, 0)])
def test_words_per_minute_maps_to_the_right_dial(wpm, expected_dial):
    assert _wpm_to_sapi_rate(wpm) == expected_dial


def test_the_chosen_dial_is_the_closest_one_available():
    """
    Regression: the documented curve (0 = 200 wpm, ends threefold) picked -3
    for 150 wpm, which is 138 -- a 9 percent error inherited by every budget.
    """
    for wpm in (120, 150, 180, 220):
        dial = _wpm_to_sapi_rate(wpm)
        best = min(MEASURED_WPM, key=lambda d: abs(MEASURED_WPM[d] - wpm))
        assert dial == best, f"{wpm} wpm chose {dial}, closest is {best}"


def test_the_dial_is_clamped_to_its_range():
    assert _wpm_to_sapi_rate(5) == -10
    assert _wpm_to_sapi_rate(5000) == 10
