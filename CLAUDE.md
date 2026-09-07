# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

- **Platform:** Windows 11, PowerShell terminal
- **Terminal encoding:** CP1252 — use only ASCII characters in all CLI output, print statements, and logs. Do not use Unicode arrows (`→`), em-dashes (`—`), box-drawing characters (`┌─┐`), or similar. Use `->`, `--`, `+-|` equivalents.
- **Python:** 3.11+
- **`claude` CLI is not on PATH** — for any Claude Code configuration changes, edit `.claude/settings.json` directly rather than using `claude mcp add` or similar commands.

## Commands

```bash
# Install (editable + dev deps including httpx for API tests)
pip install -e ".[dev]"

# Run all tests
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_eval.py

# Run a single test by name
python -m pytest tests/test_eval.py::test_name_grounding_india_win_all_caps_flagged

# Run with coverage
python -m pytest --cov=voxplay --cov-report=term-missing

# Start the web scorer (browser UI)
python -m uvicorn voxplay.web.app:app --reload --port 8000

# CLI pipeline commands
python -m voxplay.cli.main demo                          # full offline pipeline, bundled sample match
python -m voxplay.cli.main ingest data/sample_match.json
python -m voxplay.cli.main commentate output/events.json --mock
python -m voxplay.cli.main eval                          # mock, all test matches
python -m voxplay.cli.main eval --live                   # real Anthropic API
python -m voxplay.cli.main eval --match data/test_matches/eval_chase.json
```

If installed via `pip install -e .`, the `voxplay` entry point works directly (e.g. `voxplay demo`), but the `voxplay.exe` script may not be on PATH on Windows — use `python -m voxplay.cli.main` to be safe.

## API Keys

Read from environment / `.env` at startup via `python-dotenv`:
- `ANTHROPIC_API_KEY` — required for live commentary generation; omit to fall back to mock mode automatically
- `ELEVENLABS_API_KEY` — required only for ElevenLabs TTS; omit to use gTTS or mock

## Architecture

The **Event** is the single contract through the entire system. Every input source produces Events; every downstream component consumes only Events. Adding a new source or output requires no changes to other layers.

```
Input sources          Event stream           Commentary engine        Audio output
─────────────          ────────────           ─────────────────        ────────────
JSON/CSV ingest  ──►   BallEvent              CommentaryEngine  ──►   per-ball clips
Interactive scorer ──► InningsStartEvent      (Anthropic Claude         │
Web scorer ─────────►  OverStartEvent          or mock)                 ▼
[future: vision]       MilestoneEvent                            assembled .wav track
                       DrinksEvent
```

### Event schema (`voxplay/events/models.py`)

The discriminated union `Event = Annotated[Union[BallEvent, ...], Field(discriminator="event_type")]` is the shared type used everywhere. Parse with `TypeAdapter(Event).validate_python(data)`.

Key model hierarchy:
- `_BaseEvent` — `match_id`, `innings`, `timestamp`, `tags`, `video_offset` (AV sync, seconds from match start)
- `BallEvent(_BaseEvent)` — adds `outcome: Outcome`, `state: MatchState`, `delivery_type`, `note`
- `MatchState` — snapshot of score, wickets, overs, run rates, partnership, and individual batsman stats **after** the event
- `Outcome` — runs, extras, wicket, shot_tag, boundary/six flags

### State engine (`voxplay/events/state.py`)

