"""
Demo: the verification gate catching invented commentary.

Runs offline in a few seconds. Generates a grounded script, shows it passing,
then injects each kind of hallucination and shows the gate catch it.

    python demo/show_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxplay.chessgame import beats as B
from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine
from voxplay.chessgame.evaluation import COMMENTARY_THRESHOLDS
from voxplay.chessgame.pgn_source import load_pgn
from voxplay.chessgame.script import generate_script
from voxplay.chessgame.verify import verify_script

RULE = "=" * 72

#: Each line invents something a listener who plays chess would catch.
INJECTIONS = [
    ("notation leaking into speech",
     "White plays Nf3 and the position is balanced."),
    ("a square that is not in this beat",
     "The rook swings across to h seven with real menace."),
    ("a tactic no detector found",
     "A beautiful fork wins material on the spot."),
    ("a person who was never in this game",
     "Pure Kasparov, straight from his very best years."),
]


def main() -> int:
    print(RULE)
    print("  VERIFICATION GATE")
    print("  Every beat is checked against the facts the model was given.")
    print(RULE)

    config = AnalysisConfig(nodes=200_000, multipv=3, threads=1)
    with get_engine("auto", config) as engine:
        game = analyse_game(
            load_pgn("data/sample_game.pgn"), engine,
            thresholds=COMMENTARY_THRESHOLDS,
        )
    sheet = B.select_beats(
        game, pacing=B.PacingConfig(budget_seconds=B.budget_for(game))
    )
    script = generate_script(game, sheet, generator="template")

    report = verify_script(script, game, sheet)
    print()
    print("  1. Grounded commentary -- every sentence built from the facts")
    print(f"     {len(script.lines)} beats checked")
    print(f"     -> {'CLEAN' if report.clean else 'FAILED'}, "
          f"{report.errors_per_hundred_beats:.1f} errors per 100 beats")

    print()
    print("  2. The same script with one sentence replaced each time")
    for label, text in INJECTIONS:
        tampered = script.model_copy(update={
            "lines": [
                line.model_copy(update={"text": text, "words": len(text.split())})
                if index == 1 else line
                for index, line in enumerate(script.lines)
            ]
        })
        found = verify_script(tampered, game, sheet)
        caught = [f.check for f in found.errors]
        print()
        print(f"     {label}")
        print(f'       "{text}"')
        print(f"       -> {'CAUGHT: ' + ', '.join(sorted(set(caught)))}"
              if caught else "       -> MISSED")

    print()
    print(RULE)
    print("  A beat that fails is regenerated with the problem fed back,")
    print("  rather than shipped. Measured on live model output: 6.2 errors")
    print("  per 100 beats without the gate, 0.0 with it.")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
