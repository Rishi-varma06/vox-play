# Running Voxplay in front of an examiner

A 7-10 minute demo with nothing that can fail silently. Every step has a
fallback, and the fallbacks are already built.

---

## Before you start

Five minutes, the night before and again ten minutes before.

```bash
cd C:\Users\rishi\voxplay
python -m pytest tests/ -q
```

Expect `520 passed`. If that works, Python, the engine wrapper and every
layer are fine.

| Check | Command | Expect |
|---|---|---|
| Engine found | `python -c "from voxplay.chessgame.engine import find_stockfish; print(find_stockfish())"` | a path ending `stockfish-windows-x86-64-universal.exe` |
| API credit | run Act 2 once the night before | it prints a script, not an error |
| Network | open lichess.org | loads |

**Open these two files in browser tabs before you begin**, and leave them
open. They are complete, pre-generated runs. If anything live fails, you
switch tabs and keep talking.

```
demo\1_opera_game.html          Morphy, 1858 -- the classic
demo\2_live_lichess_game.html   a real blitz game pulled from Lichess
```

**Make the terminal font large.** Nobody can read 11pt from the back of a
room.

---

## Act 1 -- The problem (45 seconds, no computer)

Say it before you run anything:

> Millions of chess games are played every day. Almost none of them are ever
> explained. You can get an engine report -- a table of move labels -- or you
> can get human commentary, which only exists for world championship matches.
> There is nothing in between. This builds the thing in between.

If you want a visual: open any Lichess game review. It is a table. That is
the point.

---

## Act 2 -- Run it (3 minutes)

Open the studio. This is the version to show a non-technical examiner.

```bash
python -m voxplay.cli.main serve --open
```

1. **Paste PGN** tab, then **Load the Opera Game**, then **Analyse game**.
   The progress bar counts real positions as Stockfish evaluates them.
2. About a minute later the review screen appears: accuracies, the opening
   and where they left theory, motif count, pacing against the every-move
   baseline, what the script cost.
3. Read a beat or two aloud. Point at the green **Verified** banner.
4. Press **Turn into audio**. Play it.

The terminal version does the same thing and prints more, if you would
rather show that:

```bash
python -m voxplay.cli.main chess data/sample_game.pgn --generator claude --player
```

This is Morphy's 1858 Opera Game, the most famous short game in chess. It
takes about 90 seconds. **Talk while it runs** -- the stages print as they
finish, so narrate them:

| What prints | What to say |
|---|---|
| `[1/4] Analysing 34 positions` | "Stockfish evaluates every position. Single-threaded, so it is reproducible -- more threads and you get a different answer for the same budget." |
| `accuracy 97.9% / errors 0` and `88.2% / errors 2 (Nf6, b5)` | **Stop here.** "Those two moves are the ones chess books identify as Black's errors. The system found them with no chess knowledge beyond a win-probability model." |
| `[2/4] Tactical motifs: 13` | "Board geometry, not the language model. Asking a model whether something was a fork gets you a confident answer either way." |
| `move 12 O-O-O discoveredAttack` | "That is the famous point of Morphy's castling move, found automatically." |
| `[3/4] Narrative arc: miniature, turning point: b5` | "It worked out where the game turned." |
| `[4/4]` then the script | Let them read it. |
| `verification: clean` | "Every claim checked against the facts the model was given." |
| `player -> ...html` | Move to Act 3. |

**If the API errors:** it falls back to the template generator automatically
and keeps going. Say "that is the offline generator, which needs no API at
all" and carry on. Nothing breaks.

---

## Act 3 -- The payoff (2 minutes)

Open the file it just printed, or switch to the `1_opera_game.html` tab.

**Press play.** Then stop talking and let it run for 30-40 seconds.

Then point at three things:

1. **The board follows the voice.** It is not a video -- the timeline knows
   which half-moves each sentence is about.
2. **The beat chips** along the bottom. Click `turning point`. It jumps
   straight there. "A supervisor reviewing twenty games does not watch them."
3. **The move list** colour-codes by quality, and **the eval bar** moves.

One file. No server, no install. You can email it.

---

## Act 4 -- It is not a canned demo (90 seconds)

This is the moment that proves it is real. Go to **lichess.org/tv**, copy the
URL of whatever game is being played *right now*, and run:

```bash
python -m voxplay.cli.main chess https://lichess.org/PASTE_ID --generator claude --player --baselines
```

> "That game finished about a minute ago. Nobody has ever commentated it, and
> nobody ever would have."

When `--baselines` prints, that is your research contribution:

