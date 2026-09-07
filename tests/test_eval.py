"""Tests for the evaluation harness: checks and runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxplay.commentary.engine import CommentaryLine
from voxplay.eval.checks import (
    QualityFlag,
    check_factual_grounding,
    check_length_violations,
    check_name_grounding,
    check_phrase_repetition,
    check_repeated_openings,
    run_all_checks,
)
from voxplay.eval.runner import MatchEvalResult, run_match_eval
from voxplay.events.models import BallEvent, Extras, ExtrasType, MatchState, Outcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ball(idx: int, runs: int = 0, shot_tag: str | None = None,
          over: int = 0, ball: int = 1, score: int = 0) -> BallEvent:
    return BallEvent(
        match_id="test",
        innings=1,
        over=over,
        ball=ball,
        bowler="Bowler",
        batsman_striker="Batsman",
        outcome=Outcome(runs=runs, shot_tag=shot_tag),
        state=MatchState(score=score, wickets=0, overs_complete=over, balls_in_over=ball),
    )


def _line(idx: int, text: str, event_type: str = "ball") -> CommentaryLine:
    return CommentaryLine(event_index=idx, event_type=event_type, text=text)


# ---------------------------------------------------------------------------
# check_repeated_openings
# ---------------------------------------------------------------------------

def test_repeated_opening_detected():
    events = [_ball(i) for i in range(4)]
    lines = [
        _line(0, "Pushed back to the bowler, no run."),
        _line(1, "Driven nicely through the covers for four!"),
        _line(2, "Pushed back another dot ball."),   # "pushed back" repeats
        _line(3, "Lovely ball, defended solidly."),
    ]
    flags = check_repeated_openings(lines, events)
    checks = [f.check for f in flags]
    assert "repeated_opening" in checks


def test_no_repeated_opening_when_varied():
    events = [_ball(i) for i in range(4)]
    lines = [
        _line(0, "Driven through covers, four runs."),
        _line(1, "Short ball, pulled away for a single."),
        _line(2, "Full delivery, driven nicely."),
        _line(3, "Back of a length, defended solidly."),
    ]
    flags = check_repeated_openings(lines, events)
    assert not flags


def test_repeated_opening_only_flags_ball_events():
    events = [_ball(0), _ball(1)]
    lines = [
        _line(0, "A new over begins.", event_type="over_start"),
        _line(1, "A new chapter opens.", event_type="over_start"),
    ]
    flags = check_repeated_openings(lines, events)
    assert not flags  # over_start events are excluded


# ---------------------------------------------------------------------------
# check_phrase_repetition
# ---------------------------------------------------------------------------

def test_phrase_repetition_detected():
    events = [_ball(i) for i in range(3)]
    base = "the bowler is right on the money"
    lines = [
        _line(0, f"Good length, {base}."),
        _line(1, "A fine delivery outside off stump."),
        _line(2, f"Excellent line, {base} again."),  # repeats
    ]
    flags = check_phrase_repetition(lines, events, window=5)
    assert any(f.check == "phrase_repetition" for f in flags)


def test_phrase_repetition_respects_window():
    """Phrase repeated outside the window should not be flagged."""
    events = [_ball(i) for i in range(8)]
    repeated_phrase = "strokes it sweetly through the gap"
    fillers = [
        "Good length delivery, defended back.",
        "Short ball outside off, left alone.",
        "Full toss, driven straight to mid-on.",
        "Wide outside off stump, no shot played.",
        "Back of length, shoulder arms taken.",
        "Swinging late, missed completely outside.",
    ]
    lines = [_line(0, f"He {repeated_phrase}.")] + \
            [_line(i + 1, fillers[i]) for i in range(6)] + \
            [_line(7, f"And he {repeated_phrase} again.")]
    flags = check_phrase_repetition(lines, events, window=3)
    # With window=3, events 0 and 7 are too far apart
    assert not flags


# ---------------------------------------------------------------------------
# check_length_violations
# ---------------------------------------------------------------------------

def test_length_violation_three_sentences():
    events = [_ball(0)]
    lines = [_line(0, "That's a cracking shot. It races to the boundary. The crowd erupts in delight.")]
    flags = check_length_violations(lines, events)
    assert len(flags) == 1
    assert flags[0].check == "length_violation"


def test_length_ok_two_sentences():
    events = [_ball(0)]
    lines = [_line(0, "That's a cracking shot! It races to the boundary.")]
    flags = check_length_violations(lines, events)
    assert not flags


def test_length_violation_not_checked_for_over_start():
    events = [_ball(0)]
    lines = [_line(0, "Over starts. Bowler marks out. Here we go. Good length.", event_type="over_start")]
    flags = check_length_violations(lines, events)
    assert not flags


# ---------------------------------------------------------------------------
# check_factual_grounding
# ---------------------------------------------------------------------------

def test_grounding_shot_flagged_when_no_shot_tag():
    ev = _ball(0, runs=1, shot_tag=None)
    # Commentary mentions "driven" but event has no shot_tag
    line = _line(0, "Driven firmly down the ground for a single.")
    flags = check_factual_grounding([line], [ev])
    shot_flags = [f for f in flags if f.check == "grounding_shot"]
    assert shot_flags


def test_grounding_shot_not_flagged_when_shot_tag_present():
    ev = _ball(0, runs=4, shot_tag="cover_drive")
    line = _line(0, "Driven beautifully through the covers for four!")
    flags = check_factual_grounding([line], [ev])
    shot_flags = [f for f in flags if f.check == "grounding_shot"]
    assert not shot_flags


def test_grounding_number_flags_unknown_stat():
    ev = _ball(0, runs=1, score=15)
    ev.state.score = 15
    # Commentary mentions "42" which isn't in the event
    line = _line(0, "They need 42 more runs to win.")
    flags = check_factual_grounding([line], [ev])
    num_flags = [f for f in flags if f.check == "grounding_number"]
    assert num_flags


def test_grounding_number_ok_for_known_stat():
    ev = _ball(0, runs=4, score=50)
    ev.state.score = 50
    # "50" is the score, so it's known
    line = _line(0, "That takes them to 50 -- a fine start.")
    flags = check_factual_grounding([line], [ev])
    num_flags = [f for f in flags if f.check == "grounding_number"]
    assert not num_flags


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------

def test_run_all_checks_returns_sorted():
    events = [_ball(i) for i in range(3)]
    lines = [
        _line(0, "Pushed back firmly. Good start."),
        _line(1, "Pushed away for a single."),   # repeated opening
        _line(2, "Short, pulled hard but straight."),
    ]
    flags = run_all_checks(lines, events)
    indices = [f.event_index for f in flags]
    assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# run_match_eval (integration)
# ---------------------------------------------------------------------------

def test_run_match_eval_sample(tmp_path):
    """Full pipeline: load sample match, run mock eval, write transcript."""
    data_dir = Path(__file__).parent.parent / "data"
    sample = data_dir / "sample_match.json"
    if not sample.exists():
        pytest.skip("sample_match.json not found")

    result = run_match_eval(sample, tmp_path, mock=True)

    assert isinstance(result, MatchEvalResult)
    assert result.event_count > 0
    assert result.ball_event_count > 0
    assert len(result.transcript_lines) == result.event_count
    assert result.transcript_path is not None
    assert result.transcript_path.exists()


def test_run_match_eval_transcript_readable(tmp_path):
    """Transcript file should contain the match ID and flag summary."""
    data_dir = Path(__file__).parent.parent / "data"
    sample = data_dir / "sample_match.json"
    if not sample.exists():
        pytest.skip("sample_match.json not found")

    result = run_match_eval(sample, tmp_path, mock=True)
    text = result.transcript_path.read_text(encoding="utf-8")

    assert "demo_t20_001" in text          # match_id present
    assert "QUALITY FLAGS" in text          # summary section present
    assert "heuristic warnings" in text     # disclaimer present


def test_run_match_eval_collapse(tmp_path):
    data_dir = Path(__file__).parent.parent / "data" / "test_matches"
    path = data_dir / "eval_collapse.json"
    if not path.exists():
        pytest.skip("eval_collapse.json not found")

    result = run_match_eval(path, tmp_path, mock=True)
    assert result.match_id == "eval_collapse_001"
    assert result.ball_event_count >= 10


def test_run_match_eval_chase(tmp_path):
    data_dir = Path(__file__).parent.parent / "data" / "test_matches"
    path = data_dir / "eval_chase.json"
    if not path.exists():
        pytest.skip("eval_chase.json not found")

    result = run_match_eval(path, tmp_path, mock=True)
    assert result.match_id == "eval_chase_001"


def test_run_match_eval_last_over(tmp_path):
    data_dir = Path(__file__).parent.parent / "data" / "test_matches"
    path = data_dir / "eval_last_over.json"
    if not path.exists():
        pytest.skip("eval_last_over.json not found")

    result = run_match_eval(path, tmp_path, mock=True)
    assert result.match_id == "eval_last_over_001"
    # Last-over match should have exactly 6 ball events in the final over of the chase.
    # over=19 (0-indexed) renders as "20.X" in the summary label.
    # Filter by "need" in state_line to select only chase (innings 2) balls.
    final_over_balls = [
        t for t in result.transcript_lines
        if t.event_type == "ball" and "20." in t.event_summary and "need" in t.state_line
    ]
    assert len(final_over_balls) == 6


def test_flag_rate_calculation(tmp_path):
    data_dir = Path(__file__).parent.parent / "data"
    sample = data_dir / "sample_match.json"
    if not sample.exists():
        pytest.skip("sample_match.json not found")

    result = run_match_eval(sample, tmp_path, mock=True)
    # flag_rate should be between 0 and 1
    for check in ["repeated_opening", "phrase_repetition", "length_violation"]:
        rate = result.flag_rate(check)
        assert 0.0 <= rate <= 1.0


# ---------------------------------------------------------------------------
# check_factual_grounding: derived-number fix
# ---------------------------------------------------------------------------

def _chase_ball(score: int, target: int, balls_remaining: int,
                runs: int = 1) -> BallEvent:
    """Helper: ball event in a chase with explicit target and balls_remaining."""
    return BallEvent(
        match_id="test",
        innings=2,
        over=0,
        ball=1,
        bowler="Bowler",
        batsman_striker="Batsman",
        outcome=Outcome(runs=runs),
        state=MatchState(
            score=score, wickets=2, overs_complete=0, balls_in_over=1,
            target=target, balls_remaining=balls_remaining,
        ),
    )


def test_grounding_runs_needed_not_flagged():
    """Correct 'X needed' (= target - score) must not trigger grounding_number."""
    ev = _chase_ball(score=130, target=182, balls_remaining=33)
    # 52 = 182 - 130; model correctly says 52 needed
    line = _line(0, "They need 52 more to win from 33 balls.")
    flags = check_factual_grounding([line], [ev])
    num_flags = [f for f in flags if f.check == "grounding_number"]
    assert not num_flags, f"runs_needed=52 should not be flagged: {num_flags}"


def test_grounding_wrong_runs_still_flagged():
    """An invented run count that is NOT target - score must still flag."""
    ev = _chase_ball(score=130, target=182, balls_remaining=33)
    # 186 is neither score, target, runs_needed(52), nor any other known value
    line = _line(0, "They need 186 more to win.")
    flags = check_factual_grounding([line], [ev])
    num_flags = [f for f in flags if f.check == "grounding_number"]
    assert num_flags, "wrong run count 186 should still be flagged"


def test_grounding_wrong_balls_still_flagged():
    """A fabricated ball count that doesn't match balls_remaining must still flag."""
    ev = _chase_ball(score=154, target=182, balls_remaining=13)
    # balls_remaining=13 but model says "7 balls" — should flag
    line = _line(0, "28 needed off 7 balls — an almost impossible ask.")
    flags = check_factual_grounding([line], [ev])
    num_flags = [f for f in flags if f.check == "grounding_number"]
    assert num_flags, "fabricated '7 balls' (actual 13) should be flagged"


