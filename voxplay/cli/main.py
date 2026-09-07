"""
VoxPlay CLI — entry point for all subcommands.

Usage
-----
  voxplay score                      # interactive terminal scorer
  voxplay ingest <file>              # load CSV or JSON scorecard
  voxplay commentate <events.json>   # generate commentary transcript
  voxplay synthesize <transcript.json> [--tts gtts|mock] [--out dir]
  voxplay demo                       # end-to-end demo on bundled sample match
"""

from __future__ import annotations

import argparse
import json
import sys
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
        "eval": cmd_eval,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    app()
