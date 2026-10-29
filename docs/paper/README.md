# Voxplay — materials for the research paper

Everything in this folder is generated from, or checked against, the code at
the commit that contains it. Where a number here disagrees with
`docs/project-report.md`, this folder is the one that was re-measured.

| File | What it is |
|---|---|
| `prompts.md` | The system, outline, per-beat and batched prompts, verbatim |
| `sample_opera_game.json` | Full run: PGN metadata, timings, pacing, script, cost |
| `sample_lichess_blitz.json` | Same, on a 79-ply real game of a different archetype |

---

## 1. Tech stack, as measured on the development machine

| Component | Version | Notes |
|---|---|---|
| Python | 3.14.3 (Windows) | `audioop` left the stdlib in 3.13; see §6 |
| FastAPI | 0.136.1 | REST API and the studio |
| Uvicorn | 0.47.0 | ASGI server |
| Pydantic | 2.13.4 | Every inter-stage model |
| python-chess | 1.11.2 | Board logic, PGN parsing, UCI transport |
| Stockfish | **19** | Bundled at `.tools/stockfish/` |
| Anthropic SDK | 0.103.1 | |
| LLM | **`claude-opus-5`** | Script generation only — never chess reasoning |
| TTS | ElevenLabs `eleven_v4`, gTTS 2.5.4, system voice | Three backends, one interface |
| pydub | 0.25.1 | Audio assembly (non-WAV paths only) |

**Frontend: no framework.** The studio is hand-written HTML, CSS and
vanilla JavaScript served as static files — no React, no build step, no
bundler. This was deliberate: the deliverable had to run from a clone with
one command.

**Board rendering: no library.** Neither `chess.js` nor a board widget is
used. The board is rendered directly from FEN into DOM elements
(`voxplay/web/static/chess.js`, `paintBoard`) and, in the standalone player,
from the same FEN strings embedded in the file. python-chess does all board
*logic* server-side; the client only draws what it is given. The animated
board is driven by a timeline measured from the rendered audio, not by
estimated durations.

---

## 2. Move classification — read this before writing the methods section

**The thresholds are not centipawn thresholds.** This is the single most
important methodological point in the project and the easiest to get wrong
in a write-up.

Centipawn deltas are not comparable across positions: losing 100cp from
+900 is irrelevant, losing 100cp from 0.00 can be the game. Classifying on
raw centipawns over-reports blunders in decided positions and under-reports
them in sharp equal ones. Every evaluation is therefore mapped to **win
probability** first, and every threshold, salience score and pacing decision
downstream reads win probability, never centipawns.

```
Win% = 50 + 50 * (2 / (1 + exp(-0.00368208 * centipawns)) - 1)
```

The constant is Lichess's published mapping, chosen so our classifications
can be compared directly against Lichess's own annotations.

### The ladder

Thresholds are **win-percentage points lost by the move**:

| Label | Condition |
|---|---|
| `best` | the engine's top move |
| `book` | top move, still in opening theory |
| `good` | lost < 5 points |
| `inaccuracy` | lost ≥ 5 |
| `mistake` | lost ≥ 10 |
| `blunder` | lost ≥ 15 |

`COMMENTARY_THRESHOLDS` adds `decided_win_pct=90`: above 90% the severity is
capped at `inaccuracy`, because calling a move a blunder when the player is
still completely winning misdescribes the position. It is **off** for the
agreement experiment, so that comparison stays clean, and **on** for
commentary.

### How the thresholds were chosen — worth a paragraph in the paper

Community sources describe the bands as roughly 10 / 20 / 30 points. The
project shipped those numbers until they were tested. On 24,255 moves they
gave **Cohen's κ = 0.355** and agreed on **exactly zero** of Lichess's 2,133
inaccuracies, because they are about twice too lenient. Fitting to maximise
agreement lands on **5 / 10 / 15**, raising κ to **0.932**. The values are
round for a reason: Lichess thresholds a winning-chances delta of 0.1 / 0.2 /
0.3 on a −1..1 scale, which is 5 / 10 / 15 on this 0–100 one.

### Honest limitation

`BRILLIANT` and `GREAT` exist in the `MoveQuality` enum and are **never
assigned by anything**. They need sacrifice-soundness and only-move
detection combined, which is not implemented. The classifier produces six
labels, not eight. Do not claim eight.

---

## 3. Critical-moment detection

Three independent mechanisms, none of which involve the language model.

