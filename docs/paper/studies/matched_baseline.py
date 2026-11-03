"""
A baseline matched on everything except the separation constraint.

The top-N baseline of Section V-C gets no mandatory beats and a different
budget, so its poor opening and finish coverage is structurally guaranteed
and proves little. This runs the shipped selector against itself with the
separation parameter g of (5) set to zero: same mandatory intro, opening,
finish and outro, same word budget B from (7), same salience sigma from (2),
same greedy order. The only difference is whether (5) applies.

That makes the comparison informative in both directions. Dropping (5) can
only raise the objective, since it removes a constraint, so the question is
what it costs. Reported per game:

  moments        how many half-moves get a beat of their own
  value          total salience of those picks
  adjacent       picks sitting closer than four half-moves to another
  max gap        the longest run of half-moves between consecutive picks,
                 which is what a listener experiences as a dead stretch
  bridges        fast-forward lines the assembler had to insert
  decisive       whether the turning point gets a beat

Run:  python docs/paper/studies/matched_baseline.py --games 173
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import List, Tuple

CACHE = Path("docs/paper/studies/analysed")
OUT = Path("docs/paper/studies/matched_baseline.json")


def _measure(game, gap: int) -> dict:
    from voxplay.chessgame.beats import (
        BeatType, DEFAULT_WEIGHTS, PacingConfig, budget_for, extract_arc,
        salience, select_beats, _think_baselines,
    )

    pacing = PacingConfig(budget_seconds=budget_for(game), min_ply_gap=gap)
    sheet = select_beats(game, DEFAULT_WEIGHTS, pacing)
    arc = extract_arc(game)
    think = _think_baselines(game.moves)

    moment_types = (BeatType.MOMENT, BeatType.TURNING_POINT)
    picks = sorted(b.plies[0] for b in sheet.beats if b.beat_type in moment_types)
    scores = {m.ply: salience(m, arc, think, DEFAULT_WEIGHTS)
              for m in game.moves if m.analysis is not None}

    # Dead stretches: between the end of the opening beat, each pick, and the
    # start of the finish beat.
    opening_end = 8
    finish_start = max(1, game.total_plies - 2)
    marks = [opening_end] + picks + [finish_start]
    gaps = [b - a - 1 for a, b in zip(marks, marks[1:])]

    return {
        "moments": len(picks),
        "value": round(sum(scores.get(p, 0.0) for p in picks), 4),
        "adjacent": sum(1 for a, b in zip(picks, picks[1:]) if b - a < 4),
        "max_gap": max(gaps) if gaps else 0,
        "bridges": sum(1 for b in sheet.beats if b.beat_type is BeatType.BRIDGE),
        "seconds": sheet.total_seconds,
        "decisive": arc.turning_point_ply in set(picks),
        "has_turning_point": arc.turning_point_ply is not None,
    }


def _worker(payload: Tuple[str, int]) -> dict:
    chunk, nodes = payload
    try:
        from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine
        from voxplay.chessgame.pgn_source import ParsedGame, parse_pgn_text

        game = parse_pgn_text(chunk)
        cached = CACHE / ("%s.json" % game.meta.game_id)
        if cached.exists():
            game = ParsedGame.model_validate_json(
                cached.read_text(encoding="utf-8"))
        else:
            analyse_game(game, get_engine("stockfish", AnalysisConfig(nodes=nodes)))
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(game.model_dump_json(), encoding="utf-8")

        return {
            "game_id": game.meta.game_id,
            "plies": game.total_plies,
            "spaced": _measure(game, 4),
            "unspaced": _measure(game, 0),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def main() -> None:
    import multiprocessing
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=173)
    parser.add_argument("--nodes", type=int, default=1_000_000)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from pacing_study import collect

    chunks = collect(args.games)
    print("%d games, %d workers" % (len(chunks), args.workers), flush=True)

    rows: List[dict] = []
    started = time.time()
    payloads = [(c, args.nodes) for c in chunks]
    with multiprocessing.Pool(args.workers) as pool:
        for index, record in enumerate(pool.imap_unordered(_worker, payloads), 1):
            rows.append(record)
            rate = (time.time() - started) / index
            print("[%d/%d] %-10s eta %4.1fm"
                  % (index, len(payloads), record.get("game_id", "ERROR"),
                     (len(payloads) - index) * rate / 60), flush=True)

    good = [r for r in rows if "error" not in r]
    eligible = [r for r in good if r["spaced"]["has_turning_point"]]

    def stat(arm: str, field: str) -> float:
        return statistics.fmean(r[arm][field] for r in good)

    print()
    print("%-26s %12s %12s" % ("", "spaced g=4", "unspaced g=0"))
    print("-" * 52)
    for field, label in (("moments", "moments per game"),
                         ("value", "total salience"),
                         ("adjacent", "adjacent picks"),
                         ("max_gap", "longest dead stretch"),
                         ("bridges", "bridging lines"),
                         ("seconds", "spoken seconds")):
        print("%-26s %12.2f %12.2f"
              % (label, stat("spaced", field), stat("unspaced", field)))
    covered = {arm: sum(1 for r in eligible if r[arm]["decisive"])
               for arm in ("spaced", "unspaced")}
    print("%-26s %11.1f%% %11.1f%%"
          % ("decisive move covered",
             100 * covered["spaced"] / len(eligible),
             100 * covered["unspaced"] / len(eligible)))

    summary = {
        "games": len(good),
        "games_with_turning_point": len(eligible),
        "means": {arm: {f: round(stat(arm, f), 4) for f in
                        ("moments", "value", "adjacent", "max_gap",
                         "bridges", "seconds")}
                  for arm in ("spaced", "unspaced")},
        "decisive_covered": {
            arm: round(sum(1 for r in eligible if r[arm]["decisive"])
                       / len(eligible), 4)
            for arm in ("spaced", "unspaced")},
        "rows": good,
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main()
