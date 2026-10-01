"""
Tests for the script layer.

The Claude generator is exercised through a stubbed `_ask`, so the two-pass
orchestration, prompt construction and assembly are all covered without a
network call or an API key.

The tests that matter most are the grounding ones: nothing that reaches the
speech backend may contain chess notation, and nothing may reach the model
that it was not given as a fact.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voxplay.chessgame import beats as B
from voxplay.chessgame.engine import MockAnalysisEngine, analyse_game
from voxplay.chessgame.pgn_source import load_pgn
from voxplay.chessgame.script import (
    ClaudeGenerator,
    DEFAULT_STYLE,
    ScriptError,
    ScriptStyle,
    TemplateGenerator,
    beat_facts,
    beat_prompt,
    generate_script,
    get_generator,
    outline_prompt,
)

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"

#: Algebraic notation that must never reach the speech backend: a piece letter
#: with a destination square, a bare square, or a check/mate suffix.
NOTATION = re.compile(r"\b[KQRBN][a-h]?[1-8]?x?[a-h][1-8]\b|\b[a-h][1-8]\b|[+#]")


@pytest.fixture(scope="module")
def game():
    return analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())


@pytest.fixture(scope="module")
def sheet(game):
    return B.select_beats(game)


class StubClaude(ClaudeGenerator):
    """ClaudeGenerator with the network replaced by a recorder."""

    def __init__(self, reply: str = "A line of commentary."):
        super().__init__()
        self.reply = reply
        self.prompts: list[str] = []

    def _client(self):
        return object()

    def _ask(self, client, prompt, max_tokens, effort):
        self.prompts.append(prompt)
        return self.reply


# ---------------------------------------------------------------------------
# Template generator
# ---------------------------------------------------------------------------

def test_every_beat_becomes_a_line(game, sheet):
    script = TemplateGenerator().generate(game, sheet)
    assert len(script.lines) == len(sheet.beats)


def test_lines_keep_their_beat_type_and_plies(game, sheet):
    script = TemplateGenerator().generate(game, sheet)
    for line, beat in zip(script.lines, sheet.beats):
        assert line.beat_type == beat.beat_type
        assert line.plies == beat.plies


def test_the_template_never_speaks_notation(game, sheet):
    """
    The grounding test. Motif descriptions carry raw squares as data, and this
    is the boundary where they must already have been converted.
    """
    script = TemplateGenerator().generate(game, sheet)
    for line in script.lines:
        assert not NOTATION.search(line.text), f"notation leaked: {line.text!r}"


def test_articles_agree_with_the_word_that_follows(game, sheet):
    """'a inaccuracy' is the kind of slip a listener notices immediately."""
    text = TemplateGenerator().generate(game, sheet).full_text
    assert "a inaccuracy" not in text
    assert "a error" not in text


def test_duration_is_measured_from_the_words_actually_written(game, sheet):
    """Reads the constant rather than a literal -- it is calibrated, not fixed."""
    script = TemplateGenerator().generate(game, sheet)
    assert script.total_words > 0
    assert script.total_seconds == pytest.approx(
        script.total_words / B.WORDS_PER_SECOND, abs=0.5
    )


def test_the_budget_is_recorded_for_comparison(game, sheet):
    """Budgeted vs written duration says whether the pacing model holds."""
    script = TemplateGenerator().generate(game, sheet)
    assert script.budgeted_seconds == sheet.total_seconds


def test_the_template_is_deterministic(game, sheet):
    first = TemplateGenerator().generate(game, sheet).full_text
    second = TemplateGenerator().generate(game, sheet).full_text
    assert first == second


def test_the_script_reports_which_strategy_paced_it(game, sheet):
    assert TemplateGenerator().generate(game, sheet).strategy == "salience"


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def test_facts_never_contain_raw_squares(game, sheet):
    """
    The model is told never to write notation, so it must never be shown any.
    """
    for beat in sheet.beats:
        facts = beat_facts(beat, game, sheet, DEFAULT_STYLE)
        assert not re.search(r"\b[a-h][1-8]\b", facts), f"raw square in: {facts!r}"


def test_facts_state_a_target_length(game, sheet):
    for beat in sheet.beats:
        assert "target length" in beat_facts(beat, game, sheet, DEFAULT_STYLE)


def test_a_beginner_audience_is_told_to_explain_terms(game, sheet):
    tactical = [
        b for b in sheet.beats
        if b.beat_type in (B.BeatType.MOMENT, B.BeatType.TURNING_POINT)
    ]
    beginner = "\n".join(
        beat_facts(b, game, sheet, ScriptStyle(audience_elo=800)) for b in tactical
    )
    club = "\n".join(
        beat_facts(b, game, sheet, ScriptStyle(audience_elo=2000)) for b in tactical
    )
    assert "explain" in beginner
    assert "explain" not in club


def test_spoiler_free_mode_withholds_the_result(game, sheet):
    intro = next(b for b in sheet.beats if b.beat_type == B.BeatType.INTRO)
    hidden = beat_facts(intro, game, sheet, ScriptStyle(spoiler_free=True))
    shown = beat_facts(intro, game, sheet, ScriptStyle(spoiler_free=False))
    assert "result" not in hidden
    assert "result" in shown


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def test_the_beat_prompt_carries_the_outline_and_recent_lines(game, sheet):
    prompt = beat_prompt(
        sheet.beats[2], game, sheet, DEFAULT_STYLE, "THE THROUGH LINE", ["said before"]
    )
    assert "THE THROUGH LINE" in prompt
    assert "said before" in prompt


def test_the_first_beat_says_it_is_first(game, sheet):
    prompt = beat_prompt(sheet.beats[0], game, sheet, DEFAULT_STYLE, "x", [])
    assert "first line" in prompt


def test_the_outline_prompt_lists_the_running_order(game, sheet):
    prompt = outline_prompt(game, sheet, DEFAULT_STYLE)
    for beat in sheet.beats:
        assert beat.beat_type.value in prompt


def test_the_persona_reaches_the_prompt(game, sheet):
    energetic = beat_prompt(
        sheet.beats[0], game, sheet, ScriptStyle(persona="energetic"), "x", []
    )
    assert "Lively" in energetic


# ---------------------------------------------------------------------------
# Claude generator (stubbed)
# ---------------------------------------------------------------------------

def test_generation_is_two_pass(game, sheet):
    """One outline call, then one call per beat."""
    stub = StubClaude()
    stub.generate(game, sheet)
    assert len(stub.prompts) == len(sheet.beats) + 1


def test_the_first_call_is_the_outline(game, sheet):
    stub = StubClaude()
    stub.generate(game, sheet)
    assert "Do not write any commentary yet" in stub.prompts[0]


def test_each_beat_sees_what_was_already_said(game, sheet):
    """Continuity, and the only way it can avoid repeating itself."""
    stub = StubClaude(reply="A distinctive opening phrase.")
    stub.generate(game, sheet)
    assert "A distinctive opening phrase." in stub.prompts[2]


def test_the_generated_script_is_assembled(game, sheet):
    script = StubClaude(reply="Three words here.").generate(game, sheet)
    assert len(script.lines) == len(sheet.beats)
    assert script.total_words == 3 * len(sheet.beats)


def test_empty_replies_are_dropped(game, sheet):
    assert StubClaude(reply="").generate(game, sheet).lines == []


class RepairingStub(ClaudeGenerator):
    """Invents something on the first attempt at each beat, then behaves."""

    def __init__(self):
        super().__init__(max_repairs=1)
        self.prompts: list[str] = []
        self.attempts: dict[int, int] = {}

    def _client(self):
        return object()

    def _ask(self, client, prompt, max_tokens, effort):
        self.prompts.append(prompt)
        if "Do not write any commentary yet" in prompt:
            return "outline"
        beat = prompt.count("THIS BEAT")
        self.attempts[len(self.prompts)] = beat
        if "REJECTED" in prompt:
            return "A quiet move, and the game goes on."
        return "A beautiful fork decides it, says Kasparov."


def test_a_beat_that_invents_something_is_regenerated(game, sheet):
    """
    The gate, not just the report. An unsupported claim costs one retry with
    the problem fed back, which is cheaper and more honest than shipping it
    and reporting the rate afterwards.
    """
    stub = RepairingStub()
    script = stub.generate(game, sheet)
    assert any("REJECTED" in p for p in stub.prompts)
    assert all("fork" not in line.text for line in script.lines)


def test_the_rejection_tells_the_model_what_was_wrong(game, sheet):
    stub = RepairingStub()
    stub.generate(game, sheet)
    rejection = next(p for p in stub.prompts if "REJECTED" in p)
    assert "ungrounded" in rejection or "tactic" in rejection or "names" in rejection


def test_repairs_can_be_disabled(game, sheet):
    """max_repairs=0 ships the first attempt, for measuring the raw rate."""
    stub = RepairingStub()
    stub.max_repairs = 0
    stub.generate(game, sheet)
    assert not any("REJECTED" in p for p in stub.prompts)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_builds_the_template_generator():
    assert get_generator("template").name == "template"


def test_factory_rejects_an_unknown_generator():
    with pytest.raises(ScriptError):
        get_generator("gpt")


def test_auto_falls_back_to_the_template_without_credentials(monkeypatch):
    """The demo path must produce a script on a machine with no API key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert get_generator("auto").name == "template"


def test_auto_uses_claude_when_a_key_is_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert get_generator("auto").name == "claude-opus-5"


def test_the_convenience_wrapper_runs_end_to_end(game, sheet):
    script = generate_script(game, sheet, generator="template")
    assert script.full_text