```
7 beats, 143s      <- the system
every-move  462s   <- one line per move, what prior work does
top-8       195s   <- ranked by swing, no constraints
```

Say: "Almost every published system emits one comment per move. On this game
that is nearly eight minutes. The selection layer is the contribution."

**Risks and what to do:**

| If | Then |
|---|---|
| no network | switch to the `2_live_lichess_game.html` tab -- same thing, pre-run |
| the game is Chess960 or another variant | pick a different one from lichess.org/tv; say you filter to standard chess |
| the game is very long (100+ moves) | fine, it just takes ~60s instead of ~30s. Say "the budget scales with game length" |

---

## Act 5 -- The research (2 minutes)

**Do this one in the browser if the studio is already open.** It is the
strongest thirty seconds of the demo because the examiner watches you cause
the failure.

Go back to the review screen and type this over any beat:

```
A beautiful fork on h7 wins material, pure Kasparov at his very best.
```

Stop typing. Within a second the beat turns red and three separate checks
fire:

| | |
|---|---|
| `notation_leak` | `h7` is notation, not speech |
| `ungrounded_tactic` | no detector found a fork here |
| `ungrounded_name` | Kasparov was never in this game |

The banner flips from **Verified** to **3 unsupported claims found**. Say:

> "It does not matter that I wrote that sentence rather than the model. The
> gate checks the text, not the author."

Then undo it and watch the banner go green again.

The offline version, if the studio is not running:

```bash
python demo/show_gate.py
```

Runs offline in seconds. It shows grounded commentary passing, then injects
four kinds of invention and catches all four.

> "Published work measures how often a model invents chess facts -- one 2026
> paper found 22% for a strong model without tool support. We do not measure
> it afterwards. The model is never allowed to derive a chess fact; it is
> handed them, and everything it says is checked back against them."

Then quote the numbers from `docs/project-report.md`:

| | |
|---|---|
| Motif detection vs 14,000 human-tagged puzzles | **91.6%** recall, six detectors at 100% |
| Move classification vs Lichess's own labels | **Cohen's kappa 0.932** on 24,255 moves |
| Hallucinations, live model output | **6.2** per 100 beats without the gate, **0.0** with it |
| Cost | **$0.057** and 23 seconds per game |

---

## Questions you will be asked

**"Did the AI write this, or did you?"**
The system is engineering: engine integration, a win-probability model,
twelve tactical detectors, a constrained selection algorithm, speech
synthesis, a verification layer. The language model writes the final prose
from facts the system computes, and is never allowed to work anything out for
itself -- that is the architecture, not a limitation.

**"How do you know the commentary is correct?"**
Two ways. Everything factual is computed by code with tests, and validated
against external ground truth -- 91.6% motif recall against human-tagged
puzzles, kappa 0.932 against Lichess's own move labels. Then the verification
gate checks the generated text against those facts and regenerates anything
unsupported. Measured on live output: 0.0 errors per 100 beats with the gate.

**"Is this not just ChatGPT with extra steps?"**
Hand a language model a PGN and ask for commentary and it invents tactics
that are not on the board -- a 2026 benchmark measured 22% incorrect claims.
The contribution is what surrounds it: deciding *what* to talk about, which
no prior system does; speaking it synced to a board; and being unable to
assert something it was not given.

**"What is novel? Hasn't chess commentary been done?"**
Yes, since 2018 -- Jhamtani et al. established the benchmark and there is a
continuous line to NAACL 2025. All of it emits one comment per move, as text.
Three things are new here: **pacing**, **spoken synchronised output**, and
**grounding by construction**. The literature survey in the report says so
explicitly rather than claiming more.

**"Does it work for anything other than chess?"**
It already did. The core was built for cricket; chess was added alongside
without modifying it. That is evidence rather than a claim -- cricket needs a
human scoring ball by ball, which is why the project moved to chess.

**"What is left?"**
A listener study. The protocol is pre-registered and the software for running
it is built; it needs 30 people and a few weeks. Everything measurable
against data is measured.

---

## If everything goes wrong

Open `demo\1_opera_game.html`, press play, and talk over it. It is a complete
run of the whole pipeline. Then show `docs/project-report.md` for the
numbers. You lose the live element and nothing else.

---

## One-line cheat sheet

```bash
python -m pytest tests/ -q                                                    # proof it works
python -m voxplay.cli.main chess data/sample_game.pgn --generator claude --player
python -m voxplay.cli.main chess https://lichess.org/<id> --player --baselines
python demo/show_gate.py                                                      # the gate, offline
```
