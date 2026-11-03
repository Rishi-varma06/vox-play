"""
Why the paced selector ever misses the decisive move.

Coverage of the turning point is 98.8% over the 170 games that have one, and
top-N covers it in all of them. Two games account for the difference. This
reconstructs the selection on those games and prints, for the turning point
and for everything that beat it, the salience and the reason the selector
gave or withheld a beat -- so the explanation in the paper is a diagnosis
rather than a guess.

Run:  python docs/paper/studies/why_missed.py K6LrPms4 yQkP7qsi
"""
from __future__ import annotations

import sys
from pathlib import Path

CACHE = Path("docs/paper/studies/analysed")
NODES = 1_000_000


def load(game_id: str):
    """The analysed game, from cache if it is there and the engine if not."""
    sys.path.insert(0, str(Path(__file__).parent))
    from pacing_study import collect
    from voxplay.chessgame.pgn_source import ParsedGame, parse_pgn_text

    cached = CACHE / ("%s.json" % game_id)
    if cached.exists():
        return ParsedGame.model_validate_json(cached.read_text(encoding="utf-8"))

    from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine
    for chunk in collect(200):
        game = parse_pgn_text(chunk)
        if game.meta.game_id == game_id:
            analyse_game(game, get_engine("stockfish", AnalysisConfig(nodes=NODES)))
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(game.model_dump_json(), encoding="utf-8")
            return game
    raise SystemExit("game %s is not in the corpus" % game_id)


def explain(game_id: str) -> None:
    from voxplay.chessgame.beats import (
        BEAT_WORDS, BeatType, DEFAULT_WEIGHTS, PacingConfig, WORDS_PER_SECOND,
        budget_for, extract_arc, salience, select_beats, _mandatory_beats,
        _think_baselines,
    )

    game = load(game_id)
    arc = extract_arc(game)
    pacing = PacingConfig(budget_seconds=budget_for(game))
    think = _think_baselines(game.moves)
    mandatory = _mandatory_beats(game, arc)
    reserved = sum(BEAT_WORDS[b.beat_type] for b in mandatory)
    budget_words = int(round(pacing.budget_seconds * WORDS_PER_SECOND)) - reserved
    spoken = {p for b in mandatory
              if b.beat_type in (BeatType.OPENING, BeatType.FINISH)
              for p in b.plies}

    scored = sorted(
        ((salience(m, arc, think, DEFAULT_WEIGHTS), m) for m in game.moves
         if m.analysis is not None),
        key=lambda pair: (-pair[0], pair[1].ply))

    sheet = select_beats(game, DEFAULT_WEIGHTS, pacing)
    chosen = [b.plies[0] for b in sheet.beats
              if b.beat_type in (BeatType.MOMENT, BeatType.TURNING_POINT)]

    tp = arc.turning_point_ply
    rank = next((i for i, (_, m) in enumerate(scored, 1) if m.ply == tp), None)
    tp_score = next((s for s, m in scored if m.ply == tp), None)

    print("=" * 66)
    print("%s  %d plies, budget %.0f s (%d words after the mandatory beats)"
          % (game_id, game.total_plies, pacing.budget_seconds, budget_words))
    print("turning point: ply %s, salience %.3f, rank %s of %d candidates"
          % (tp, tp_score, rank, len(scored)))
    print("selector chose plies:", chosen)

    if tp in spoken:
        print("REASON: the turning point falls inside the opening or finish "
              "beat, which already speaks those plies.")
        return

    blockers = [p for p in chosen if abs(p - tp) < pacing.min_ply_gap]
    if blockers:
        print("REASON: separation. Plies %s were accepted first and sit within "
              "%d half-moves of ply %d, so (5) excludes it."
              % (blockers, pacing.min_ply_gap, tp))
        for p in blockers:
            s = next((s for s, m in scored if m.ply == p), None)
            print("        ply %d scored %.3f against the turning point's %.3f"
                  % (p, s, tp_score))
        return

    if tp_score is not None and tp_score < pacing.min_salience:
        print("REASON: the turning point scored below the floor of %.2f."
              % pacing.min_salience)
        return

    print("REASON: budget. Nothing within %d half-moves was taken, so the "
          "turning point was reached with too few words left for a "
          "%d-word beat." % (pacing.min_ply_gap, BEAT_WORDS[BeatType.TURNING_POINT]))


def main() -> None:
    for game_id in (sys.argv[1:] or ["K6LrPms4", "yQkP7qsi"]):
        explain(game_id)


if __name__ == "__main__":
    main()
