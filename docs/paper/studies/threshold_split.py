"""
M5: fit the classification thresholds on one split, report kappa on another.

The defaults were originally fitted to maximise agreement on all 339 games
and kappa was then reported on the same corpus. That measures how well a
three-parameter grid search can fit 24,255 points, which is not the question.
This splits the games in half, searches on one half only, and reports kappa on
the other -- plus, for contrast, kappa for the community 10/20/30 bands that
the project shipped before any of this was measured.

Run:  python docs/paper/studies/threshold_split.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List, Sequence

from voxplay.chessgame.agreement import (
    AnnotatedMove, annotated_moves, read_games, rule_agreement,
)
from voxplay.chessgame.evaluation import ClassificationThresholds

PGN = Path("data/games/annotated.pgn")
OUT = Path("docs/paper/studies/threshold_split.json")
SEED = 20261102

#: Grid searched on the training half. Steps of 1 point of win probability;
#: anything finer is below the resolution at which Lichess's own labels and
#: ours could be expected to agree.
GRID = [float(x) for x in range(2, 31)]


def load_by_game() -> List[List[AnnotatedMove]]:
    text = PGN.read_text(encoding="utf-8", errors="replace")
    per_game = []
    for game in read_games(text):
        moves = annotated_moves(game)
        if moves:
            per_game.append(moves)
    return per_game


def search(moves: Sequence[AnnotatedMove]) -> tuple[ClassificationThresholds, float]:
    """Exhaustive grid search for the kappa-maximising thresholds."""
    best, best_kappa = None, -2.0
    for inaccuracy in GRID:
        for mistake in GRID:
            if mistake <= inaccuracy:
                continue
            for blunder in GRID:
                if blunder <= mistake:
                    continue
                thresholds = ClassificationThresholds(
                    inaccuracy=inaccuracy, mistake=mistake, blunder=blunder
                )
                kappa = rule_agreement(moves, thresholds).kappa
                if kappa > best_kappa:
                    best, best_kappa = thresholds, kappa
    return best, best_kappa


def main() -> None:
    per_game = load_by_game()
    random.Random(SEED).shuffle(per_game)
    half = len(per_game) // 2
    train_games, test_games = per_game[:half], per_game[half:]
    train = [m for g in train_games for m in g]
    test = [m for g in test_games for m in g]

    print(f"{len(per_game)} games -> train {len(train_games)} games "
          f"({len(train)} moves), test {len(test_games)} games ({len(test)} moves)")

    fitted, train_kappa = search(train)
    print(f"\nfitted on train: {fitted.inaccuracy:.0f} / {fitted.mistake:.0f} / "
          f"{fitted.blunder:.0f}   train kappa {train_kappa:.3f}")

    report = rule_agreement(test, fitted)
    shipped = rule_agreement(test, ClassificationThresholds())
    community = rule_agreement(
        test, ClassificationThresholds(inaccuracy=10.0, mistake=20.0, blunder=30.0)
    )
    full = rule_agreement([m for g in per_game for m in g], ClassificationThresholds())

    print(f"\nHELD-OUT kappa, fitted thresholds      {report.kappa:.3f}")
    print(f"HELD-OUT kappa, shipped 5/10/15        {shipped.kappa:.3f}")
    print(f"HELD-OUT kappa, community 10/20/30     {community.kappa:.3f}")
    print(f"whole corpus, shipped 5/10/15          {full.kappa:.3f}")
    print(f"\nheld-out raw agreement {100 * report.observed:.1f}%  "
          f"chance {100 * report.expected:.1f}%")
    for cls in ("inaccuracy", "mistake", "blunder"):
        recall = report.error_recall(cls)
        if recall is not None:
            print(f"  held-out agreement on {cls:<11} {100 * recall:5.1f}%")

    OUT.write_text(json.dumps({
        "seed": SEED,
        "games": len(per_game),
        "train_games": len(train_games), "train_moves": len(train),
        "test_games": len(test_games), "test_moves": len(test),
        "fitted": [fitted.inaccuracy, fitted.mistake, fitted.blunder],
        "train_kappa": round(train_kappa, 4),
        "test_kappa_fitted": round(report.kappa, 4),
        "test_kappa_shipped": round(shipped.kappa, 4),
        "test_kappa_community": round(community.kappa, 4),
        "full_corpus_kappa_shipped": round(full.kappa, 4),
        "test_raw_agreement": round(report.observed, 4),
        "test_expected": round(report.expected, 4),
        "test_recall": {
            c: (None if report.error_recall(c) is None
                else round(report.error_recall(c), 4))
            for c in ("inaccuracy", "mistake", "blunder")
        },
        "test_matrix": report.matrix,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
