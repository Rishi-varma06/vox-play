"""
m1: per-detector precision, recall and F1, not recall alone.

The validator already counted control firings; the paper reported only the
recall column. This writes the whole confusion matrix per detector so
precision can be stated rather than implied, and records the counts that
settle how many motifs, themes and comparable detectors there actually are.

What precision means here, exactly
----------------------------------
Positives are puzzles Lichess tags with the detector's own theme. Negatives
are puzzles drawn from five common themes that are not motifs at all
(mateIn2, endgame, middlegame, crushing, advantage) and that carry no tag
under test. Precision is therefore TP / (TP + FP) AT THIS SAMPLING RATIO
-- 1,000 positives against 2,000 negatives -- and not an estimate of
precision in running commentary, where motif prevalence is far lower.

A control firing is also an UPPER bound on false positives, because Lichess
theme tags are not exhaustive: a puzzle tagged only `middlegame` may contain
a genuine fork that nobody tagged. Precision below is correspondingly a lower
bound. Both facts are stated in the paper rather than buried here.

Run:  python docs/paper/studies/motif_precision.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from voxplay.chessgame.models import Motif
from voxplay.chessgame.validate import (
    CONTROL_THEMES, NOT_COMPARABLE, SCORABLE, detected_motifs, sample_database,
)

ARCHIVE = Path("data/lichess_db_puzzle.csv.zst")
OUT = Path("docs/paper/studies/motif_precision.json")

PER_THEME = 1000
CONTROLS = 2000


def f1(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    if not precision or not recall:
        return None
    return 2 * precision * recall / (precision + recall)


def main() -> None:
    motifs = list(SCORABLE)
    themes = [SCORABLE[m] for m in motifs]
    print("%d detectors, %d themes under test, %d control themes"
          % (len(motifs), len(themes), len(CONTROL_THEMES)))
    print("sampling %d per theme + %d controls from %s"
          % (PER_THEME, CONTROLS, ARCHIVE), flush=True)

    started = time.time()
    sampled, controls = sample_database(
        ARCHIVE, themes, PER_THEME, controls=CONTROLS, progress=True
    )
    print("sampled in %.0fs" % (time.time() - started), flush=True)

    # Detect once per puzzle rather than once per (puzzle, detector): the
    # detectors all run in one pass anyway, and this turns twelve passes over
    # the control pool into one.
    control_hits = [
        (set(puzzle.themes), detected_motifs(puzzle)) for puzzle in controls
    ]

    rows: List[dict] = []
    for motif in motifs:
        theme = SCORABLE[motif]
        positives = sampled.get(theme, [])
        true_positive = sum(
            1 for puzzle in positives if motif in detected_motifs(puzzle)
        )
        negatives = [
            found for tags, found in control_hits if theme not in tags
        ]
        false_positive = sum(1 for found in negatives if motif in found)

        recall = true_positive / len(positives) if positives else None
        denominator = true_positive + false_positive
        precision = true_positive / denominator if denominator else None
        specificity = (
            (len(negatives) - false_positive) / len(negatives)
            if negatives else None
        )

        rows.append({
            "motif": motif.value,
            "theme": theme,
            "comparable": motif not in NOT_COMPARABLE,
            "not_comparable_reason": NOT_COMPARABLE.get(motif),
            "positives": len(positives),
            "true_positive": true_positive,
            "false_negative": len(positives) - true_positive,
            "negatives": len(negatives),
            "false_positive": false_positive,
            "true_negative": len(negatives) - false_positive,
            "recall": None if recall is None else round(recall, 4),
            "precision": None if precision is None else round(precision, 4),
            "specificity": None if specificity is None else round(specificity, 4),
            "f1": None if f1(precision, recall) is None
                  else round(f1(precision, recall), 4),
        })
        print("  %-18s n=%4d  recall %5.1f%%  precision %5.1f%%  F1 %5.3f  "
              "fired on %d/%d controls"
              % (motif.value, len(positives), 100 * (recall or 0),
                 100 * (precision or 0), f1(precision, recall) or 0,
                 false_positive, len(negatives)), flush=True)

    comparable = [r for r in rows if r["comparable"] and r["positives"] >= 20]
    summary: Dict[str, object] = {
        "detectors": len(motifs),
        "themes_under_test": len(themes),
        "control_themes": list(CONTROL_THEMES),
        "per_theme": PER_THEME,
        "controls_drawn": len(controls),
        "puzzles_total": sum(r["positives"] for r in rows) + len(controls),
        "comparable_detectors": len(comparable),
        "mean_recall": round(
            sum(r["recall"] for r in comparable) / len(comparable), 4),
        "mean_precision": round(
            sum(r["precision"] for r in comparable if r["precision"])
            / sum(1 for r in comparable if r["precision"]), 4),
        "mean_f1": round(
            sum(r["f1"] for r in comparable if r["f1"])
            / sum(1 for r in comparable if r["f1"]), 4),
        "rows": rows,
    }

    print()
    print("comparable detectors: %d of %d" % (len(comparable), len(motifs)))
    print("mean recall    %.1f%%" % (100 * summary["mean_recall"]))
    print("mean precision %.1f%%" % (100 * summary["mean_precision"]))
    print("mean F1        %.3f" % summary["mean_f1"])
    print("puzzles used   %s" % format(summary["puzzles_total"], ","))

    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main()