**Tactical motifs — board geometry only, no engine, no LLM.** Twelve
detectors: fork, pin, skewer, discovered attack, hanging piece, sacrifice,
smothered mate, back-rank mate, double check, promotion, and two standing
patterns. Names deliberately match Lichess puzzle themes so validation is a
direct comparison rather than a mapping exercise. Geometry can establish
that material was given up but not that a sacrifice was *sound*; soundness
is decided one layer up, where the evaluation lives.

**Opening deviation — indexed by position, not move order.** Comparing move
sequences prefix-by-prefix misses every transposition. Each known line from
the CC0 `lichess-org/chess-openings` database (~3,500 openings, ~390 KB) is
replayed and the resulting *position* indexed, so a transposed Queen's
Gambit resolves correctly. Popularity figures are out of reach: Lichess's
opening explorer has required an API token since 2026.

**Salience — the weighted score that drives pacing.**

| Signal | Weight |
|---|---|
| Evaluation swing | 1.00 |
| Narrative role (turning point / point of no return) | 0.60 |
| Tactical motif | 0.45 |
| Criticality (only-move gap) | 0.35 |
| Material swing | 0.25 |
| Think time vs that player's own baseline | 0.20 |

Selection is then **constrained**, which is the actual contribution: maximise
total salience subject to a spoken-duration budget, mandatory coverage
(beginning, decisive moment, end), minimum 4-ply spacing between moments,
and a bridging line whenever more than 12 plies would pass silently. Without
spacing, the top-scoring moves in a tactical flurry are all adjacent and the
script narrates four consecutive half-moves then skips twenty.

---

## 4. Results, re-measured for this document

Both runs: Stockfish 19, **1,000,000 nodes/position, single-threaded**,
`claude-opus-5`, `COMMENTARY_THRESHOLDS`.

| | Opera Game 1858 | Lichess blitz `JgdyxXZd` |
|---|---|---|
| Half-moves | 33 | 79 |
| Archetype | miniature | blunderfest |
| Engine time | **53.0 s** | **123.3 s** |
| LLM time | **23.5 s** | **20.0 s** |
| API calls | 6 | 6 |
| Tokens (in / out) | 6,305 / 682 | — |
| Cost | **$0.0486** | **$0.0464** |
| Beats | 5 | 5 |
| Paced length | 117.8 s | 117.8 s |
| Every-move baseline | 249.7 s (**2.1×**) | 597.8 s (**5.1×**) |
| Top-8 baseline | 194.6 s | 194.6 s |
| Verification | clean, 0 errors | clean, 0 errors |

The pacing advantage widening from 2.1× to 5.1× as the game lengthens is the
result worth putting in a figure: the paced budget is capped and the
baseline's is not.

### Validation against external data

| Measure | Result | Corpus |
|---|---|---|
| Tactical motif recall | **91.6%** mean, 6 detectors at 100% | 14,000 human-tagged Lichess puzzles |
| Classifier agreement | **κ = 0.932** (raw 97.8%, chance 66.8%) | 24,255 moves, 339 games, Lichess annotations |
| Hallucination rate | **6.2 → 0.0** per 100 beats, gate off → on | 32 beats of live model output |
| Cost per game | $0.0571 (gate off) / $0.0617 (gate on) | 5 games |

None of these labels were produced by this project — that is the point of
quoting them.

### ⚠ A number in the project report I could not reproduce

`docs/project-report.md` (Objective 1) states *"137 positions of a 136-half-
move game analysed in 36.9 seconds."* That is 0.27 s/position. Both runs
above measure **~1.55 s/position** at 1M nodes single-threaded, consistently
(34 pos → 53.0 s; 80 pos → 123.3 s). 137 positions should therefore take
**~212 s**, not 36.9 s.

Analysis is a **sequential loop** — there is no parallelism in
`analyse_game()`, despite a docstring that anticipates it. The 36.9 s figure
is consistent with roughly 200k nodes, i.e. the "Fast" preset, not the 1M
the reproducibility claims assume.

**Before this goes in a paper**: either re-measure at 1M nodes and update
the report, or state the node budget alongside the timing. A reviewer who
divides 137 by 36.9 will notice.

---

## 5. Multilingual support — there is none

Asked for in the brief, so stated plainly: **English only, and not by
oversight.**

`GTTSProvider` accepts a `lang` parameter, but it is not exposed by the CLI
or the web API and nothing sets it. More fundamentally, the barrier is not
translation:

- `speech.py` converts notation to spoken English by **rule** — "e4" becomes
  "e four", "Nf3" becomes "knight to f three". That grammar is
  English-specific and would need rewriting per language.
