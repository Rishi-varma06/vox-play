"""
Why a control firing is an upper bound on false positives, with the counts.

`motif_precision.py` treats a detector firing on a control puzzle as a false
positive, which is the conservative reading. Lichess theme tags are not
exhaustive, so some of those firings are untagged true instances -- a puzzle
tagged only `mateIn2` that happens to deliver mate on the back rank really is
a back-rank mate.

This records, for every control puzzle a detector fires on, which themes that
puzzle actually carries, so the claim in the paper is a measurement rather
than an excuse.

Run:  python docs/paper/studies/control_firings.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict

import chess

from voxplay.chessgame.models import Motif
from voxplay.chessgame.validate import (
    CONTROL_THEMES, SCORABLE, detected_motifs, sample_database,
)

ARCHIVE = Path("data/lichess_db_puzzle.csv.zst")
OUT = Path("docs/paper/studies/control_firings.json")

#: Detectors whose control firings are worth explaining: the ones whose
#: precision is visibly below 1.0 in the main table.
OF_INTEREST = (
    Motif.BACK_RANK_MATE, Motif.FORK, Motif.SKEWER, Motif.PIN,
    Motif.HANGING_PIECE, Motif.DISCOVERED_ATTACK,
)


def mates_on_back_rank(puzzle) -> bool:
    """Whether the solution line actually ends in checkmate on a back rank."""
    try:
        board = chess.Board(puzzle.fen)
    except ValueError:
        return False
    for uci in puzzle.solution:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return False
        if move not in board.legal_moves:
            return False
        board.push(move)
    if not board.is_checkmate():
        return False
    king = board.king(board.turn)
    return king is not None and chess.square_rank(king) in (0, 7)


def main() -> None:
    themes = [SCORABLE[m] for m in SCORABLE]
    print("drawing the same control pool as the precision study ...", flush=True)
    _, controls = sample_database(
        ARCHIVE, themes, 1, controls=2000, progress=True
    )
    print("%d controls" % len(controls), flush=True)

    summary: Dict[str, dict] = {}
    for motif in OF_INTEREST:
        theme = SCORABLE[motif]
        fired = [
            puzzle for puzzle in controls
            if theme not in puzzle.themes and motif in detected_motifs(puzzle)
        ]
        tags = Counter(
            tag for puzzle in fired for tag in puzzle.themes
            if tag in CONTROL_THEMES
        )
        entry = {
            "fired_on_controls": len(fired),
            "control_pool": sum(
                1 for p in controls if theme not in p.themes),
            "their_control_themes": dict(tags.most_common()),
        }
        if motif is Motif.BACK_RANK_MATE:
            genuine = sum(1 for puzzle in fired if mates_on_back_rank(puzzle))
            entry["line_really_ends_in_back_rank_mate"] = genuine
            entry["share_genuine"] = (
                round(genuine / len(fired), 4) if fired else None)
        summary[motif.value] = entry
        print("  %-18s fired on %4d controls   %s"
              % (motif.value, len(fired), dict(tags.most_common(3))),
              flush=True)
        if motif is Motif.BACK_RANK_MATE and fired:
            print("      of which the line really does end in back-rank "
                  "mate: %d (%.0f%%)"
                  % (entry["line_really_ends_in_back_rank_mate"],
                     100 * entry["share_genuine"]), flush=True)

    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main()