def test_grounding_overs_remaining_not_flagged():
    """Full overs remaining (balls_remaining // 6) must not flag."""
    ev = _chase_ball(score=100, target=182, balls_remaining=48)
    # 48 // 6 = 8 overs remaining — model correctly says 8
    line = _line(0, "Eight overs to go, 82 still required.")
    flags = check_factual_grounding([line], [ev])
    # "82" = 182-100 = runs_needed, "8" would not be a digit in "Eight"
    # "82" should not flag now that runs_needed is accepted
    num_flags = [f for f in flags if f.check == "grounding_number"]
    assert not num_flags, f"runs_needed=82 should not be flagged: {num_flags}"


# ---------------------------------------------------------------------------
# check_name_grounding
# ---------------------------------------------------------------------------

def _innings_start(batting: str, bowling: str) -> "InningsStartEvent":
    from voxplay.events.models import InningsStartEvent
    return InningsStartEvent(
        match_id="test",
        innings=1,
        batting_team=batting,
        bowling_team=bowling,
        state=MatchState(),
    )


def test_name_grounding_hallucinated_team_flagged():
    """Real-world cricket nation mentioned in a match it has no part in must flag."""
    events = [_innings_start("Titans", "Raiders"), _ball(0)]
    lines = [
        _line(0, "And INDIA WIN off the last ball!", event_type="innings_start"),
        _line(1, "And INDIA WIN off the last ball!"),
    ]
    flags = check_name_grounding(lines, events)
    assert any(f.check == "name_grounding" for f in flags)


