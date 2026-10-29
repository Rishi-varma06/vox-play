# VoxPlay

**Automatic spoken commentary for games, from structured data alone.**

Millions of chess games are played every day and almost none of them are ever
explained. Engine analysis gives you an accurate table of move labels; human
commentary gives you a story but only exists for elite events. VoxPlay closes
that gap: paste a game link, get narrated commentary synced to a board, at
negligible cost per game.

*University capstone. Two domains -- chess and cricket -- on one pipeline.*

---

## What it does

```
PGN / Lichess link
      |
      v
  engine analysis  ->  move classification  ->  tactical motifs
      |
      v
  beat selection   <-- decides WHAT gets airtime and for how long
      |
      v
  commentary script  ->  verification gate  <-- rejects anything unsupported
      |
      v
  spoken audio + timeline  ->  synced board player
```

Stages 1-3 are chess-specific. Everything after is domain-agnostic, and the
proof is that it was written for cricket first.

---

## Quick start

```bash
pip install -e ".[dev]"
```

### The studio (browser)

```bash
python -m voxplay.cli.main serve --open
```

Upload a PGN, read the commentary it writes, edit anything you disagree
with, then press **Turn into audio**. Nothing is spoken until a person has
read it -- speech is the expensive artefact, and committing to it before the
words are right wastes the listener's time more than it wastes ours.

Two choices are worth knowing about. **How much to cover** selects the
pacing: the paced recap (the default, and what this project is about), every
move, or the eight biggest swings. The second two are the baselines the
paced arm is measured against, offered as modes because a listener who wants
every move is asking for one on purpose. **Voice** is chosen on the review
screen, after the text has been read, so nothing is paid for until the words
are right.

Edited text goes back through the same verifier the pipeline uses, so you
can type an invented tactic into a beat and watch it get flagged before it
is ever narrated.

### The pipeline (terminal)

Narrate the bundled 1858 Opera Game:

```bash
python -m voxplay.cli.main chess data/sample_game.pgn --player
```

Or any real game, fetched live:

```bash
python -m voxplay.cli.main chess https://lichess.org/<game-id> --player --baselines
```

`--player` writes a **single self-contained HTML file** -- board, audio,
captions, evaluation bar and move list. Open it in a browser and press play.
No ffmpeg, no server, no API key.

Add `--baselines` to print the pacing comparison the project is built around.

### Useful flags

| Flag | |
|---|---|
| `--audio` | synthesise speech and a timeline (implied by `--player`) |
| `--generator template\|claude\|auto` | `auto` uses Claude if a key is set, else the grounded template |
| `--nodes N` | engine budget per position (default 1,000,000) |
| `--threads N` | **keep at 1** -- above 1, results stop being reproducible |
| `--audience N` | listener rating; below 1600 chess terms get explained |
| `--persona analytical\|energetic` | narration register |
| `--tts offline\|gtts\|elevenlabs\|mock` | voice. `offline` needs no network or key; `elevenlabs` needs a key and spends credits |

### Voices

| | Needs | Sounds like |
|---|---|---|
| `offline` | nothing | a system voice; fine for checking timing |
| `gtts` | a network | clearly better, still evidently synthetic |
| `elevenlabs` | `ELEVENLABS_API_KEY` | broadcast; bills roughly one credit per character |

`gtts` and `elevenlabs` return MP3, which is converted to WAV before the
timeline measures it -- the board sync reads durations out of a WAV header
and will not take an estimate. That conversion needs ffmpeg; if none is on
your PATH, `pip install imageio-ffmpeg` supplies one without a system
install. `offline` emits WAV and needs neither.

---

## Measured results

Nothing below is an estimate.

