"""
VoxPlay CLI — entry point for all subcommands.

Usage
-----
  voxplay score                      # interactive terminal scorer
  voxplay ingest <file>              # load CSV or JSON scorecard
  voxplay commentate <events.json>   # generate commentary transcript
  voxplay synthesize <transcript.json> [--tts gtts|mock] [--out dir]
  voxplay demo                       # end-to-end demo on bundled sample match
  voxplay chess <file.pgn|lichess-url|game-id>   # chess commentary pipeline
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()  # load .env if present, before any os.environ reads

from voxplay.events.models import Event
from pydantic import TypeAdapter

_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_events(path: Path) -> List[Event]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw if isinstance(raw, list) else raw.get("events", raw)
    return [_event_adapter.validate_python(r) for r in records]


def _save_events(events: List[Event], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [json.loads(e.model_dump_json()) for e in events]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved {len(data)} events -> {path}")


def _save_transcript(lines, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{"event_index": l.event_index, "event_type": l.event_type, "text": l.text} for l in lines]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Transcript ({len(data)} lines) -> {path}")


# ---------------------------------------------------------------------------
# Subcommand: score
# ---------------------------------------------------------------------------

def cmd_score(args: argparse.Namespace) -> None:
    from voxplay.scorer import InteractiveScorer

    scorer = InteractiveScorer(match_id=getattr(args, "match_id", None))
    events = scorer.run()
    if not events:
        print("No events recorded.")
        return

    out_dir = Path(getattr(args, "out", None) or "output")
    match_id = scorer.match_id
    events_path = out_dir / f"{match_id}_events.json"
    _save_events(events, events_path)


# ---------------------------------------------------------------------------
# Subcommand: ingest
# ---------------------------------------------------------------------------

def cmd_ingest(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    suffix = path.suffix.lower()
    if suffix == ".csv":
        from voxplay.ingest.csv_parser import CSVEventSource
        source = CSVEventSource(path)
    elif suffix in (".json", ".jsonl"):
        from voxplay.ingest.json_parser import JSONEventSource
        source = JSONEventSource(path)
    else:
        print(f"Unsupported file format: {suffix}. Use .csv or .json", file=sys.stderr)
        sys.exit(1)

    events = list(source.produce())
    print(f"  Loaded {len(events)} events from {path}")

    out_dir = Path(getattr(args, "out", None) or "output")
    stem = path.stem
    events_path = out_dir / f"{stem}_events.json"
    _save_events(events, events_path)


# ---------------------------------------------------------------------------
# Subcommand: commentate
# ---------------------------------------------------------------------------

def cmd_commentate(args: argparse.Namespace) -> None:
    from voxplay.commentary.engine import CommentaryEngine

    events_path = Path(args.events)
    events = _load_events(events_path)

    mock = getattr(args, "mock", False) or not __import__("os").environ.get("ANTHROPIC_API_KEY")
    engine = CommentaryEngine(mock=mock)

    print(f"\n  Generating commentary for {len(events)} events "
          f"({'mock' if engine.mock else 'live API'})...\n")

    lines = list(engine.commentate(events))

    for line in lines:
        print(f"  [{line.event_type:>14}]  {line.text}")

    out_dir = Path(getattr(args, "out", None) or "output")
    stem = events_path.stem.replace("_events", "")
    transcript_path = out_dir / f"{stem}_transcript.json"
    _save_transcript(lines, transcript_path)


# ---------------------------------------------------------------------------
# Subcommand: synthesize
# ---------------------------------------------------------------------------

def cmd_synthesize(args: argparse.Namespace) -> None:
    from voxplay.tts import get_provider, assemble

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"Transcript not found: {transcript_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    tts_name = getattr(args, "tts", "gtts") or "gtts"
    provider = get_provider(tts_name)

    out_dir = Path(getattr(args, "out", None) or "output") / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)

    clip_paths = []
    stem = transcript_path.stem.replace("_transcript", "")

    print(f"\n  Synthesising {len(data)} clips with {tts_name}...\n")
    for item in data:
        idx = item["event_index"]
        text = item["text"]
        clip_file = out_dir / f"{stem}_{idx:04d}"
        try:
            path = provider.synthesize(text, clip_file)
            clip_paths.append(path)
            print(f"  [{idx:04d}] {path.name}")
        except Exception as exc:
            print(f"  [{idx:04d}] FAILED: {exc}", file=sys.stderr)

    if not clip_paths:
        print("No clips generated.", file=sys.stderr)
        sys.exit(1)

    out_track = Path(getattr(args, "out", None) or "output") / f"{stem}_commentary.wav"
    final = assemble(clip_paths, out_track)
    print(f"\n  Assembled commentary track -> {final}")


# ---------------------------------------------------------------------------
# Subcommand: demo
# ---------------------------------------------------------------------------

def cmd_demo(args: argparse.Namespace) -> None:
    """Full end-to-end pipeline on the bundled sample match."""
    import os
    from voxplay.commentary.engine import CommentaryEngine
    from voxplay.ingest.json_parser import JSONEventSource
    from voxplay.tts import get_provider, assemble

    # Locate bundled sample data
    data_dir = Path(__file__).parent.parent.parent / "data"
    sample = data_dir / "sample_match.json"
    if not sample.exists():
        print(f"Sample data not found at {sample}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path("output") / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: load events
    print("\n[demo] Loading sample match events...")
    source = JSONEventSource(sample)
    events = list(source.produce())
    print(f"[demo] {len(events)} events loaded.")

    events_path = out_dir / "demo_events.json"
    _save_events(events, events_path)

    # Step 2: commentary — use mock unless real key available
    mock = not os.environ.get("ANTHROPIC_API_KEY")
    engine = CommentaryEngine(mock=mock)
    mode = "mock" if engine.mock else "live API"
    print(f"\n[demo] Generating commentary ({mode})...")
    lines = list(engine.commentate(events))

    print()
    for line in lines:
        print(f"  [{line.event_type:>14}]  {line.text}")

    transcript_path = out_dir / "demo_transcript.json"
    _save_transcript(lines, transcript_path)

    # Step 3: TTS — always use mock in demo so it works offline
    tts_name = "mock" if not os.environ.get("ANTHROPIC_API_KEY") else getattr(args, "tts", "mock") or "mock"
    # Actually always use mock for TTS in demo to avoid needing internet
    tts_name = "mock"
    provider = get_provider(tts_name)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[demo] Synthesising audio clips ({tts_name} TTS)...")
    clip_paths = []
    for item in json.loads(transcript_path.read_text()):
        idx = item["event_index"]
        clip_file = clips_dir / f"demo_{idx:04d}"
        try:
            path = provider.synthesize(item["text"], clip_file)
            clip_paths.append(path)
        except Exception as exc:
            print(f"  clip {idx}: {exc}", file=sys.stderr)

    print(f"[demo] {len(clip_paths)} clips generated.")

    # Step 4: assemble
    out_track = out_dir / "demo_commentary.wav"
    try:
        final = assemble(clip_paths, out_track)
        print(f"\n[demo] Commentary track -> {final}")
    except Exception as exc:
        print(f"\n[demo] Assembly failed: {exc}", file=sys.stderr)

    print("\n[demo] Done! Outputs are in ./output/demo/")
    print("       demo_transcript.json - full commentary text")
    print("       demo_commentary.wav  - assembled audio (mock silence)")


# ---------------------------------------------------------------------------
# Argument parsing + entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Subcommand: eval
# ---------------------------------------------------------------------------

def cmd_chess(args: argparse.Namespace) -> None:
    """Full chess pipeline: PGN or Lichess link -> commentary script."""
    from voxplay.chessgame import beats as B
    from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine
    from voxplay.chessgame.evaluation import COMMENTARY_THRESHOLDS, ERROR_QUALITIES
    from voxplay.chessgame.models import Color
    from voxplay.chessgame.pgn_source import load_lichess, load_pgn
    from voxplay.chessgame.script import ScriptError, ScriptStyle, generate_script
    from voxplay.chessgame.speech import ascii_fold

    source = args.source
    print("=" * 70)
    if source.lower().endswith(".pgn") or Path(source).exists():
        print(f"  Loading PGN: {source}")
        game = load_pgn(source)
    else:
        print(f"  Fetching from Lichess: {source}")
        game = load_lichess(source)

    meta = game.meta
    print(f"  {ascii_fold(meta.white or '?')} vs {ascii_fold(meta.black or '?')}"
          f"   {meta.result.value}   {meta.eco or ''} {ascii_fold(meta.opening or '')}")
    print(f"  {game.total_plies} half-moves")
    print("=" * 70)

    config = AnalysisConfig(nodes=args.nodes, multipv=3, threads=args.threads)
    if not config.reproducible:
        print("  NOTE: threads > 1 -- results will not reproduce exactly.")
    print(f"\n[1/4] Analysing {game.total_plies + 1} positions "
          f"({args.nodes:,} nodes each) ...")
    started = time.time()
    with get_engine(args.engine, config) as engine:
        game = analyse_game(game, engine, thresholds=COMMENTARY_THRESHOLDS)
    print(f"      done in {time.time() - started:.1f}s using {engine.name}")

    for colour, name in ((Color.WHITE, meta.white), (Color.BLACK, meta.black)):
        played = [m for m in game.moves if m.color == colour and m.analysis]
        if not played:
            continue
        errors = [m for m in played if m.analysis.quality in ERROR_QUALITIES]
        average = sum(m.analysis.accuracy for m in played) / len(played)
        print(f"      {ascii_fold(name or colour.value)[:26]:<26} "
              f"accuracy {average:5.1f}%   errors {len(errors)}"
              + (f"   ({', '.join(m.san for m in errors[:5])})" if errors else ""))

    motifs = [(m, h) for m in game.moves for h in (m.analysis.motifs if m.analysis else [])]
    print(f"\n[2/4] Tactical motifs: {len(motifs)} detected")
    for move, hit in motifs[: args.max_motifs]:
        print(f"      move {move.move_number:>3} {move.san:<8} "
              f"{hit.motif.value:<16} {hit.description}")
    if len(motifs) > args.max_motifs:
        print(f"      ... and {len(motifs) - args.max_motifs} more")

    arc = B.extract_arc(game)
    by_ply = {m.ply: m for m in game.moves}
    turning = by_ply[arc.turning_point_ply].san if arc.turning_point_ply in by_ply else "-"
    budget = args.budget or B.budget_for(game)
    sheet = B.select_beats(game, pacing=B.PacingConfig(budget_seconds=budget))
    print(f"\n[3/4] Narrative arc: {arc.archetype.value}   "
          f"turning point: {turning}   lead changes: {arc.lead_changes}")
    print(f"      {len(sheet.beats)} beats, {sheet.total_seconds:.0f}s "
          f"(budget {budget:.0f}s)")
    if args.baselines:
        flat = B.select_every_move(game)
        topn = B.select_top_n(game, 8)
        print(f"      baselines: every-move {flat.total_seconds:.0f}s "
              f"({len(flat.beats)} beats), top-8 {topn.total_seconds:.0f}s")

    print(f"\n[4/4] Commentary script")
    style = ScriptStyle(persona=args.persona, audience_elo=args.audience)
    try:
        script = generate_script(game, sheet, generator=args.generator, style=style)
    except ScriptError as exc:
        # "auto" means "whatever actually works": a key that is present but
        # rejected (no credit, revoked, wrong org) must not kill the run. An
        # explicit --generator claude is a request, so that one fails loudly.
        if args.generator != "auto":
            print(f"      ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"      Claude unavailable -- {exc}")
        print("      Falling back to the template generator.")
        script = generate_script(game, sheet, generator="template", style=style)
    print(f"      {script.generator}: {script.total_words} words, "
          f"{script.total_seconds:.0f}s spoken\n")
    print("-" * 70)
    for line in script.lines:
        print(f"[{line.beat_type.value}]")
        print(f"  {line.text}")
        print()
    print("-" * 70)

    from voxplay.chessgame.verify import verify_script

    checked = verify_script(script, game, sheet, spoiler_free=style.spoiler_free)
    verdict = "clean" if checked.clean else f"{len(checked.errors)} ERRORS"
    print(f"\n  verification: {verdict}"
          f"   ({checked.errors_per_hundred_beats:.1f} errors per 100 beats,"
          f" {len(checked.findings) - len(checked.errors)} warnings)")
    for finding in checked.findings:
        print(f"    [{finding.severity}] {finding.check} @ beat "
              f"{finding.beat_index} ({finding.beat_type.value}): {finding.message}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(script.model_dump_json(indent=2), encoding="utf-8")
        print(f"  Script saved -> {out}")

    if args.audio or args.player:
        from voxplay.chessgame.audio import AudioError, narrate, write_timeline

        print(f"\n[5/5] Narrating with the '{args.tts}' voice ...")
        started = time.time()
        try:
            audio = narrate(script, args.audio_dir, provider=args.tts)
        except AudioError as exc:
            print(f"      ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        timeline = write_timeline(audio)
        print(f"      synthesised {len(audio.cues)} clips in {time.time() - started:.1f}s")
        print(f"      track    -> {audio.track_path}  ({audio.total_ms / 1000:.0f}s)")
        print(f"      timeline -> {timeline}")
        print(f"      word-count estimate {audio.estimated_ms / 1000:.0f}s, "
              f"actual {audio.total_ms / 1000:.0f}s (drift {audio.drift_ratio:.2f}x)")
        for cue in audio.cues:
            span = f"{cue.plies[0]}-{cue.plies[-1]}" if cue.plies else "-"
            print(f"        {cue.start_ms / 1000:6.1f}s  {cue.duration_ms / 1000:5.1f}s  "
                  f"{cue.beat_type.value:<14} plies {span}")

        if args.player:
            from voxplay.chessgame.player import PlayerError, build_player

            page = Path(args.audio_dir) / f"{game.meta.game_id}_player.html"
            try:
                page = build_player(game, sheet, audio, page)
            except PlayerError as exc:
                print(f"      ERROR: {exc}", file=sys.stderr)
                sys.exit(1)
            size_mb = page.stat().st_size / (1024 * 1024)
            print(f"      player   -> {page}  ({size_mb:.1f} MB, self-contained)")
            print(f"      open it in a browser: board, audio and captions in one file")


def cmd_validate_motifs(args: argparse.Namespace) -> None:
    """Score the motif detectors against human-tagged Lichess puzzles."""
    from voxplay.chessgame.validate import (
        ValidationError, run_validation, run_validation_from_database,
    )

    print("=" * 70)
    print("  Motif detector validation against the Lichess puzzle database (CC0)")
    print(f"  {args.count} puzzles per theme, {args.controls} control puzzles")
    if args.database:
        print(f"  source: {args.database}")
    else:
        print("  source: live API (slow and rate limited; prefer --database)")
    print("=" * 70)
    print()

    started = time.time()
    try:
        if args.database:
            report = run_validation_from_database(
                args.database,
                per_theme=args.count,
                controls=args.controls,
                max_rows=args.max_rows,
                progress=True,
            )
        else:
            report = run_validation(
                count=args.count,
                controls=args.controls,
                cache_dir=args.cache_dir,
                delay=args.delay,
                progress=True,
            )
    except ValidationError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print()
    print(report.table())
    print()
    print(f"  {report.puzzles_used} puzzles scored in {time.time() - started:.0f}s")
    print("  recall  = of puzzles a human tagged with this theme, how many we find")
    print("  control = puzzles NOT about this theme; how often we fire anyway")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"  report -> {out}")


def cmd_agreement(args: argparse.Namespace) -> None:
    """Measure classifier agreement against Lichess's own annotations."""
    from voxplay.chessgame.agreement import (
        AgreementError, annotated_moves, featured_users,
        fetch_annotated_games, read_games, rule_agreement,
    )

    print("=" * 70)
    print("  Move classifier agreement with Lichess annotations")
    print("=" * 70)
    try:
        if args.pgn:
            text = Path(args.pgn).read_text(encoding="utf-8")
            print(f"  source: {args.pgn}")
        else:
            users = args.users.split(",") if args.users else featured_users(args.players)
            print(f"  players: {', '.join(users)}")
            text = fetch_annotated_games(users, per_user=args.per_player,
                                         cache=args.cache, progress=True)
    except AgreementError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    moves, games = [], 0
    for game in read_games(text):
        games += 1
        moves.extend(annotated_moves(game))
    print(f"  {games} analysed games, {len(moves):,} annotated moves\n")

    if not moves:
        print("  ERROR: no annotated moves found.", file=sys.stderr)
        sys.exit(1)

    report = rule_agreement(moves)
    print(report.table())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n  report -> {out}")


