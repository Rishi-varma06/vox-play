"""
M3 / M4: pacing measured over a corpus rather than two hand-picked games.

Two games cannot show a distribution, and the main claim of this project is
about selection. This analyses N games from the Lichess corpus and records,
per game and per arm: spoken duration, beat count, how many selected
half-moves sit adjacent to another selection, and whether the opening, the
decisive moment and the finish are covered at all.

It also computes the EXACT optimum of the selection problem by dynamic
programming, so the greedy selector's optimality gap is a measured number
rather than an assurance. See `exact_selection` for the formulation.

Determinism: every position is still evaluated by a single-threaded engine at
a fixed node budget. Games run in separate processes, each with its own
engine, which parallelises the corpus without making any individual search
multi-threaded -- the property Section VIII-A depends on.

Run:  python docs/paper/studies/pacing_study.py --games 120 --workers 10
      python docs/paper/studies/pacing_study.py --report
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PGN = Path("data/games/annotated.pgn")
OUT = Path("docs/paper/studies/pacing_study.jsonl")

#: Games shorter than this are not really games; longer than this and the
#: engine cost per game stops being worth the marginal data point.
MIN_PLIES, MAX_PLIES = 20, 200


# ---------------------------------------------------------------------------
# Exact selection, for the optimality gap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    ply: int
    value: float
    words: int


def exact_selection(
    candidates: Sequence[Candidate], word_budget: int, gap: int
) -> float:
    """
    The optimal total salience under the same constraints the greedy obeys.

    maximise   sum of value over the chosen set S
    subject to sum of words over S <= word_budget
               |ply_i - ply_j| >= gap for every distinct pair in S

    With candidates in ply order the pairwise separation constraint is implied
    by the constraint between CONSECUTIVE chosen candidates, so the problem is
    a knapsack on a line and an exact O(n * word_budget) DP applies. Beat
    costs are integer word counts, which is what keeps the budget axis
    discrete and the solution exact rather than approximate.
    """
    items = sorted(candidates, key=lambda c: c.ply)
    n = len(items)
    if n == 0 or word_budget <= 0:
        return 0.0

    # next_ok[i] = first index j > i whose ply is far enough from item i.
    next_ok = [n] * n
    for i, item in enumerate(items):
        for j in range(i + 1, n):
            if items[j].ply - item.ply >= gap:
                next_ok[i] = j
                break

    memo: Dict[Tuple[int, int], float] = {}

    def best(i: int, budget: int) -> float:
        if i >= n:
            return 0.0
        key = (i, budget)
        hit = memo.get(key)
        if hit is not None:
            return hit
        out = best(i + 1, budget)
        if items[i].words <= budget:
            take = items[i].value + best(next_ok[i], budget - items[i].words)
            if take > out:
                out = take
        memo[key] = out
        return out

    sys.setrecursionlimit(10_000)
    return best(0, word_budget)


# ---------------------------------------------------------------------------
# Per-game measurement
# ---------------------------------------------------------------------------

def adjacent_picks(plies: Sequence[int], gap: int) -> int:
    """How many selected half-moves sit closer than `gap` to another pick."""
    ordered = sorted(plies)
    return sum(1 for a, b in zip(ordered, ordered[1:]) if b - a < gap)


def measure(game, nodes: int) -> dict:
    """Analyse one game and score all three selection arms."""
    from voxplay.chessgame.beats import (
        BEAT_WORDS, BeatType, DEFAULT_WEIGHTS, PacingConfig, WORDS_PER_SECOND,
        budget_for, extract_arc, salience, select_beats, select_every_move,
        select_top_n, _mandatory_beats, _think_baselines,
    )
    from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine

    engine = get_engine("stockfish", AnalysisConfig(nodes=nodes))
    started = time.time()
    analyse_game(game, engine)
    eval_seconds = time.time() - started

    budget = budget_for(game)
    pacing = PacingConfig(budget_seconds=budget)
    paced = select_beats(game, DEFAULT_WEIGHTS, pacing)
    flat = select_every_move(game)
    topn = select_top_n(game)
    arc = extract_arc(game)

    moment_types = (BeatType.MOMENT, BeatType.TURNING_POINT)
    picked = [b.plies[0] for b in paced.beats if b.beat_type in moment_types]
    topn_picked = [b.plies[0] for b in topn.beats]

    def covers(sheet, ply: Optional[int]) -> bool:
        if ply is None:
            return False
        return any(ply in beat.plies for beat in sheet.beats)

    opening_plies = set(range(1, 9))

    def covers_opening(sheet) -> bool:
        return any(opening_plies & set(beat.plies) for beat in sheet.beats)

    # --- optimality gap -----------------------------------------------------
    mandatory = _mandatory_beats(game, arc)
    reserved_words = sum(BEAT_WORDS[b.beat_type] for b in mandatory)
    budget_words = int(round(budget * WORDS_PER_SECOND)) - reserved_words
    spoken = set()
    for beat in mandatory:
        if beat.beat_type in (BeatType.OPENING, BeatType.FINISH):
            spoken.update(beat.plies)

    think = _think_baselines(game.moves)
    candidates: List[Candidate] = []
    for move in game.moves:
        if move.analysis is None or move.ply in spoken:
            continue
        score = salience(move, arc, think, DEFAULT_WEIGHTS)
        if score < pacing.min_salience:
            continue
        kind = (BeatType.TURNING_POINT if move.ply == arc.turning_point_ply
                else BeatType.MOMENT)
        candidates.append(Candidate(move.ply, score, BEAT_WORDS[kind]))

    greedy_value = sum(b.salience for b in paced.beats
                       if b.beat_type in moment_types)
    optimum = exact_selection(candidates, budget_words, pacing.min_ply_gap)

    return {
        "game_id": game.meta.game_id,
        "plies": game.total_plies,
        "archetype": arc.archetype.value,
        "turning_point_ply": arc.turning_point_ply,
        "nodes": nodes,
        "eval_seconds": round(eval_seconds, 1),
        "budget_seconds": round(budget, 1),
        "paced_seconds": paced.total_seconds,
        "flat_seconds": flat.total_seconds,
        "topn_seconds": topn.total_seconds,
        "paced_beats": len(paced.beats),
        "paced_moments": len(picked),
        "flat_beats": len(flat.beats),
        "topn_beats": len(topn.beats),
        "ratio_vs_flat": (round(flat.total_seconds / paced.total_seconds, 2)
                          if paced.total_seconds else None),
        "paced_adjacent": adjacent_picks(picked, pacing.min_ply_gap),
        "topn_adjacent": adjacent_picks(topn_picked, pacing.min_ply_gap),
        "paced_covers_opening": covers_opening(paced),
        "topn_covers_opening": covers_opening(topn),
        "paced_covers_turning_point": covers(paced, arc.turning_point_ply),
        "topn_covers_turning_point": covers(topn, arc.turning_point_ply),
        "paced_covers_finish": covers(paced, game.total_plies),
        "topn_covers_finish": covers(topn, game.total_plies),
        "candidates": len(candidates),
        "greedy_value": round(greedy_value, 4),
        "optimal_value": round(optimum, 4),
        "optimality": round(greedy_value / optimum, 4) if optimum > 0 else None,
    }


def _worker(payload: Tuple[str, int]) -> dict:
    """Re-parse in the child so a ParsedGame never crosses a process boundary."""
    pgn_text, nodes = payload
    try:
        from voxplay.chessgame.pgn_source import parse_pgn_text
        return measure(parse_pgn_text(pgn_text), nodes)
    except Exception as exc:  # noqa: BLE001 - one game failing is not fatal
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

def split_pgn(text: str) -> List[str]:
    """One PGN string per game, cut at each Event header that follows moves."""
    out: List[str] = []
    current: List[str] = []
    for line in text.splitlines(keepends=True):
        starts_game = line.startswith("[Event ")
        has_moves = any(
            row.strip() and not row.startswith("[") for row in current
        )
        if starts_game and current and has_moves:
            out.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        out.append("".join(current))
    return out


def collect(limit: int) -> List[str]:
    from voxplay.chessgame.pgn_source import parse_pgn_text

    text = PGN.read_text(encoding="utf-8", errors="replace")
    chosen: List[str] = []
    for chunk in split_pgn(text):
        try:
            game = parse_pgn_text(chunk)
        except Exception:  # noqa: BLE001 - unparseable games are skipped
            continue
        if MIN_PLIES <= game.total_plies <= MAX_PLIES:
            chosen.append(chunk)
        if len(chosen) >= limit:
            break
    return chosen


def run(games: int, nodes: int, workers: int) -> None:
    import multiprocessing

    chunks = collect(games)
    print("%d games selected (%d-%d plies), %s nodes, %d workers"
          % (len(chunks), MIN_PLIES, MAX_PLIES, format(nodes, ","), workers),
          flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payloads = [(chunk, nodes) for chunk in chunks]
    started = time.time()
    with OUT.open("a", encoding="utf-8") as handle:
        with multiprocessing.Pool(workers) as pool:
            for index, record in enumerate(
                pool.imap_unordered(_worker, payloads), start=1
            ):
                handle.write(json.dumps(record) + "\n")
                handle.flush()
                rate = (time.time() - started) / index
                print("[%d/%d] %10s %4sp paced %6.1fs flat %6.1fs opt %.3f "
                      "eta %5.1fm"
                      % (index, len(payloads), record.get("game_id", "ERROR"),
                         record.get("plies", "?"),
                         record.get("paced_seconds") or 0.0,
                         record.get("flat_seconds") or 0.0,
                         record.get("optimality") or 0.0,
                         (len(payloads) - index) * rate / 60),
                      flush=True)
                if "error" in record:
                    print("    " + record["error"], flush=True)
    print("DONE", flush=True)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def spread(values: Sequence[float]) -> str:
    if not values:
        return "no data"
    ordered = sorted(values)

    def at(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]

    return ("median %7.1f   IQR %7.1f-%7.1f   range %7.1f-%7.1f"
            % (statistics.median(ordered), at(0.25), at(0.75),
               ordered[0], ordered[-1]))


def report() -> None:
    records = [
        json.loads(line)
        for line in OUT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = [r for r in records if "error" not in r]
    print("%d games (%d failed), %s half-moves\n"
          % (len(rows), len(records) - len(rows),
             format(sum(r["plies"] for r in rows), ",")))

    print("plies            ", spread([r["plies"] for r in rows]))
    print("paced seconds    ", spread([r["paced_seconds"] for r in rows]))
    print("top-n seconds    ", spread([r["topn_seconds"] for r in rows]))
    print("every-move secs  ", spread([r["flat_seconds"] for r in rows]))
    print("ratio vs flat    ", spread([r["ratio_vs_flat"] for r in rows
                                       if r["ratio_vs_flat"]]))
    print("paced beats      ", spread([r["paced_beats"] for r in rows]))
    print("paced moments    ", spread([r["paced_moments"] for r in rows]))
    print()
    print("ADJACENT PICKS (selections closer than 4 plies to another)")
    for arm in ("paced", "topn"):
        counts = [r["%s_adjacent" % arm] for r in rows]
        clean = sum(1 for count in counts if count == 0)
        print("  %-6s mean %5.2f   total %4d   games with none %d/%d (%.0f%%)"
              % (arm, statistics.fmean(counts), sum(counts), clean, len(rows),
                 100 * clean / len(rows)))
    print()
    print("COVERAGE (share of games where the arm says anything about it)")
    for field in ("covers_opening", "covers_turning_point", "covers_finish"):
        paced = sum(1 for r in rows if r["paced_%s" % field])
        topn = sum(1 for r in rows if r["topn_%s" % field])
        print("  %-15s paced %5.1f%%   topn %5.1f%%"
              % (field.replace("covers_", ""),
                 100 * paced / len(rows), 100 * topn / len(rows)))
    print()
    print("GREEDY OPTIMALITY (greedy total salience / exact DP optimum)")
    ratios = [r["optimality"] for r in rows if r.get("optimality")]
    if ratios:
        exact = sum(1 for ratio in ratios if ratio >= 0.9999)
        print("  mean %.4f   min %.4f   optimal on %d/%d games (%.0f%%)"
              % (statistics.fmean(ratios), min(ratios), exact, len(ratios),
                 100 * exact / len(ratios)))
    print()
    print("ARCHETYPES")
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["archetype"]] = counts.get(row["archetype"], 0) + 1
    for name, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        print("  %-18s %4d" % (name, count))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=120)
    parser.add_argument("--nodes", type=int, default=1_000_000)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.report:
        report()
    else:
        run(args.games, args.nodes, args.workers)


if __name__ == "__main__":
    main()
