# VoxPlay — AI Cricket Commentary Engine

Turns structured ball-by-ball match data into natural English audio commentary.

**University Capstone · Phase 1 MVP**

---

## Architecture

```
Input source          Event stream          Commentary engine         Audio
─────────────         ────────────          ─────────────────         ─────
scorecard file  ──►  BallEvent             Anthropic Claude   ──►   per-ball clips
interactive scorer ─► InningsStartEvent    (rolling 3-ball            │
[vision model*]       OverStartEvent        context window)            ▼
                      MilestoneEvent                          assembled track (.wav)
                      DrinksEvent

* Extension point — see voxplay/ingest/base.py
```

The **Event** is the single contract through the whole system. Input sources produce events; commentary/TTS consume them. Adding a new source (vision model, live feed) or a new output (multilingual, different TTS) requires no changes to other layers.

---

## Setup

```bash
# 1. Clone & enter the project
cd voxplay

# 2. Create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure API keys (optional — demo works without them)
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

> **ffmpeg** is needed by pydub to handle MP3 files. Install it from https://ffmpeg.org/download.html and add it to your PATH.  
> The mock TTS provider writes silent WAV files, so **ffmpeg is not required** to run the demo.

---

## Quick start — demo day

This command runs the complete pipeline on the bundled sample T20 match with **no API keys or internet connection**:

```bash
voxplay demo
```

Outputs land in `./output/demo/`:
- `demo_events.json` — the parsed event stream
- `demo_transcript.json` — generated commentary (one line per event)
- `demo_commentary.wav` — assembled audio track (silent placeholders in mock mode)

To run with live Anthropic commentary:
```bash
# .env must contain ANTHROPIC_API_KEY
voxplay demo
```

---

## All commands

### Interactive scorer
Score a match ball-by-ball in the terminal. State is computed automatically.

```bash
voxplay score
voxplay score --match-id my_match --out ./output
```

**Scorer key reference**

| Input | Action |
|-------|--------|
| `0`–`6` | Runs scored (4 and 6 set boundary/six flags automatically) |
| `w` | Wide (+1, ball not counted) |
| `n` | No-ball (+1, ball not counted) |
| `b <runs>` | Byes |
| `l <runs>` | Leg byes |
| `W` | Wicket (prompts for dismissal details) |
| `u` / `undo` | Remove last ball |
| `d` | Add drinks break |
| `s` / `save` | Save event stream and exit |
| `q` / `quit` | Quit without saving |
| `?` | Help |

### Ingest a scorecard file
```bash
voxplay ingest data/sample_match.json
voxplay ingest data/my_scorecard.csv --out ./output
```

**CSV column format** — required columns: `match_id, innings, over, ball, runs`.  
Optional: `bowler, batsman_striker, batsman_non_striker, extras_type, extras_runs, dismissal_type, dismissed_batsman, fielder, shot_tag, is_boundary, is_six, timestamp, tags` (pipe-separated).

### Generate commentary
```bash
voxplay commentate output/my_match_events.json
voxplay commentate output/my_match_events.json --mock   # offline
```

### Synthesize audio
```bash
voxplay synthesize output/my_match_transcript.json --tts gtts
voxplay synthesize output/my_match_transcript.json --tts mock   # silent stubs
```

---

## Running tests

```bash
pytest
pytest --cov=voxplay --cov-report=term-missing
```

Test coverage includes:
- Schema parsing and edge cases
- Automatic state computation (runs, extras, wickets, run rates, over rollover)
- Commentary windowing logic and mock generation
- Interactive scorer event output and undo

---

## Extension points

### Adding a new input source (e.g. a vision model)
See `voxplay/ingest/base.py` — implement the `EventSource` interface and wire it into `voxplay/cli/main.py` as a new subcommand. **No changes to the commentary engine or TTS layer are required.**

### Adding multilingual support
The commentary engine produces English text from a system prompt (`voxplay/commentary/prompts.py`). To add a language:
1. Add a translated `SYSTEM_PROMPT` and prompt builders in a new `prompts_<lang>.py`.
2. Add a language flag to `CommentaryEngine.__init__` and select the prompt module there.
3. Pass the language code to the TTS provider (gTTS supports ~60 languages via its `lang` parameter).

### Swapping in ElevenLabs TTS
See `voxplay/tts/elevenlabs_provider.py` — the stub explains exactly what to implement. Use `--tts elevenlabs` once done.

---

## Project structure

```
voxplay/
├── voxplay/
│   ├── events/          # Pydantic event schema + automatic state engine
│   ├── ingest/          # EventSource interface, CSV + JSON parsers
│   ├── commentary/      # Commentary engine + prompt builders
│   ├── tts/             # TTS interface, gTTS, ElevenLabs stub, assembler
│   ├── scorer/          # Interactive terminal scorer
│   └── cli/             # CLI entry point (argparse subcommands)
├── data/
│   └── sample_match.json   # Bundled T20 demo match
├── tests/
├── output/              # Generated files (gitignored)
├── requirements.txt
├── pyproject.toml
└── .env.example
```
