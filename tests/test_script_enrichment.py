"""
Facts the narrator is given, and the licence that must match them.

Two of these guard the same invariant from opposite sides: beat_facts and
licence_for have to move together. A fact handed over but not licensed gets
flagged as an invention; a claim licensed but never supplied becomes a blind
spot in the gate.
"""

from __future__ import annotations

import pytest

from voxplay.chessgame.beats import Archetype, BeatType, extract_arc, select_beats
from voxplay.chessgame.models import MoveQuality
from voxplay.chessgame.pgn_source import load_pgn
from voxplay.chessgame.script import (
    ARCHETYPE_NOTE,
    BEST_LINE_PLIES,
    DEFAULT_STYLE,
    _arc_note,
    _spoken_line,
    beat_facts,
    outline_prompt,
)


# ---------------------------------------------------------------------------
# The engine's continuation
# ---------------------------------------------------------------------------

def test_spoken_line_drops_the_move_already_named():
    # The facts name the preferred move on its own line; repeating it here
    # would have the narrator say it twice in one breath.
    spoken = _spoken_line(["Kd8", "Rd1", "Qe6"])

    assert "king to d eight" not in spoken
    assert spoken.startswith("rook to d one")


def test_spoken_line_is_capped():
    long_line = ["Kd8", "Rd1", "Qe6", "Bxd7", "Nxd7", "Rb8", "Nxb8", "Rd8"]
    spoken = _spoken_line(long_line)

    assert spoken.count("then") == BEST_LINE_PLIES - 1


def test_spoken_line_carries_no_notation():
    spoken = _spoken_line(["Kd8", "Bxd7+", "Nf6"])

    for token in ("Bxd7", "Nf6", "+", "#"):
        assert token not in spoken


@pytest.mark.parametrize("san_moves", [[], ["Kd8"], ["Kd8", ""]])
def test_spoken_line_is_empty_when_there_is_nothing_to_add(san_moves):
    assert _spoken_line(san_moves) == ""


# ---------------------------------------------------------------------------
# Archetype guidance
# ---------------------------------------------------------------------------

def test_every_archetype_except_unknown_has_guidance():
    # UNKNOWN deliberately has none: it means the shape could not be read,
    # and inventing a register for it is how a decisive game got told as a
    # draw in the first place.
    for archetype in Archetype:
        if archetype is Archetype.UNKNOWN:
            assert archetype.value not in ARCHETYPE_NOTE
        else:
            assert archetype.value in ARCHETYPE_NOTE, archetype


def test_unknown_archetype_contributes_no_guidance():
    game = load_pgn("data/sample_game.pgn")
    sheet = select_beats(game)

    assert sheet.arc.archetype is Archetype.UNKNOWN
    assert _arc_note(sheet) == ""


def test_unanalysed_decisive_game_is_not_called_a_draw():
    # The regression this guards: archetype defaulted to DRAW, so a game
    # White won arrived at the narrator as "shape of the game: draw" beside
    # "result: White won" -- two facts that cannot both hold.
    game = load_pgn("data/sample_game.pgn")
    arc = extract_arc(game)

    assert arc.winner is not None
    assert arc.archetype is not Archetype.DRAW


def test_arc_guidance_reaches_the_outline_prompt(monkeypatch):
    game = load_pgn("data/sample_game.pgn")
    sheet = select_beats(game)
    monkeypatch.setitem(ARCHETYPE_NOTE, "unknown", "SHAPE GUIDANCE MARKER")

    assert "SHAPE GUIDANCE MARKER" in outline_prompt(game, sheet, DEFAULT_STYLE)


# ---------------------------------------------------------------------------
# Facts and licence stay in step
# ---------------------------------------------------------------------------

def _analysed_game():
    from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine

    game = load_pgn("data/sample_game.pgn")
    engine = get_engine("mock", AnalysisConfig(nodes=1000))
    return analyse_game(game, engine)


def test_engine_line_reaches_the_facts_when_a_move_was_not_best():
    game = _analysed_game()
    sheet = select_beats(game)

    moments = [
        b for b in sheet.beats
        if b.beat_type in (BeatType.MOMENT, BeatType.TURNING_POINT)
    ]
    if not moments:
        pytest.skip("mock analysis produced no moments for this game")

    texts = [beat_facts(b, game, sheet, DEFAULT_STYLE) for b in moments]
    by_ply = {m.ply: m for m in game.moves}
    expected = any(
        by_ply[p].analysis
        and not by_ply[p].analysis.is_best
        and _spoken_line(by_ply[p].analysis.best_line_san)
        for b in moments for p in b.plies if p in by_ply
    )
    if expected:
        assert any("what that line gives:" in t for t in texts)


def test_squares_in_the_engine_line_are_licensed():
    from voxplay.chessgame.script import ScriptLine
    from voxplay.chessgame.verify import licence_for
    from voxplay.chessgame.speech import speak_san
    from voxplay.chessgame.verify import spoken_squares

    game = _analysed_game()
    sheet = select_beats(game)
    by_ply = {m.ply: m for m in game.moves}

    for beat in sheet.beats:
        line = ScriptLine(
            beat_type=beat.beat_type, plies=list(beat.plies), text="placeholder"
        )
        licence = licence_for(line, game, sheet)
        for ply in beat.plies:
            analysis = by_ply[ply].analysis if ply in by_ply else None
            if analysis is None or analysis.is_best:
                continue
            for san in analysis.best_line_san[1:BEST_LINE_PLIES + 1]:
                if not san:
                    continue
                # Every square the facts expose must be sayable, or the gate
                # rejects the narrator for using what it was handed.
                for square in spoken_squares(speak_san(san)):
                    assert square in licence.squares
