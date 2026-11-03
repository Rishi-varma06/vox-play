"""
Measure the gate's false-positive rate before and after the licence fix, on
one corpus.

The first measurement ran over 59 games and 450 beats; the post-fix run over
60 and 456, because one game failed in the earlier run. Two rates over two
different corpora are not a before-and-after, so this reverts the fix in a
copy of verify.py, measures, restores, and measures again — both arms over
whatever games complete in the second run.

The revert is done by text substitution on the real file and restored in a
finally block. If this script is interrupted, check `git diff` on
voxplay/chessgame/verify.py before trusting anything.

Run:  python docs/paper/studies/gate_before_after.py --games 60
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

VERIFY = Path("voxplay/chessgame/verify.py")
STUDY = Path("docs/paper/studies/gate_false_positives.py")
RESULT = Path("docs/paper/studies/gate_false_positives.json")
OUT = Path("docs/paper/studies/gate_before_after.json")

#: The block the fix added. Removing it restores the pre-fix licence.
FIX = """        # And license each whitespace-delimited token the same way, because
        # that is exactly what check_names does to the generated text: it
        # strips punctuation per token, so "Nimzo-Larsen Attack" reaches the
        # check as "NimzoLarsen" and "Attack". Licensing only the whole
        # joined string left "NimzoLarsen" unlicensed, and an opening name
        # the facts had supplied was reported as invented -- 12 of the 15
        # false positives in the gate measurement, all of them opening names.
        for token in folded.split():
            piece = re.sub(r"[^A-Za-z]", "", token)
            if len(piece) > 2:
                licence.names.add(_spelling(piece))
"""


def run_study(games: int, nodes: int, workers: int) -> dict:
    subprocess.run(
        [sys.executable, str(STUDY), "--games", str(games),
         "--nodes", str(nodes), "--workers", str(workers)],
        check=True, capture_output=True, text=True)
    return json.loads(RESULT.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=60)
    parser.add_argument("--nodes", type=int, default=200_000)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    original = VERIFY.read_text(encoding="utf-8")
    if FIX not in original:
        raise SystemExit("the licence fix is not in verify.py as expected")

    try:
        print("measuring WITHOUT the licence fix ...", flush=True)
        VERIFY.write_text(original.replace(FIX, ""), encoding="utf-8")
        before = run_study(args.games, args.nodes, args.workers)
    finally:
        VERIFY.write_text(original, encoding="utf-8")
        print("verify.py restored", flush=True)

    print("measuring WITH the licence fix ...", flush=True)
    after = run_study(args.games, args.nodes, args.workers)

    def line(tag: str, r: dict) -> str:
        return ("%-8s %d games, %d beats, %d false positives = %.2f per 100, "
                "%d games clean, checks %s"
                % (tag, r["games"], r["beats"], r["false_positives"],
                   r["false_positive_rate_per_100_beats"],
                   r["games_entirely_clean"], r["error_checks"]))

    print()
    print(line("before", before))
    print(line("after", after))
    matched = before["beats"] == after["beats"] and before["games"] == after["games"]
    print("\ncorpora match:", matched)

    OUT.write_text(json.dumps(
        {"matched_corpus": matched, "before": before, "after": after},
        indent=2), encoding="utf-8")
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