def cmd_eval(args: argparse.Namespace) -> None:
    """Run commentary quality evaluation over the full test suite."""
    from voxplay.eval.runner import run_match_eval

    mock = not getattr(args, "live", False)
    out_dir = Path(getattr(args, "out", "output/eval"))

    # Build the list of match files to evaluate
    data_dir = Path(__file__).parent.parent.parent / "data"
    if getattr(args, "match", None):
        match_files = [Path(args.match)]
    else:
        match_files = [
            data_dir / "sample_match.json",
            data_dir / "test_matches" / "eval_collapse.json",
            data_dir / "test_matches" / "eval_chase.json",
            data_dir / "test_matches" / "eval_last_over.json",
        ]

    mode_str = "mock (offline)" if mock else "live Anthropic API"
    print(f"\n[eval] Running commentary quality evaluation -- {mode_str}")
    print(f"[eval] {len(match_files)} match(es) | output -> {out_dir}\n")

    results = []
    for path in match_files:
        if not path.exists():
            print(f"  [SKIP] {path} -- file not found")
            continue
        print(f"  Evaluating: {path.name} ...", end="", flush=True)
        result = run_match_eval(path, out_dir, mock=mock)
        results.append(result)
        total_flags = len(result.all_flags)
        print(f" {result.event_count} events, {result.ball_event_count} balls, {total_flags} flags")
        print(f"    Transcript -> {result.transcript_path}")

    if not results:
        print("[eval] No matches evaluated.")
        return

    # Per-match summary table
    _CHECKS = [
        ("repeated_opening", "Repeated openings"),
        ("phrase_repetition", "Phrase repeats"),
        ("length_violation",  "Length violations"),
        ("grounding_number",  "Ungrounded numbers"),
        ("grounding_shot",    "Ungrounded shots"),
        ("name_grounding",    "Halluc. names"),
    ]

    print()
    print("=" * 72)
    print("PER-MATCH SUMMARY  (flags as % of ball events)")
    print("-" * 72)
    hdr = f"{'Match':<28}" + "".join(f"{label[:10]:>12}" for _, label in _CHECKS)
    print(hdr)
    print("-" * 72)

    total_balls = 0
    total_flag_counts: dict[str, int] = {}

    for r in results:
        name = r.match_id[:26]
        counts = r.flag_counts()
        row = f"  {name:<26}"
        for check, _ in _CHECKS:
            n = counts.get(check, 0)
            pct = (n / r.ball_event_count * 100) if r.ball_event_count else 0.0
            row += f"  {n:>3} ({pct:4.0f}%)"
        print(row)
        total_balls += r.ball_event_count
        for check, _ in _CHECKS:
            total_flag_counts[check] = total_flag_counts.get(check, 0) + r.flag_counts().get(check, 0)

    print("-" * 72)
    # Overall row
    row = f"  {'OVERALL':<26}"
    for check, _ in _CHECKS:
        n = total_flag_counts.get(check, 0)
        pct = (n / total_balls * 100) if total_balls else 0.0
        row += f"  {n:>3} ({pct:4.0f}%)"
    print(row)
    print("=" * 72)
    print()
    print("  Flags are heuristic warnings for human review, not pass/fail scores.")
    print(f"  Read the full transcripts in {out_dir}/ to assess quality.")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voxplay",
        description="AI cricket commentary engine.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # score
    p_score = sub.add_parser("score", help="Launch the interactive terminal scorer.")
    p_score.add_argument("--match-id", dest="match_id", default=None)
    p_score.add_argument("--out", default="output", help="Output directory.")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Load a CSV/JSON scorecard into an event stream.")
    p_ingest.add_argument("file", help="Path to .csv or .json scorecard.")
    p_ingest.add_argument("--out", default="output")

    # commentate
    p_comm = sub.add_parser("commentate", help="Generate commentary over an event stream.")
    p_comm.add_argument("events", help="Path to events JSON file.")
    p_comm.add_argument("--mock", action="store_true", help="Use mock LLM responses.")
    p_comm.add_argument("--out", default="output")

    # synthesize
    p_synth = sub.add_parser("synthesize", help="Produce assembled audio from a transcript.")
    p_synth.add_argument("transcript", help="Path to transcript JSON file.")
    p_synth.add_argument("--tts", default="gtts", choices=["gtts", "elevenlabs", "mock"])
    p_synth.add_argument("--out", default="output")

    # demo
    p_demo = sub.add_parser("demo", help="Run the full pipeline on the bundled sample match.")
    p_demo.add_argument("--tts", default="mock", choices=["gtts", "elevenlabs", "mock"])

    # chess
    p_chess = sub.add_parser(
        "chess",
        help="Chess commentary: PGN file, Lichess URL, or Lichess game id.",
    )
    p_chess.add_argument(
        "source",
        help="Path to a .pgn file, a Lichess game URL, or a bare Lichess game id.",
    )
    p_chess.add_argument("--engine", default="auto", choices=["auto", "stockfish", "mock"],
                         help="auto falls back to the mock when Stockfish is absent.")
    p_chess.add_argument("--nodes", type=int, default=1_000_000,
                         help="Engine node budget per position (default 1000000).")
    p_chess.add_argument("--threads", type=int, default=1,
                         help="Engine threads. Keep at 1 -- above 1 results stop reproducing.")
    p_chess.add_argument("--generator", default="auto", choices=["auto", "template", "claude"],
                         help="auto uses Claude when an API key is set, else the template.")
    p_chess.add_argument("--budget", type=float, default=None,
                         help="Target spoken seconds. Defaults to scaling with game length.")
    p_chess.add_argument("--persona", default="analytical", choices=["analytical", "energetic"])
    p_chess.add_argument("--audience", type=int, default=1200,
                         help="Listener rating; below 1600 chess terms get explained.")
    p_chess.add_argument("--baselines", action="store_true",
                         help="Also print the every-move and top-N pacing baselines.")
    p_chess.add_argument("--max-motifs", type=int, default=20,
                         help="Cap how many motifs are listed.")
    p_chess.add_argument("--out", default=None, help="Write the script to a JSON file.")
    p_chess.add_argument("--audio", action="store_true",
                         help="Synthesise spoken audio and a timeline for the script.")
    p_chess.add_argument("--tts", default="offline", choices=["offline", "mock", "gtts"],
                         help="Voice backend. 'offline' needs no network or ffmpeg.")
    p_chess.add_argument("--player", action="store_true",
                         help="Also build a standalone HTML player (implies --audio).")
    p_chess.add_argument("--audio-dir", default="output/chess",
                         help="Where to write clips, the track, and the timeline.")

    # validate-motifs
    p_val = sub.add_parser(
        "validate-motifs",
        help="Score motif detectors against human-tagged Lichess puzzles.",
    )
    p_val.add_argument("--count", type=int, default=40,
                       help="Puzzles to sample per theme (default 40).")
    p_val.add_argument("--controls", type=int, default=60,
                       help="Control puzzles drawn from unrelated themes.")
    p_val.add_argument("--delay", type=float, default=2.0,
                       help="Seconds between API calls. 0.7 earns HTTP 429s; be kind.")
    p_val.add_argument("--cache-dir", default="data/puzzles",
                       help="Where fetched puzzles are cached between runs.")
    p_val.add_argument("--database", default=None,
                       help="Path to lichess_db_puzzle.csv.zst. Strongly preferred "
                            "over the API: one pass, no rate limits.")
    p_val.add_argument("--max-rows", type=int, default=None,
                       help="Stop scanning the database after this many puzzles.")
    p_val.add_argument("--out", default="output/motif_validation.json")

    # agreement
    p_agr = sub.add_parser(
        "agreement",
        help="Measure classifier agreement with Lichess's own annotations.",
    )
    p_agr.add_argument("--pgn", default=None,
                       help="Score an existing PGN of analysed games instead of fetching.")
    p_agr.add_argument("--users", default=None,
                       help="Comma-separated Lichess usernames to pull games from.")
    p_agr.add_argument("--players", type=int, default=8,
                       help="How many currently-featured players to sample from.")
    p_agr.add_argument("--per-player", type=int, default=40,
                       help="Analysed games to fetch per player.")
    p_agr.add_argument("--cache", default="data/games/annotated.pgn")
    p_agr.add_argument("--out", default="output/agreement.json")

    # eval
    p_eval = sub.add_parser(
        "eval",
        help="Run commentary quality evaluation over all test matches.",
    )
    p_eval.add_argument(
        "--live", action="store_true",
        help="Use real Anthropic API instead of mock (requires ANTHROPIC_API_KEY).",
    )
    p_eval.add_argument("--out", default="output/eval", help="Output directory for transcripts.")
    p_eval.add_argument(
        "--match", default=None,
        help="Evaluate a single JSON file instead of the full suite.",
    )

    return parser


def app() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "score": cmd_score,
        "ingest": cmd_ingest,
        "commentate": cmd_commentate,
        "synthesize": cmd_synthesize,
        "demo": cmd_demo,
        "chess": cmd_chess,
        "validate-motifs": cmd_validate_motifs,
        "agreement": cmd_agreement,
        "eval": cmd_eval,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    app()
