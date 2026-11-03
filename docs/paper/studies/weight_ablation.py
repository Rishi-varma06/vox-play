"""
M4: sensitivity of beat selection to each hand-set salience weight.

The six weights were set by hand. That is a legitimate objection, and the
answer is to measure how much each one actually changes the output: a weight
whose removal changes nothing is not carrying the result, and a weight whose
removal changes everything needs justifying.

For each game the selector is run once at the shipped weights and once with
each signal zeroed in turn. Reported per variant:

  retained    share of the shipped selection the variant still picks
  jaccard     overlap of the two selected ply sets
  value       total salience of the variant's picks, scored under the
              SHIPPED weights, as a fraction of the shipped selection's --
              so a variant that picks worse moves scores below 1.0 on the
              objective the system is actually trying to maximise
  turning     share of games where the variant still covers the turning point

Analysed games are cached as JSON, so re-running this or any later study over
the same corpus costs no engine time.

Run:  python docs/paper/studies/weight_ablation.py --games 60
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Tuple

OUT = Path("docs/paper/studies/weight_ablation.json")
CACHE = Path("docs/paper/studies/analysed")

SIGNALS = ("swing", "narrative", "motif", "criticality", "material", "think_time")


def _worker(payload: Tuple[str, int]) -> dict:
    pgn_text, nodes = payload
    try:
        from voxplay.chessgame.beats import (
            BeatType, DEFAULT_WEIGHTS, PacingConfig, budget_for, extract_arc,
            salience, select_beats, _think_baselines,
        )
        from voxplay.chessgame.engine import (
            AnalysisConfig, analyse_game, get_engine,
        )
        from voxplay.chessgame.pgn_source import parse_pgn_text

        game = parse_pgn_text(pgn_text)
        cached = CACHE / ("%s.json" % game.meta.game_id)
        if cached.exists():
            game = type(game).model_validate_json(
                cached.read_text(encoding="utf-8"))
        else:
            analyse_game(game, get_engine(
                "stockfish", AnalysisConfig(nodes=nodes)))
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(game.model_dump_json(), encoding="utf-8")

        pacing = PacingConfig(budget_seconds=budget_for(game))
        arc = extract_arc(game)
        think = _think_baselines(game.moves)
        moment_types = (BeatType.MOMENT, BeatType.TURNING_POINT)

        # Salience under the SHIPPED weights, the objective every variant is
        # scored against.
        shipped_score = {
            move.ply: salience(move, arc, think, DEFAULT_WEIGHTS)
            for move in game.moves if move.analysis is not None
        }

        def picks(weights) -> List[int]:
            sheet = select_beats(game, weights, pacing)
            return [b.plies[0] for b in sheet.beats
                    if b.beat_type in moment_types]

        base = picks(DEFAULT_WEIGHTS)
        base_set = set(base)
        base_value = sum(shipped_score.get(p, 0.0) for p in base)

        variants: Dict[str, dict] = {}
        for signal in SIGNALS:
            chosen = picks(replace(DEFAULT_WEIGHTS, **{signal: 0.0}))
            chosen_set = set(chosen)
            union = base_set | chosen_set
            value = sum(shipped_score.get(p, 0.0) for p in chosen)
            variants[signal] = {
                "picks": len(chosen),
                "retained": (len(base_set & chosen_set) / len(base_set)
                             if base_set else None),
                "jaccard": (len(base_set & chosen_set) / len(union)
                            if union else None),
                "value_ratio": (value / base_value if base_value else None),
                "covers_turning_point": arc.turning_point_ply in chosen_set,
            }

        return {
            "game_id": game.meta.game_id,
            "plies": game.total_plies,
            "base_picks": len(base),
            "base_value": round(base_value, 4),
            "base_covers_turning_point": arc.turning_point_ply in base_set,
            # A drawn game can have no turning point, and scoring those as
            # misses understates every arm equally but misstates the level.
            "has_turning_point": arc.turning_point_ply is not None,
            "variants": variants,
        }
    except Exception as exc:  # noqa: BLE001 - one game failing is not fatal
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def main() -> None:
    import multiprocessing
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=60)
    parser.add_argument("--nodes", type=int, default=1_000_000)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from pacing_study import collect  # noqa: PLC0415

    chunks = collect(args.games)
    print("%d games, %s nodes, %d workers"
          % (len(chunks), format(args.nodes, ","), args.workers), flush=True)

    rows: List[dict] = []
    started = time.time()
    payloads = [(chunk, args.nodes) for chunk in chunks]
    with multiprocessing.Pool(args.workers) as pool:
        for index, record in enumerate(
            pool.imap_unordered(_worker, payloads), start=1
        ):
            rows.append(record)
            rate = (time.time() - started) / index
            print("[%d/%d] %10s %4sp eta %5.1fm"
                  % (index, len(payloads), record.get("game_id", "ERROR"),
                     record.get("plies", "?"),
                     (len(payloads) - index) * rate / 60), flush=True)
            if "error" in record:
                print("    " + record["error"], flush=True)

    good = [r for r in rows if "error" not in r]
    # Games with no turning point cannot give one a beat, so they are
    # excluded from that column rather than counted as misses.
    eligible = [r for r in good if r.get("has_turning_point", True)]
    summary = {
        "games": len(good),
        "games_with_turning_point": len(eligible),
        "nodes": args.nodes,
        "base_turning_point_coverage": round(
            sum(1 for r in eligible if r["base_covers_turning_point"])
            / len(eligible), 4),
        "signals": {},
        "rows": good,
    }
    print()
    print("%-13s %9s %9s %9s %9s" % ("signal removed", "retained", "jaccard",
                                     "value", "turning pt"))
    print("-" * 54)
    for signal in SIGNALS:
        retained = [r["variants"][signal]["retained"] for r in good
                    if r["variants"][signal]["retained"] is not None]
        jaccard = [r["variants"][signal]["jaccard"] for r in good
                   if r["variants"][signal]["jaccard"] is not None]
        value = [r["variants"][signal]["value_ratio"] for r in good
                 if r["variants"][signal]["value_ratio"] is not None]
        turning = sum(1 for r in eligible
                      if r["variants"][signal]["covers_turning_point"])
        entry = {
            "mean_retained": round(statistics.fmean(retained), 4),
            "mean_jaccard": round(statistics.fmean(jaccard), 4),
            "mean_value_ratio": round(statistics.fmean(value), 4),
            "turning_point_coverage": round(turning / len(eligible), 4),
        }
        summary["signals"][signal] = entry
        print("%-13s %8.1f%% %9.3f %9.3f %8.1f%%"
              % (signal, 100 * entry["mean_retained"], entry["mean_jaccard"],
                 entry["mean_value_ratio"],
                 100 * entry["turning_point_coverage"]))

    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nbaseline turning-point coverage %.1f%%"
          % (100 * summary["base_turning_point_coverage"]))
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
