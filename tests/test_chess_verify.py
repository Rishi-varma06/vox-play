"""
Tests for the verification gate.

Two halves, and both are necessary. The first shows the gate passes
commentary that is grounded -- the template generator builds every sentence
from the facts, so a finding there is a false positive. The second shows it
CATCHES each kind of invention, because a verifier that never fires proves
nothing at all.

Hallucinations are injected deliberately rather than waited for: the point is
to know the gate works before trusting a number it produces.
"""

from __future__ import annotations

import pytest

from voxplay.chessgame import beats as B
from voxplay.chessgame.engine import MockAnalysisEngine, analyse_game
from voxplay.chessgame.pgn_source import load_pgn
from voxplay.chessgame.script import CommentaryScript, ScriptLine, TemplateGenerator
from voxplay.chessgame.verify import (
    ERROR,
    WARNING,
    Finding,
    NOTATION,
    check_repetition,
    licence_for,
    spoken_squares,
    verify_script,
)
from pathlib import Path

SAMPLE_PGN = Path(__file__).resolve().parents[1] / "data" / "sample_game.pgn"


@pytest.fixture(scope="module")
def game():
    return analyse_game(load_pgn(SAMPLE_PGN), MockAnalysisEngine())


@pytest.fixture(scope="module")
def sheet(game):
    return B.select_beats(game)


@pytest.fixture(scope="module")
def script(game, sheet):
    return TemplateGenerator().generate(game, sheet)


def rewrite(script: CommentaryScript, index: int, text: str) -> CommentaryScript:
    """A copy of `script` with one beat's text replaced."""
    lines = [l.model_copy() for l in script.lines]
    lines[index] = lines[index].model_copy(update={"text": text,
                                                   "words": len(text.split())})
    return script.model_copy(update={"lines": lines})


def checks_fired(report) -> set:
    return {f.check for f in report.findings}


# ---------------------------------------------------------------------------
# Grounded commentary passes
# ---------------------------------------------------------------------------

def test_the_template_generator_passes_clean(script, game, sheet):
    """
    It builds every sentence from the facts, so any finding here is a false
    positive in the gate rather than a defect in the commentary.
    """
    report = verify_script(script, game, sheet)
    assert report.clean, [f.message for f in report.errors]


def test_the_engines_suggested_move_is_licensed(script, game, sheet):
    """
    Regression. The facts name the engine's preference ("better was king to d
    eight"), so the narration may too. The first licence omitted it and
    flagged a fact the model had been handed.
    """
    report = verify_script(script, game, sheet)
    assert "ungrounded_square" not in checks_fired(report)


def test_a_clean_script_reports_a_zero_rate(script, game, sheet):
    assert verify_script(script, game, sheet).errors_per_hundred_beats == 0.0


# ---------------------------------------------------------------------------
# Invention is caught
# ---------------------------------------------------------------------------

def test_notation_in_spoken_text_is_caught(script, game, sheet):
    bad = rewrite(script, 1, "White plays Nf3 and the position is balanced.")
    assert "notation_leak" in checks_fired(verify_script(bad, game, sheet))


def test_a_bare_square_is_caught(script, game, sheet):
    bad = rewrite(script, 1, "The knight settles on e4 and eyes the centre.")
    assert "notation_leak" in checks_fired(verify_script(bad, game, sheet))


def test_a_square_from_another_part_of_the_game_is_caught(script, game, sheet):
    bad = rewrite(script, 1, "The rook swings across to h seven with menace.")
    assert "ungrounded_square" in checks_fired(verify_script(bad, game, sheet))


def test_an_invented_tactic_is_caught(script, game, sheet):
    bad = rewrite(script, 1, "A beautiful fork wins material on the spot.")
    assert "ungrounded_tactic" in checks_fired(verify_script(bad, game, sheet))


def test_an_invented_player_is_caught(script, game, sheet):
    bad = rewrite(script, 1, "This is pure Kasparov, straight from his best years.")
    assert "ungrounded_name" in checks_fired(verify_script(bad, game, sheet))


def test_a_contradicted_evaluation_is_caught(game, sheet):
    """Saying Black is winning in a position White has won."""
    line = ScriptLine(
        beat_type=B.BeatType.MOMENT, plies=[game.moves[-1].ply],
        text="Black is completely winning here.", words=5, est_seconds=2.0,
    )
    script = CommentaryScript(
        game_id=game.meta.game_id, generator="test", strategy="salience", lines=[line]
    )
    game.moves[-1].analysis.win_pct_after = 99.0
    report = verify_script(script, game, sheet)
    assert "evaluation_contradiction" in checks_fired(report)


