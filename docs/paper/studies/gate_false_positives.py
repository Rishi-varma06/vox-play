"""
M2 (part): the verification gate's false-positive rate, measured for free.

The gate's error rate on model output says how much invention it catches. It
says nothing about how often it rejects a sentence that was fine, and a gate
that regenerates good text is a cost and a quality risk rather than a
safeguard.

The template generator assembles sentences mechanically from the same fact
block the gate licences, so every claim it makes is true by construction.
Any ERROR-severity finding against template output is therefore a false
positive, and no language model is involved, so this runs over hundreds of
beats at no API cost.

Warnings are counted separately and excluded from the rate. Template prose is
repetitive and fixed-length by design, so `repetition` and `length` warnings
are expected behaviour of the checks rather than defects of the gate.

Run:  python docs/paper/studies/gate_false_positives.py --games 40
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

OUT = Path("docs/paper/studies/gate_false_positives.json")


def _worker(payload: Tuple[str, int]) -> dict:
    pgn_text, nodes = payload
    try:
        from voxplay.chessgame.beats import (
            DEFAULT_WEIGHTS, PacingConfig, budget_for, select_beats,
        )
        from voxplay.chessgame.engine import (
            AnalysisConfig, analyse_game, get_engine,
        )
        from voxplay.chessgame.pgn_source import parse_pgn_text
        from voxplay.chessgame.script import TemplateGenerator
        from voxplay.chessgame.verify import verify_script

        game = parse_pgn_text(pgn_text)
        analyse_game(game, get_engine("stockfish", AnalysisConfig(nodes=nodes)))
        sheet = select_beats(
            game, DEFAULT_WEIGHTS,
            PacingConfig(budget_seconds=budget_for(game)),
        )
        script = TemplateGenerator().generate(game, sheet)
        report = verify_script(script, game, sheet)
        return {
            "game_id": game.meta.game_id,
            "plies": game.total_plies,
            "beats": report.beats,
            "errors": len(report.errors),
            "warnings": len(report.findings) - len(report.errors),
            "error_checks": dict(Counter(f.check for f in report.errors)),
            "warning_checks": dict(Counter(
                f.check for f in report.findings if f.severity != "error"
            )),
            "error_messages": [
                "%s: %s" % (f.check, f.message) for f in report.errors
            ][:5],
        }
    except Exception as exc:  # noqa: BLE001 - one game failing is not fatal
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def main() -> None:
    import multiprocessing

    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=40)
    parser.add_argument("--nodes", type=int, default=200_000)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    import sys
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
            print("[%d/%d] %10s beats %3s errors %2s warnings %2s"
                  % (index, len(payloads), record.get("game_id", "ERROR"),
                     record.get("beats", "?"), record.get("errors", "?"),
                     record.get("warnings", "?")), flush=True)
            if "error" in record:
                print("    " + record["error"], flush=True)

    good = [r for r in rows if "error" not in r]
    beats = sum(r["beats"] for r in good)
    errors = sum(r["errors"] for r in good)
    warnings = sum(r["warnings"] for r in good)
    error_checks: Dict[str, int] = {}
    warning_checks: Dict[str, int] = {}
    for row in good:
        for check, count in row["error_checks"].items():
            error_checks[check] = error_checks.get(check, 0) + count
        for check, count in row["warning_checks"].items():
            warning_checks[check] = warning_checks.get(check, 0) + count

    summary = {
        "games": len(good),
        "failed": len(rows) - len(good),
        "nodes": args.nodes,
        "beats": beats,
        "false_positives": errors,
        "false_positive_rate_per_100_beats": (
            round(100.0 * errors / beats, 3) if beats else None),
        "games_entirely_clean": sum(1 for r in good if r["errors"] == 0),
        "warnings": warnings,
        "error_checks": error_checks,
        "warning_checks": warning_checks,
        "seconds": round(time.time() - started, 1),
        "examples": [m for r in good for m in r["error_messages"]][:20],
        "rows": good,
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print("%d beats over %d games" % (beats, len(good)))
    print("false positives (error severity): %d  = %.2f per 100 beats"
          % (errors, summary["false_positive_rate_per_100_beats"] or 0))
    print("games with no false positive: %d/%d"
          % (summary["games_entirely_clean"], len(good)))
    print("warnings (expected on template prose): %d %s"
          % (warnings, warning_checks))
    if error_checks:
        print("which checks fired: %s" % error_checks)
        for message in summary["examples"][:5]:
            print("  " + message)
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main()
