"""
What the licence cannot express, demonstrated rather than asserted.

The seven checks are lexical: they test which squares, motif names, player
names and evaluation direction appear in the text. A sentence can therefore
be false about the position and still use only licensed vocabulary. Section
V-F should say so, and the claims there should be ones we have actually run
through the gate.

Each case below is a sentence that is WRONG about the game but draws only on
the licence for that beat -- squares d1, d7, e7, g3; motifs sacrifice and
hangingPiece; the players' own names. Any case the gate passes is a blind
spot. Two controls that do reach outside the licence are included to show
the checks fire when they can.

Run:  python docs/paper/studies/gate_blind_spots.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("docs/paper/studies/gate_blind_spots.json")
SAMPLE = Path("data/sample_game.pgn")

#: (name, what makes it false, the sentence)
CASES = [
    ("wrong side",
     "gives the move to the player who did not make it; White played it",
     "Black sacrifices on d seven and the position opens up."),
    ("wrong piece",
     "a licensed square, but the piece standing on it is a rook",
     "The bishop takes on d seven and the position opens up."),
    ("motif on the wrong move",
     "a licensed tactic attached to a move that did not create it",
     "The sacrifice here is on g three, quietly setting everything up."),
    ("unanchored strategy",
     "a strategic claim with no square, motif or name to check",
     "Black has the better structure here and simply controls the centre."),
    ("implied result",
     "reveals the outcome without naming it",
     "There is nothing left to play for; the board says it all now."),
    ("invented square (control)",
     "names a square the facts never supplied -- should be caught",
     "The rook swings to h seven and decides it."),
    ("invented name (control)",
     "names a player not in the game -- should be caught",
     "Pure Kasparov, this."),
]


def main() -> None:
    from voxplay.chessgame import beats as B
    from voxplay.chessgame.engine import MockAnalysisEngine, analyse_game
    from voxplay.chessgame.pgn_source import load_pgn
    from voxplay.chessgame.script import ScriptLine
    from voxplay.chessgame.verify import licence_for, verify_line

    game = analyse_game(load_pgn(SAMPLE), MockAnalysisEngine())
    sheet = B.select_beats(game)
    beat = next(b for b in sheet.beats
                if b.beat_type in (B.BeatType.MOMENT, B.BeatType.TURNING_POINT))

    results = []
    for name, why, text in CASES:
        line = ScriptLine(beat_type=beat.beat_type, plies=list(beat.plies),
                          text=text)
        licence = licence_for(line, game, sheet)
        findings = verify_line(line, 0, licence)
        errors = [f.check for f in findings if f.severity == "error"]
        results.append({"case": name, "why": why, "text": text,
                        "caught": bool(errors), "checks": errors})
        print("%-28s %-7s %s" % (name, "CAUGHT" if errors else "PASSES",
                                 ",".join(errors) or "-"))

    blind = [r for r in results if not r["caught"]
             and "control" not in r["case"]]
    controls = [r for r in results if "control" in r["case"]]
    print()
    print("blind spots: %d of %d semantic cases pass every check"
          % (len(blind), len(results) - len(controls)))
    print("controls:    %d of %d lexical cases are caught, as they should be"
          % (sum(1 for r in controls if r["caught"]), len(controls)))
    OUT.write_text(json.dumps({"cases": results}, indent=2), encoding="utf-8")
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