`advance_state(prev: MatchState, outcome: Outcome, over_number, total_overs) -> MatchState` is the single function that drives all state transitions. It handles: runs, extras (wide/no-ball don't count as legal deliveries), wickets (resets partnership/batsman stats), over rollover, balls remaining, CRR/RRR. Always call this; never compute state manually.

`init_state(target, total_overs)` creates a fresh `MatchState` for the start of an innings.

### Commentary engine (`voxplay/commentary/engine.py` + `prompts.py`)

`CommentaryEngine(mock, window_size=5)` — wraps the Anthropic API (or mock). Key details:
- Rolling context window of 5 previous commentary lines is passed with each prompt to enforce variety
- `mock=True` (or no `ANTHROPIC_API_KEY`) → cycles through static responses in `_MOCK_RESPONSES`
- Model: `claude-sonnet-4-6`
- `event_to_prompt(ev, window)` dispatches to per-event prompt builders; each builder is in `prompts.py`
- Ball prompts include a `DO NOT reuse any phrase of 4+ consecutive words` instruction to reduce repetition

### Eval / quality checks (`voxplay/eval/checks.py`)

Six heuristic checks run against `(lines: List[CommentaryLine], events: List[Event])`:
- `repeated_opening` — same first 2 words within 4-ball window
- `phrase_repetition` — shared 4-gram within 5-ball window (ignores cricket filler phrases)
- `length_violation` — ball commentary > 2 sentences
- `grounding_number` — number in commentary not traceable to event data (known derived quantities like `target - score` are whitelisted)
- `grounding_shot` — shot word without `shot_tag` in event (context filter: "pushed to mid-on" is not a shot)
- `name_grounding` — real-world cricket nation (`_CRICKET_COUNTRIES`) mentioned but not a match team (case-insensitive)

All flags are heuristic warnings for human review, not pass/fail. `run_all_checks()` runs all six.

### Web scorer (`voxplay/web/`)

FastAPI backend + plain HTML/CSS/JS single-page app.

**Backend** (`app.py`):
- `MatchStore` persists match sessions as JSON to `data/matches.json` (configurable via `VOXPLAY_DATA_DIR` env var)
- `MatchSession` is the canonical match state — `events: List[dict]` stores all events as raw dicts, `match_state: dict` caches the current `MatchState`
- After undo, `_recompute(session)` replays all events from scratch to rebuild cached state
- Innings numbering: counted from `innings_start` events in `session.events`, not from a stored counter

**Key endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/matches` | Create match |
| POST | `/matches/{id}/innings` | Start innings (creates InningsStartEvent + OverStartEvent) |
| POST | `/matches/{id}/ball` | Record delivery (advance_state, rotation, over/innings completion) |
| DELETE | `/matches/{id}/ball` | Undo last ball (pops last BallEvent, recomputes) |
| POST | `/matches/{id}/over` | New over — requires `over_complete=True` |
| POST | `/matches/{id}/swap-strike` | Manual striker swap |
| PATCH | `/matches/{id}/ball/{idx}/offset` | Update `video_offset` on a specific event |
| POST | `/matches/{id}/end-innings` | Manually lock innings; stores `innings1_score` |
| POST | `/matches/{id}/commentary` | Generate commentary for all events |

**Frontend** (`voxplay/web/static/`):
- Single source of truth: every API response returns the full `MatchSession`; `renderScoringScreen(match)` re-renders everything from it
- Match clock auto-runs from innings start; `video_offset` is captured at button-tap time (not submit time) to minimize lag; per-ball nudge controls and a "Refine Timings" modal allow post-entry correction
- Shot/delivery inputs are tap-grid chips (not dropdowns)

### Ingest (`voxplay/ingest/`)

Both `CSVEventSource` and `JSONEventSource` implement `EventSource` (lazy iterator). To add a new source, subclass `EventSource` in a new file and wire it into `cli/main.py`. No other files need changing.

### TTS (`voxplay/tts/`)

`TTSProvider.synthesize(text, output_path) -> Path` is the interface. Concrete providers: `GttsTTSProvider`, `ElevenLabsTTSProvider` (stub), `MockTTSProvider` (writes silent WAV). The assembler (`assembler.py`) concatenates clips into a final `.wav`. Register new providers in `voxplay/tts/__init__.py::get_provider()`.

## Chess domain (`voxplay/chessgame/`)

The second domain. Cricket needs a human scoring ball by ball before any
commentary exists; chess gets a complete event stream from a free text file
plus ground-truth evaluation from an engine. Package is named `chessgame`, not
`chess`, so it does not shadow the python-chess library.

```
PGN / Lichess -> pgn_source -> MoveEvent stream -> engine.analyse_game -> MoveAnalysis
                 (no engine)                       (Stockfish or mock)
```

### Load-bearing decisions

- **Classify on win probability, not centipawns.** 100cp is ~12 points of win
  probability near equality and under 1 point at +900. Centipawn thresholds
  therefore mislabel both ends of the range. `evaluation.py` owns the mapping.
- **A move the engine ranks first is never an error.** When the top move
  appears to lose win probability, the search is revising its own earlier
  assessment one ply deeper. Without this rule the classifier calls Morphy's
  winning combination an inaccuracy.
- **Budget by nodes, not depth or time.** Time-limited analysis gives
  different answers on different machines, which makes reported numbers
  unreproducible.
- **Analyse positions, not moves.** The position after move i is the position
  before move i+1, so N moves need N+1 evaluations rather than 2N.
- **Always close an AnalysisEngine** (use `with`). A leaked Stockfish
  subprocess hangs pytest at exit.

### Motifs (`motifs.py`)

Deterministic board geometry, never the LLM. A model asked "was that a fork?"
answers confidently either way, and a listener who plays chess catches the one
time in ten it is wrong. Detectors hand the commentary layer a fact plus a
factual sentence; the model narrates it rather than deducing it.

Names match Lichess puzzle-database themes so the detectors can be scored for
precision and recall against that dataset (CC0, human-tagged) rather than
asserted to work.

`static_exchange_eval()` is the shared primitive -- python-chess has no SEE,
and hanging-piece, sacrifice and trapped-piece detection all need one.

Three filters exist because the naive version had terrible precision. On the
33-move Opera Game it emitted 36 motifs including 17 "pins"; it now emits 13,
all meaningful:

- **Pawns are excluded from pins and skewers.** A queen notionally pinning a
  pawn to another pawn is geometrically real and conversationally nothing.
- **A fork needs two targets worth a knight or more.** "Forks the king and a
  pawn" is a check that happens to hit a pawn.
- **Standing motifs are reported only when created.** A pin persists for as
  many moves as the pieces stay put; `STANDING_MOTIFS` patterns are diffed
  against the position before the move so only the news survives. Without
  this the same pin is announced five moves running.

Soundness of a sacrifice is NOT decided here -- geometry cannot know whether
one works. This layer reports the material fact; the analysis layer combines
it with the evaluation.

### Beat selection (`beats.py`) -- the research core

The layer the project rests on. Engine analysis is solved, LLM prose is easy,
speech is a paid API; deciding which of a hundred half-moves deserve airtime
is the open question, and it is what separates commentary from a machine
reading a move list.

Posed as constrained selection: maximise total salience subject to a spoken
duration budget, mandatory coverage (beginning, decisive moment, end), and
spacing rules that stop three detailed beats landing back to back.

**Two baselines live beside the real selector on purpose.** An A/B test needs
all three to exist and emit the same shape of output:

| function | strategy | on the Opera Game |
|---|---|---|
| `select_every_move()` | flat, one beat per half-move | 594s (9.9 min), 33 beats |
| `select_top_n()` | top N by swing, no constraints | 144s, clusters on adjacent plies, misses the mate |
| `select_beats()` | salience + budget + coverage + spacing | 105s, 6 beats |

If the constrained selector does not beat the baselines with listeners, that
is a real result, and this module is where it gets measured.

`SalienceWeights` and `PacingConfig` are the experiment's knobs -- vary them
and report the effect. `Archetype` conditions the narrative shape; without it
a collapse and a grind get the same script.

Duration is modelled as `words / WORDS_PER_SECOND` (150 wpm). The budget is
spent in airtime, so beat sizes are chunky relative to a short game -- that is
intentional, not a bug.

### Thresholds

`DEFAULT_THRESHOLDS` matches Lichess so classification agreement stays
comparable. `COMMENTARY_THRESHOLDS` adds `decided_win_pct=90`, which refuses
to call a move played from a won position a blunder. Use the default on the
evaluation path and the commentary one on the commentary path.

### Stockfish

Vendored at `.tools/stockfish/` (gitignored, ~100MB). `find_stockfish()`
checks explicit path, then `VOXPLAY_STOCKFISH_PATH`, then `.tools/`, then
PATH. `get_engine("auto")` falls back to `MockAnalysisEngine` when no binary
is present, so the demo path always runs. Mock evaluations are deterministic
and position-correlated but are not chess -- never report them as results.

## Test data

- `data/sample_match.json` — bundled T20 demo match, used by `voxplay demo` and `eval`
- `data/test_matches/eval_collapse.json`, `eval_chase.json`, `eval_last_over.json` — eval suite covering collapse, run-chase, and final-over scenarios
