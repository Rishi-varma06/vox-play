"""
Agreement between the pipeline people actually run and Lichess's labels.

Section V-B compares our classification RULE against Lichess's, with both
reading Lichess's own `[%eval]`. Any disagreement there is the thresholds or
the win-probability conversion, which is close to tautological: we adopted
Lichess's formula and recovered Lichess's thresholds, so a high kappa mostly
says the implementation is faithful.

What that does not test is the system. The shipped pipeline does not read
Lichess's evaluations; it runs Stockfish at 1,000,000 nodes on one thread and
classifies from that. This measures the same kappa against the same Lichess
NAGs, but from our engine's evaluations -- the number that validates what
people run. It should be the lower of the two, because it now carries every
disagreement between two engines at two search efforts as well.

Only the held-out half of the split in `threshold_split.py` is used, under
the same seed, so the thresholds were never fitted on these games.

Run:  python docs/paper/studies/pipeline_agreement.py --workers 10
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Dict, List, Tuple

PGN = Path("data/games/annotated.pgn")
CACHE = Path("docs/paper/studies/analysed")
OUT = Path("docs/paper/studies/pipeline_agreement.json")
SEED = 20261102   # the seed threshold_split.py uses, so the split matches


def held_out_chunks() -> List[str]:
    """The test half of the threshold split, as raw PGN."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from pacing_study import is_standard, split_pgn

    text = PGN.read_text(encoding="utf-8", errors="replace")
    standard = [c for c in split_pgn(text) if is_standard(c)]
    random.Random(SEED).shuffle(standard)
    return standard[len(standard) // 2:]


def _worker(payload: Tuple[str, int]) -> dict:
    chunk, nodes = payload
    try:
        from voxplay.chessgame.agreement import NOT_AN_ERROR, annotated_moves, read_games
        from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine
        from voxplay.chessgame.models import MoveQuality
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

        # Lichess's verdict per ply, from the NAGs on the same PGN.
        theirs = {m.ply: m.lichess for m in annotated_moves(next(read_games(chunk)))}

        errors = (MoveQuality.INACCURACY, MoveQuality.MISTAKE, MoveQuality.BLUNDER)
        pairs = []
        for move in game.moves:
            if move.analysis is None or move.ply not in theirs:
                continue
            quality = move.analysis.quality
            ours = quality.value if quality in errors else NOT_AN_ERROR
            pairs.append((ours, theirs[move.ply]))
        return {"game_id": game.meta.game_id, "pairs": pairs}
    except Exception as exc:  # noqa: BLE001 - one game failing is not fatal
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def main() -> None:
    import multiprocessing

    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=1_000_000)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    from voxplay.chessgame.agreement import CLASSES, compare

    chunks = held_out_chunks()
    print("%d held-out games, %s nodes, %d workers"
          % (len(chunks), format(args.nodes, ","), args.workers), flush=True)

    rows: List[dict] = []
    started = time.time()
    payloads = [(c, args.nodes) for c in chunks]
    with multiprocessing.Pool(args.workers) as pool:
        for index, record in enumerate(pool.imap_unordered(_worker, payloads), 1):
            rows.append(record)
            rate = (time.time() - started) / index
            print("[%d/%d] %-10s %4s moves  eta %4.1fm"
                  % (index, len(payloads), record.get("game_id", "ERROR"),
                     len(record.get("pairs", [])),
                     (len(payloads) - index) * rate / 60), flush=True)
            if "error" in record:
                print("    " + record["error"], flush=True)

    good = [r for r in rows if "error" not in r]
    pairs = [tuple(p) for r in good for p in r["pairs"]]
    report = compare(pairs, basis="our engine at %s nodes" % format(args.nodes, ","))

    print()
    print(report.table())

    summary = {
        "nodes": args.nodes,
        "games": len(good),
        "moves": report.moves,
        "kappa": round(report.kappa, 4),
        "observed": round(report.observed, 4),
        "expected": round(report.expected, 4),
        "recall": {c: (None if report.error_recall(c) is None
                       else round(report.error_recall(c), 4))
                   for c in CLASSES[1:]},
        "matrix": report.matrix,
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main()