- Every prompt is English.
- The verifier's notation regex, square vocabulary and name-grounding word
  lists are English.

Translating the output would also break the guarantee: the gate checks the
*generated text* against the facts, and a translated string has not been
checked. Doing this properly means a spoken-form grammar and a verifier
vocabulary per language, which is a project of its own. Present it as future
work, not as a partial feature.

---

## 6. Problems hit, and how they were fixed

Reviewers do like this section, and these are all real, all in git history.

**Thresholds taken from documentation were twice too lenient.** κ = 0.355,
zero agreement on 2,133 inaccuracies. Fixed by fitting to the corpus: κ =
0.932. *The lesson that generalises: a number from a community wiki is a
hypothesis, not a constant.*

**Motif recall was 46.5% because of a scoring bug, not a detection bug.** The
validator scored only the first move of each puzzle; Lichess tags describe
the whole solution line. Fixed: 91.6%.

**The hallucination rate was overstated at 18.8 per 100 beats.** Most
"errors" were the checker mishandling apostrophes and British spellings, not
the model inventing anything. Real rate: 6.2.

**A fix that improved a metric and made the product worse.** One change took
a detector to 100% recall while making it call Morphy's queen sacrifice "a
piece left undefended". Measured, then reverted. *A number that improves is
not automatically a system that improves.*

**Multi-threaded Stockfish is not reproducible.** Lazy SMP shares a
transposition table between racing threads, so a fixed node budget does not
pin the result above one thread: two 4-thread runs of the same game
disagreed on **27 of 33 half-moves**, enough to move the detected turning
point. Everything reported is single-threaded.

**The audio timeline drifted when a dependency appeared.** The shared
assembler inserts a 400 ms gap on its pydub path and none on its pure-WAV
path, so board-sync offsets depended on whether ffmpeg happened to be
installed. Fixed by giving the chess path its own assembler with an explicit
gap, and computing offsets from the same frame counts that get written.

**pydub is broken on Python 3.13+.** It imports `audioop`, removed from the
stdlib in 3.13, so MP3 assembly failed entirely — meaning gTTS had never
worked on the dev machine despite being offered as a CLI option. Fixed with
the `audioop-lts` backport plus a pip-installed static ffmpeg. A related bug
made a missing ffmpeg skip *every* clip silently and export an empty track;
it now raises.

**Full-match commentary came out as generic filler.** Flat beats were handed
a word count and no facts at all, so the model wrote general advice about
chess — and the verifier passed it, because vague prose asserts nothing
checkable. Fixed by giving flat beats the same per-move facts a moment gets.

**The narrative archetype was classified and then discarded.** Eight shapes
computed from the win-probability trace, handed to the model as one word, so
a collapse and a grind were narrated in the same register. Now each carries
guidance on emphasis. Doing this exposed a sentinel bug: `extract_arc()`
defaulted to `DRAW` when nothing was analysed, so a decisive game was told
"shape: draw" beside "result: White won" — harmless as one word, actively
misleading once it became a paragraph of instruction.

**The engine's principal variation was computed on every move and read by
nothing.** The narrator could say which move was better but never what it
led to. Now four plies of the line reach the facts, mirrored in the
verifier's licence so the gate's guarantee is unchanged.

**Separately synthesised clips sounded assembled.** Each beat was a separate
TTS request with no knowledge of its neighbours, so every line closed as
though the game ended there. Fixed by passing `previous_text` / `next_text`,
neither of which is spoken.

---

## 7. What I cannot supply — you need these from the team

| Item | Who has it |
|---|---|
| Four author names + registration numbers | You |
| Faculty mentor's name and designation | You |
| Department name as it should appear | You |
| Format: IEEE two-column vs university template | Your department |
| Page/word limit, and which deadline | Third Review 21–29 Nov? Final 10 Dec? |
| Mentor feedback from reviews 1 and 2 | You |

**User feedback / ratings: none exists yet.** The listener study is designed,
pre-registered and implemented (`docs/listener-study-protocol.md`,
`voxplay/chessgame/study.py`) but **has not been run**. It is the one
incomplete objective. If the paper needs human evaluation, this is the
blocker — not a writing task.

Informal feedback from 5–10 people would *not* satisfy the pre-registered
protocol and should not be presented as if it did. Either run the study, or
state plainly that human evaluation is pending and cite the pre-registration.

**Existing references:** `docs/project-report.md` ends with a 10-item list,
including the five chess-commentary papers the related-work section is built
on. Reuse it so citations stay consistent across reviews.