| What | Result |
|---|---|
| Tactical motif recall vs. human-tagged Lichess puzzles | **91.6%** mean over 11 motifs, n=1,000 each; six at 100% |
| Move classification agreement with Lichess | **Cohen's kappa 0.932** on 24,255 moves from 339 games |
| Pacing vs. flat move-by-move | **1.8x** shorter on a 33-half-move game, **3.8x** on a 136-half-move one |
| Pacing vs. top-N by swing | similar length, but top-N never reaches the finish and clusters on adjacent half-moves |
| Spoken-duration model accuracy | **1.03x** predicted vs. actual |
| Hallucinations, model-written commentary | **6.2** errors per 100 beats without the gate, **0.0** with it |
| Cost per game, model-written | **$0.057** and 23 seconds, measured from API usage |
| Analysis speed | 137 positions in 37s, single-threaded and reproducible |

The flat baseline is one short line per move -- what move-by-move commentary
actually is, and what the systems in the literature produce. It is beaten on
length, and the gap widens with game length because the paced budget is
capped and the baseline's is not. Top-N is beaten on structure rather than
length: it finds the big swing but ends the commentary before the game does.

---

## The three things that are new here

Automatic chess commentary has a literature going back to Jhamtani et al.
(ACL 2018). "A language model writes chess commentary" is well covered. What
is not:

1. **Pacing.** Essentially all prior work emits one comment per move. There
   is no content-selection stage. In audio that is fatal.
2. **Spoken, synchronised output.** The literature generates text.
3. **Grounding by construction.** The model narrates facts it is handed and
   never derives a chess fact of its own; a verification gate checks every
   beat against those facts and regenerates anything unsupported. Hebbar et
   al. (2026) measured a strong model producing incorrect chess claims 22% of
   the time without tool support.

See [`docs/project-report.md`](docs/project-report.md) for the full survey.

---

## Validation tools

```bash
# Score motif detectors against the Lichess puzzle database (CC0)
python -m voxplay.cli.main validate-motifs --database data/lichess_db_puzzle.csv.zst

# Measure classifier agreement with Lichess's own annotations
python -m voxplay.cli.main agreement
```

---

## Cricket

The original domain, and the reason the architecture generalises. Cricket
needs a human scoring ball by ball before any commentary is possible, which
is exactly the problem chess does not have -- that is why the project moved.

```bash
python -m voxplay.cli.main demo          # full offline pipeline, bundled match
python -m voxplay.cli.main score         # score a match in the terminal
python -m uvicorn voxplay.web.app:app --reload --port 8000   # browser scorer
```

The chess domain was added *alongside* cricket without modifying the shared
core, which is the evidence for the reuse claim rather than an assertion
about it.

---

## Documentation

| | |
|---|---|
| [`docs/project-report.md`](docs/project-report.md) | Problem, literature survey, limitations, objectives, results |
| [`docs/weekly-diary.md`](docs/weekly-diary.md) | Running record of work, backed by commits |
| [`docs/listener-study-protocol.md`](docs/listener-study-protocol.md) | Pre-registered human evaluation design |
| [`docs/demo.md`](docs/demo.md) | Runbook for demonstrating the project live |
| `CLAUDE.md` | Architecture notes and the decisions that are load-bearing |

---

## Requirements

- Python 3.11+
- **Stockfish** for real analysis. Put it on PATH, set `VOXPLAY_STOCKFISH_PATH`,
  or drop it in `.tools/stockfish/`. Without it, `--engine mock` runs the whole
  pipeline offline with deterministic placeholder evaluations -- useful for
  testing, never for results.
- `ANTHROPIC_API_KEY` only for model-written commentary. Without it the
  grounded template generator runs instead, and the demo still works.

---

## Tests

```bash
python -m pytest tests/
```

444 tests, entirely offline -- no network, no API key, no engine required.

---

## Project structure

```
voxplay/
  chessgame/     chess domain: ingest, engine, motifs, beats, script,
                 speech, audio, player, validation, verification
  events/        cricket event schema and state engine
  ingest/        cricket scorecard parsers
  commentary/    LLM commentary engine (shared)
  eval/          cricket quality checks
  tts/           speech providers and assembly (shared)
  scorer/        interactive cricket scorer
  web/           browser-based cricket scorer
  cli/           command line entry point
data/            sample game, sample match, test fixtures
docs/            report sections, diary, study protocol
```