def test_findings_are_errors_not_warnings(script, game, sheet):
    """Invention is a defect; style problems are not."""
    bad = rewrite(script, 1, "A beautiful fork wins material on the spot.")
    report = verify_script(bad, game, sheet)
    assert all(f.severity == ERROR for f in report.findings
               if f.check == "ungrounded_tactic")


def test_the_rate_rises_with_the_errors(script, game, sheet):
    bad = rewrite(script, 1, "A beautiful fork settles it, says Kasparov.")
    assert verify_script(bad, game, sheet).errors_per_hundred_beats > 0


def test_every_finding_names_the_beat_it_came_from(script, game, sheet):
    bad = rewrite(script, 2, "A beautiful fork wins material.")
    for finding in verify_script(bad, game, sheet).findings:
        assert 0 <= finding.beat_index < len(bad.lines)


# ---------------------------------------------------------------------------
# Style warnings
# ---------------------------------------------------------------------------

def test_repeated_phrasing_is_a_warning_not_an_error():
    phrase = "the rook lifts to the third rank and swings across"
    lines = [
        ScriptLine(beat_type=B.BeatType.MOMENT, plies=[], text=phrase,
                   words=10, est_seconds=4.0)
        for _ in range(2)
    ]
    script = CommentaryScript(game_id="x", generator="t", strategy="s", lines=lines)
    findings = check_repetition(script)
    assert findings and all(f.severity == WARNING for f in findings)


def test_distinct_phrasing_is_not_flagged():
    lines = [
        ScriptLine(beat_type=B.BeatType.MOMENT, plies=[],
                   text="The knight leaps into the centre with real purpose.",
                   words=9, est_seconds=4.0),
        ScriptLine(beat_type=B.BeatType.MOMENT, plies=[],
                   text="A quiet pawn push changes the shape of everything.",
                   words=9, est_seconds=4.0),
    ]
    script = CommentaryScript(game_id="x", generator="t", strategy="s", lines=lines)
    assert check_repetition(script) == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("knight to f three", {"f3"}),
        ("bishop takes the rook on d seven", {"d7"}),
        ("from a one to h eight", {"a1", "h8"}),
        ("no squares at all here", set()),
    ],
)
def test_spoken_squares_are_recovered(text, expected):
    assert spoken_squares(text) == expected


@pytest.mark.parametrize("text", ["Nf3", "Qxh7", "e4", "a8=Q", "Rd8"])
def test_notation_patterns_are_recognised(text):
    assert NOTATION.search(text)


@pytest.mark.parametrize(
    "text", ["knight to f three", "the position is balanced", "castles kingside"]
)
def test_spoken_english_is_not_mistaken_for_notation(text):
    assert not NOTATION.search(text)


def test_the_licence_mirrors_the_beat_it_describes(script, game, sheet):
    licence = licence_for(script.lines[1], game, sheet)
    assert licence.squares
    assert licence.names


# ---------------------------------------------------------------------------
# Licence / fact-block symmetry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("opening,spoken", [
    ("Nimzo-Larsen Attack", "NimzoLarsen"),
    ("Czech-Indian Defense", "CzechIndian"),
    ("Van't Kruijs Opening", "Vant"),
    ("Bishop's Opening", "Bishops"),
])
def test_hyphenated_opening_names_are_licensed(game, sheet, opening, spoken):
    """
    An opening name the facts supply must survive the name check.

    `check_names` strips punctuation from each whitespace-delimited token, so
    "Nimzo-Larsen Attack" reaches it as "NimzoLarsen" and "Attack". The
    licence used to join the whole string instead, licensing
    "NimzoLarsenAttack" and nothing else, so an opening the model had been
    handed came back as an invented name. That accounted for 12 of the 15
    false positives in the gate measurement, every one an opening name.
    """
    patched = game.model_copy(deep=True)
    patched.meta.opening = opening
    line = ScriptLine(
        beat_type=B.BeatType.OPENING,
        plies=[],
        text=f"They are into the {spoken} here, a line with its own ideas.",
    )
    licence = licence_for(line, patched, sheet)
    assert _spelling_of(spoken) in licence.names


def _spelling_of(word: str) -> str:
    from voxplay.chessgame.verify import _spelling
    return _spelling(word)