def test_name_grounding_known_team_not_flagged():
    """Fictitious team name (not in _CRICKET_COUNTRIES) must not flag."""
    events = [_innings_start("Titans", "Raiders"), _ball(0)]
    lines = [
        _line(0, "Great start.", event_type="innings_start"),
        _line(1, "TITANS are on fire tonight!"),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


def test_name_grounding_cricket_caps_not_flagged():
    """Common cricket words in all-caps (FOUR, etc.) must not flag."""
    events = [_innings_start("Falcons", "Eagles"), _ball(0)]
    lines = [
        _line(0, "Ready.", event_type="innings_start"),
        _line(1, "FOUR! Driven beautifully through the covers."),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


def test_name_grounding_only_checks_ball_events():
    """name_grounding must only run on ball events, not innings_start."""
    events = [_innings_start("Titans", "Raiders"), _ball(0)]
    lines = [
        _line(0, "INDIA WIN the toss!", event_type="innings_start"),
        _line(1, "A quiet dot ball."),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


# ---------------------------------------------------------------------------
# True positives: all three known live-transcript hallucinations must flag
# ---------------------------------------------------------------------------

def test_name_grounding_india_win_all_caps_flagged():
    """'INDIA WIN' (all-caps) must flag in a Titans vs Raiders match."""
    events = [_innings_start("Titans", "Raiders"), _ball(0)]
    lines = [
        _line(0, "Great start.", event_type="innings_start"),
        _line(1, "And INDIA WIN off the last ball!"),
    ]
    flags = check_name_grounding(lines, events)
    assert any(f.check == "name_grounding" for f in flags)


def test_name_grounding_south_africa_title_case_flagged():
    """'South Africa win' (title-case) must flag in a Thunderbolts vs Stormriders match."""
    events = [_innings_start("Thunderbolts", "Stormriders"), _ball(0)]
    lines = [
        _line(0, "Great start.", event_type="innings_start"),
        _line(1, "South Africa win off the last ball!"),
    ]
    flags = check_name_grounding(lines, events)
    assert any(f.check == "name_grounding" for f in flags)


def test_name_grounding_india_lowercase_flagged():
    """'as India build momentum' (lowercase) must flag in a non-India match."""
    events = [_innings_start("Thunderbolts", "Stormriders"), _ball(0)]
    lines = [
        _line(0, "Great start.", event_type="innings_start"),
        _line(1, "as India build momentum here, the asking rate climbs."),
    ]
    flags = check_name_grounding(lines, events)
    assert any(f.check == "name_grounding" for f in flags)


def test_name_grounding_real_team_not_flagged_when_in_match():
    """'South Africa' must NOT flag when it IS one of the match teams."""
    events = [_innings_start("South Africa", "India"), _ball(0)]
    lines = [
        _line(0, "Great start.", event_type="innings_start"),
        _line(1, "South Africa need 12 more off this over."),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


# ---------------------------------------------------------------------------
# False positives: emphatic all-caps words must NEVER flag
# ---------------------------------------------------------------------------

def test_name_grounding_launches_not_flagged():
    """'he LAUNCHES that one' — emphatic verb, not a team name."""
    events = [_innings_start("Falcons", "Eagles"), _ball(0)]
    lines = [
        _line(0, "Ready.", event_type="innings_start"),
        _line(1, "He LAUNCHES that one into the stands for six!"),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


def test_name_grounding_game_not_flagged():
    """'this is the GAME' — emphatic noun, not a team name."""
    events = [_innings_start("Falcons", "Eagles"), _ball(0)]
    lines = [
        _line(0, "Ready.", event_type="innings_start"),
        _line(1, "This is the GAME right here — every ball counts!"),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


def test_name_grounding_passed_not_flagged():
    """'the 150 has been PASSED' — emphatic past tense, not a team name."""
    events = [_innings_start("Falcons", "Eagles"), _ball(0)]
    lines = [
        _line(0, "Ready.", event_type="innings_start"),
        _line(1, "The 150 has been PASSED and the innings is building nicely."),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


def test_name_grounding_complete_not_flagged():
    """'the chase is COMPLETE' — emphatic adjective, not a team name."""
    events = [_innings_start("Falcons", "Eagles"), _ball(0)]
    lines = [
        _line(0, "Ready.", event_type="innings_start"),
        _line(1, "And the chase is COMPLETE!"),
    ]
    flags = check_name_grounding(lines, events)
    assert not any(f.check == "name_grounding" for f in flags)


# ---------------------------------------------------------------------------
# Task 2 + 3: grounding_shot context-aware push/pushed
# ---------------------------------------------------------------------------

def test_grounding_pushed_to_mid_on_not_flagged():
    """'pushed firmly to mid-on' (no shot_tag) must NOT flag grounding_shot."""
    ev = _ball(0, runs=0, shot_tag=None)
    line = _line(0, "Pushed firmly to mid-on, solid defence.")
    flags = check_factual_grounding([line], [ev])
    shot_flags = [f for f in flags if f.check == "grounding_shot"]
    assert not shot_flags, f"'pushed to mid-on' is direction not a named shot: {shot_flags}"


def test_grounding_pushed_straight_to_cover_not_flagged():
    """'pushed firmly but straight to cover' (no shot_tag) must NOT flag."""
    ev = _ball(0, runs=0, shot_tag=None)
    line = _line(0, "Pushed firmly but straight to cover, no run.")
    flags = check_factual_grounding([line], [ev])
    shot_flags = [f for f in flags if f.check == "grounding_shot"]
    assert not shot_flags, f"direction to fielder must not flag: {shot_flags}"


def test_grounding_pushed_through_covers_flagged():
    """'pushed through the covers for four' (no shot_tag) MUST flag grounding_shot."""
    ev = _ball(0, runs=4, shot_tag=None)
    line = _line(0, "Pushed through the covers for four!")
    flags = check_factual_grounding([line], [ev])
    shot_flags = [f for f in flags if f.check == "grounding_shot"]
    assert shot_flags, "push through covers with no shot_tag should flag — invented shot path"


def test_grounding_pushed_with_shot_tag_not_flagged():
    """When shot_tag IS present, push commentary must not flag regardless."""
    ev = _ball(0, runs=1, shot_tag="push")
    line = _line(0, "Pushed through mid-wicket for a single.")
    flags = check_factual_grounding([line], [ev])
    shot_flags = [f for f in flags if f.check == "grounding_shot"]
    assert not shot_flags
