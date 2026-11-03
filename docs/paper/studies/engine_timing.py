"""
How long one position actually takes, measured rather than recalled.

Section V-F reports per-game analysis times and Section VII-A quotes a rate
per position. A reviewer divided one by the other and found they disagree by
about 1.7x, which means at least one of them was wrong. This measures the
rate directly, on the two games the paper reports, under the configuration
the paper claims: 1,000,000 nodes, MultiPV 3, a single search thread, one
game at a time and nothing else running.

Positions, not plies, is the correct denominator: `analyse_game` evaluates
the position after move i as the position before move i+1, so an N-ply game
costs N+1 evaluations, less any repeated position the engine cache serves.

Run:  python docs/paper/studies/engine_timing.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path("docs/paper/studies/engine_timing.json")
OPERA = Path("data/sample_game.pgn")
REPEATS = 2


def time_game(game, nodes: int = 1_000_000):
    from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine

    config = AnalysisConfig(nodes=nodes)
    assert config.threads == 1, "this measurement is only meaningful single-threaded"
    engine = get_engine("stockfish", config)
    started = time.perf_counter()
    analyse_game(game, engine)
    return time.perf_counter() - started


def main() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from pacing_study import collect
    from voxplay.chessgame.pgn_source import load_pgn, parse_pgn_text

    cases = [("Opera Game", load_pgn(OPERA))]
    for chunk in collect(200):
        game = parse_pgn_text(chunk)
        if 75 <= game.total_plies <= 85:
            cases.append(("corpus game %s" % game.meta.game_id, game))
            break

    results = []
    for label, game in cases:
        plies = game.total_plies
        positions = plies + 1
        runs = []
        for _ in range(REPEATS):
            runs.append(time_game(game.model_copy(deep=True)))
        best = min(runs)
        results.append({
            "label": label, "plies": plies, "positions": positions,
            "runs_s": [round(r, 1) for r in runs],
            "seconds": round(best, 1),
            "s_per_position": round(best / positions, 3),
        })
        print("%-22s %3d plies, %3d positions: %s  -> %.2f s/position"
              % (label, plies, positions,
                 " / ".join("%.1f s" % r for r in runs), best / positions),
              flush=True)

    rate = sum(r["seconds"] for r in results) / sum(
        r["positions"] for r in results)
    print("\npooled rate: %.2f s per position at 1,000,000 nodes, "
          "single-threaded" % rate)
    OUT.write_text(json.dumps(
        {"nodes": 1_000_000, "threads": 1, "repeats": REPEATS,
         "pooled_s_per_position": round(rate, 3), "cases": results},
        indent=2), encoding="utf-8")
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
