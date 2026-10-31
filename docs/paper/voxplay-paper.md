# Voxplay: Pacing and Verifiable Grounding for Spoken Chess Commentary Generated from Move Notation

> **DRAFT.** Author block, mentor, department and venue are placeholders —
> see `[TODO]` markers. All measurements are reproducible from the
> repository at the commit containing this file. Section VII reports a
> pre-registered human evaluation that **has not yet been conducted**; it is
> written as a protocol, not as a result, and must not be presented
> otherwise.

**Authors:** `[TODO: four names + registration numbers]`
**Guide:** `[TODO: mentor name, designation]`
**Department:** `[TODO: department, institution]`

---

## Abstract

Millions of chess games are played online daily, and almost none are ever
explained. Engine analysis produces a table of labels; human commentary
exists only for elite play. Prior work on automatic chess commentary has
converged on generating text one comment per move, and evaluates factual
accuracy after generation rather than preventing error during it. We present
Voxplay, a system that converts move notation alone — no video, no vision —
into spoken commentary synchronised to an animated board. Three design
decisions distinguish it. First, commentary is *paced*: beat selection is
posed as constrained optimisation over salience subject to a spoken-duration
budget, mandatory narrative coverage, and spacing constraints, reducing a
79-half-move game from a 598-second move-by-move narration to 118 seconds.
Second, the language model is never asked to derive a chess fact; it is
handed facts computed by deterministic, independently validated code and
asked only to phrase them. Third, every generated sentence is checked
against the facts that licensed it before it is spoken, and a failing
sentence is regenerated with the failure fed back. Against external
human-labelled corpora the components achieve 91.6% mean tactical-motif
recall over 14,000 Lichess puzzles and Cohen's κ = 0.932 against Lichess's
own move annotations over 24,255 moves. Enabling the verification gate
reduces observed hallucination from 6.2 to 0.0 per 100 beats on live model
output, at a cost of approximately US$0.05 per game. A pre-registered
listener study to evaluate engagement, informativeness and naturalness is
specified and implemented but not yet run; its design and thresholds are
reported here as fixed in advance.

**Keywords** — automatic sports commentary, data-to-text generation, content
selection, grounded generation, hallucination mitigation, text-to-speech,
chess.

---

## I. Introduction

A chess game is a complete, unambiguous record of events. Every move is
known, every position is computable, and a strong engine can quantify the
consequence of each decision. Despite this, the experience available to an
ordinary player who has just finished a game is a table: a list of moves
tagged *blunder*, *mistake*, *inaccuracy*, with numeric evaluations beside
them. The table says a move was bad. It does not say what was missed, which
of eighty moves actually decided the outcome, or why. And a table cannot be
listened to.

Human commentary supplies all of that, and is economically restricted to
elite play: it requires an expert, working for hours, per game. Between the
engine report and the grandmaster broadcast there is nothing.

This paper describes a system that occupies that gap. The input is the move
list — the same thing a player would write on a scoresheet. The output is
spoken audio with a board that follows the narration. No video is processed
and no vision model is involved.

### A. Why chess is the right domain for this problem

Chess has a property most commentary domains lack: **the facts are
computable and checkable**. Whether a move was a blunder, whether a fork
exists on the board, whether a player left opening theory, how much winning
probability a decision cost — each can be established by deterministic code
and verified independently. This makes it possible to build a generation
pipeline in which the language model is structurally incapable of inventing
a domain fact, because it is never asked to produce one. That property is
what the present work exploits, and it is the reason the approach
generalises less readily to domains where ground truth must itself be
inferred.

### B. Contributions

1. **Pacing as constrained selection.** We formulate what to say as
   maximising total salience subject to a spoken-duration budget, coverage
   requirements and spacing constraints, and implement two baselines
   (every-move and unconstrained top-N) in the same codebase so the
   contribution can be isolated rather than asserted.

2. **Grounding by construction.** All chess facts are computed by
   independently validated deterministic components and supplied to the
   model as an explicit, closed fact block. The model's task is reduced to
   realisation.

3. **A verification gate with a symmetric licence.** Every generated
   sentence is checked against a *licence* — the set of claims the beat's
   facts support — derived from the same function that builds those facts,
   so the two cannot drift apart without a test failing.

4. **Speech with a measured timeline.** Audio offsets are measured from the
   rendered waveform rather than estimated from word counts, which is what
   permits board synchronisation to be correct rather than approximately
   correct.

5. **A pre-registered evaluation protocol** with thresholds fixed before
   data collection, reported here in advance of the study.

---

## II. Related Work

### A. Chess commentary generation

Jhamtani et al. [1] introduced the task with a dataset mined from chess
forum discussion, training models to produce a comment per move. Zang et al.
[2] coupled generation to a neural chess engine so that commentary could
reference evaluation. Lee et al. [3] combined language models with symbolic
reasoning engines, improving factual grounding. Kim et al. [4] introduced
concept-guided generation with a dedicated evaluation metric (GCC-Eval),
prioritising which chess concepts a comment should express.

All four generate **text**, and all four operate **per move**. Concept
prioritisation [4] selects *what to say about a move*; none selects *which
moves to speak about at all*, which is the problem a listener experiences as
pacing.

### B. Factuality in commentary

Hebbar et al. [5] measured hallucination directly, finding a strong
contemporary model produced incorrect chess claims 22.0% of the time without
tool support, and smaller open-weight models exceeding 40%. Their
contribution is tool-augmented *evaluation* — measurement after generation.
The present work addresses the complementary half: preventing the error
during generation, and refusing to speak a sentence that fails.

### C. Commentary in other sports, and data-to-text

MatchTime [6] generates soccer commentary from video, a fundamentally
different input modality with inferred rather than computed ground truth. A
recent survey of AI-generated game commentary [7] confirms the general
pattern: dense, per-event text output, with factuality treated as a
post-hoc metric.

The content-selection problem itself is older than the current literature on
commentary. Barzilay and Lapata [10] framed collective content selection for
concept-to-text generation as a constrained decision over candidate records
rather than independent per-record choices. Our pacing formulation is in
that tradition, with the addition of a *duration* budget — a constraint that
only becomes meaningful once the output is spoken rather than read.

### D. Positioning

| Work | Selection / pacing | Output | Factuality |
|---|---|---|---|
| Jhamtani et al. [1] | none — every move | text | human rating |
| Zang et al. [2] | none — every move | text | automatic + human |
| Lee et al. [3] | none — every move | text | human preference |
| Kim et al. [4] | concept prioritisation | text | GCC-Eval |
| Hebbar et al. [5] | evaluation only | — | tool-augmented eval |
| **Voxplay** | **budgeted beat selection** | **speech + synced board** | **grounded + gated** |

That a language model can write chess commentary is established. Our claims
are pacing, spoken delivery, and grounding enforced by construction.

---

## III. System Architecture

The pipeline has six stages. Stages 1–3 are chess-specific; stages 4–6 are
domain-agnostic and are shared, unmodified, with a second sport implemented
in the same codebase.

```
PGN / Lichess link
  │
  ├─1─ Ingest        notation → structured move events
  ├─2─ Evaluate      Stockfish, every position
  ├─3─ Detect        12 tactical motifs, opening theory
  ├─4─ Pace          salience + budget + coverage + spacing
  ├─5─ Write         LLM realises the supplied facts
  ├─6─ Verify+Speak  licence check, then TTS + timeline
  │
  └─→ audio + synchronised board
```

The architectural invariant is the boundary between stages 1–3 and stage 5.
Everything the narration may assert is computed before the model is invoked.
The dependency graph is acyclic: the data model imports nothing internal and
every later stage depends only on earlier ones, which is what allows beat
selection to be tested without an engine and verification to be tested
without an API key.

### A. Implementation

Python 3.14, FastAPI 0.136 for the API and web studio, python-chess 1.11.2
for board logic and PGN parsing, and Stockfish 19 as the analysis engine.
Script generation uses `claude-opus-5`. Three speech backends sit behind one
interface: the host system voice, Google Text-to-Speech, and ElevenLabs
(`eleven_v4`).

Two implementation choices are worth noting because they are frequently
assumed otherwise. The web interface uses **no frontend framework** — plain
HTML, CSS and JavaScript, no build step — and the board uses **no chess
rendering library**: positions are drawn directly from FEN into DOM
elements, with python-chess performing all board logic server-side.

---

## IV. Methodology

### A. Evaluation in win probability, not centipawns

Centipawn differences are not comparable across positions. Losing 100
centipawns from +900 is irrelevant; losing 100 centipawns from an equal
position can decide the game. Classifying on raw centipawns therefore
over-reports blunders in decided positions and under-reports them in sharp
equal ones.

Every evaluation is mapped to win probability using Lichess's published
logistic [8]:

> Win% = 50 + 50 · (2 / (1 + exp(−0.00368208 · centipawns)) − 1)

This mapping is already saturated at the extremes, so the same 100-centipawn
delta is worth approximately 12 percentage points near equality and under
one point at +900. **Every** downstream component — classification, salience
scoring, pacing — reads win probability; centipawns do not propagate past
this stage. The specific constant is adopted so that our classifications are
directly comparable against Lichess's own annotations, which Section VI
exploits.

Engine analysis is budgeted by **nodes**, not depth or wall-clock time.
Time-limited search returns different results on different machines and
under different load, which would make every number in this paper
irreproducible. A node budget is deterministic for a fixed engine build. All
reported results use 1,000,000 nodes per position, `MultiPV` 3, and **a
single search thread** — see Section VIII-A for why the thread count is not
a free parameter.

Positions, not moves, are evaluated: the position after move *i* is the
position before move *i+1*, so an N-move game requires N+1 evaluations
rather than 2N.

### B. Move classification

Labels are assigned from win-probability points lost by the move:

| Label | Condition |
|---|---|
| `best` | the engine's top move |
| `book` | top move, still within opening theory |
| `good` | lost < 5 points |
| `inaccuracy` | lost ≥ 5 points |
| `mistake` | lost ≥ 10 points |
| `blunder` | lost ≥ 15 points |

A commentary-specific variant caps severity at `inaccuracy` once the mover's
win probability exceeds 90%, since describing a move as a blunder while the
player remains completely winning misdescribes the position. This cap is
disabled for the agreement experiment of Section VI-B so that comparison
remains clean.

**On the choice of thresholds.** Community documentation describes these
bands as approximately 10 / 20 / 30 points, and the system used those values
initially. Tested against 24,255 annotated moves they yielded Cohen's
κ = 0.355 and agreed with **zero** of the 2,133 moves Lichess labels
*inaccuracy* — they are approximately twice too lenient. Fitting thresholds
to maximise agreement on that corpus yields 5 / 10 / 15 and κ = 0.932. The
fitted values are exactly round because Lichess thresholds a winning-chances
delta of 0.1 / 0.2 / 0.3 on a −1…1 scale, equivalent to 5 / 10 / 15 on a
0–100 scale. We report this because the initial values were taken from
documentation and were wrong by a factor of two; a published constant is a
hypothesis, not a measurement.

### C. Tactical motif detection

Twelve motifs — fork, pin, skewer, discovered attack, hanging piece,
sacrifice, smothered mate, back-rank mate, double check, promotion, and two
standing patterns — are detected from **board geometry alone**: no engine,
no language model. A model asked whether a position contains a fork will
answer confidently either way, and a listener who plays chess will detect
the error. Detector names deliberately match Lichess puzzle theme labels so
that validation (Section VI-A) is a direct comparison rather than a mapping
exercise.

Geometry can establish that material was given up but not that a sacrifice
was *sound*; soundness is decided in the analysis layer, which has the
evaluation.

### D. Opening deviation by position index

Naming the opening and identifying where players left theory is one of the
most natural things a commentator says. The obvious implementation compares
the game's move sequence against known lines prefix by prefix, which misses
every transposition: a game reaching a known position by a different move
order is reported as a novelty. We instead replay each line in the CC0
`lichess-org/chess-openings` database (~3,500 openings) and index the
resulting **position**, so transpositions resolve correctly at the cost of a
one-time index build.

### E. Pacing as constrained selection

This is the component the contribution rests on. Let *M* be the half-moves
of a game. For each *m ∈ M* we compute a salience score as a weighted sum of
normalised signals:

| Signal | Weight | Description |
|---|---|---|
| Evaluation swing | 1.00 | win probability lost, saturating |
| Narrative role | 0.60 | turning point / point of no return |
| Tactical motif | 0.45 | strongest motif present, weighted by type |
| Criticality | 0.35 | gap between best and second-best move |
| Material swing | 0.25 | material won or lost |
| Think time | 0.20 | relative to that player's own baseline |

Swing dominates by design — what *changed* is more interesting than what was
merely elegant — but a sound sacrifice that holds the evaluation steady
would score nothing on swing alone, which is why motifs carry substantial
weight.

Selection then maximises total salience subject to:

- a **spoken-duration budget** (default 120 s), where duration is estimated
  from word counts using an empirically measured speaking rate;
- **coverage**: the script must contain an opening, a decisive moment, and a
  finish;
- **spacing**: at least 4 plies between selected moments, and no more than
  12 plies of silence without a bridging line.

The constraints, not the scoring, are the contribution. Without spacing, the
highest-scoring moves in a tactical flurry are all adjacent, and the script
narrates four consecutive half-moves in detail and then skips twenty — which
is precisely the failure mode of the unconstrained top-N baseline.

**Measured speaking rate.** Duration budgeting uses 1.85 written words per
second, which is *measured*, not derived from a nominal speaking rate. A
134-word script rendered at a nominal 155 wpm produced 72.6 s of audio — an
effective 111 wpm. The 40% discrepancy arises from clause-boundary pauses,
numeral expansion ("98 percent" is three spoken words), and per-clip leading
and trailing padding. Budgeting at the nominal rate promises a two-minute
recap and delivers nearly three.

### F. Script generation

Generation proceeds in two stages: one call establishes a through-line for
the game, then each beat is realised in a separate call that receives the
outline, the lines already spoken (to prevent repetition), and a **fact
block** that constitutes the complete and only basis for that sentence. If a
fact is not in the block, the narrator may not state it.

Notation is converted to spoken form by **rule**, not by prompt. Asking a
model to read "Qxh7#" aloud succeeds most of the time and then produces
"queen x h seven hash" on the move that matters. The conversion is built
from structured fields (piece, origin, destination, flags) rather than by
parsing SAN, so there is no string to misparse.

For full-coverage sheets, beats are generated in runs of six per call and
parsed back by index, rejecting the batch entirely if the response shape is
wrong. A partially parsed batch would attach one move's commentary to a
different move — an error in which every individual claim remains true, and
which the verifier therefore cannot detect.

### G. The verification gate

Each generated sentence is checked against a **licence**: the set of
claims the beat's facts actually support, comprising permitted squares,
permitted motifs, permitted names, the position evaluation, and whether the
result may be revealed.

Seven checks run: notation leakage into speech, ungrounded squares,
ungrounded tactics, evaluation contradiction, ungrounded names, cross-beat
repetition, and length against the beat's budget. The first five are
factual defects; the last two are stylistic warnings.

The licence is derived from the same function that builds the fact block.
This symmetry is load-bearing in both directions: a fact supplied but not
licensed produces a false positive (an early version flagged the engine's
own suggested move as an invented square), and a claim licensed but never
supplied produces a blind spot.

A beat that fails is **regenerated** with the specific failure fed back,
rather than shipped. Models invent when reaching past their facts, and
naming the unsupported claim is usually sufficient to stop it.

### H. Speech and timeline

Audio offsets are **measured from the rendered waveform**, never estimated.
Clips produced in compressed formats are transcoded before any offset is
taken, so the rule that durations come from a WAV header holds regardless of
which voice was used — the choice of backend cannot silently desynchronise
the board.

Each clip is synthesised with its neighbouring lines supplied as unspoken
context. Without this, a line generated in isolation closes as though the
game ended there and begins without preceding intonation, and a six-beat
recap is heard as six announcements rather than one person speaking.

---

## V. Implementation and Interface

A web studio exposes the pipeline as four steps: upload a game, analyse,
**review the generated text**, then synthesise speech. Speech is deliberately
gated behind human review — it is the expensive artefact, and committing to
it before the words are correct wastes the listener's time. Text edited by
the user is re-verified by the same gate that checks generated text, so an
invented claim typed by a human is caught identically.

Three coverage modes are selectable: the paced recap (default), every move,
and top-N by swing. The latter two are the baselines of Section VI-C,
exposed as modes because a listener requesting full coverage is requesting
the baseline deliberately. The paced mode remains the default; the claim of
this work is that pacing is *better*, not that coverage is unavailable.

---

## VI. Experimental Validation

All component results are measured against **externally produced** labels
that this project did not generate.

### A. Tactical motif recall

Detectors were scored against the Lichess puzzle database (CC0), using
1,000 puzzles per theme across 14 themes (14,000 puzzles), plus 2,000
control positions to detect over-firing.

**Mean recall across 11 comparable motifs: 91.6%**, with six detectors at
100%. The pin detector is the notable exception at 47.0%, discussed in
Section VIII-B.

An early measurement of this same quantity returned 46.5%. The cause was a
scoring defect, not a detection defect: the validator scored only the first
move of each puzzle, whereas Lichess theme tags describe the complete
solution line.

### B. Classification agreement

The classifier was compared against Lichess's own published annotations on
**24,255 moves from 339 analysed games**.

| Metric | Value |
|---|---|
| Raw agreement | 97.8% |
| Agreement expected by chance | 66.8% |
| **Cohen's κ** | **0.932** |

We report κ rather than raw agreement because the class distribution is
heavily skewed toward non-errors, and raw agreement would flatter the result.
κ above 0.8 is conventionally described as almost perfect.

### C. Pacing against both baselines

Both baselines are implemented in the same module as the selector and
produce identically shaped output, so an A/B comparison is possible.

| | System | Top-N | Every move |
|---|---|---|---|
| Opera Game (33 half-moves) | 117.8 s | 194.6 s | 249.7 s (**2.1×**) |
| Blitz game (79 half-moves) | 117.8 s | 194.6 s | 597.8 s (**5.1×**) |
| Reaches the finish | yes | **no** | yes |
| Adjacent picks (long game) | 0 | 3 | n/a |

Two distinct comparisons, which must not be conflated.

**Against every-move**, the advantage is length, and it *widens* with game
duration — from 2.1× to 5.1× — because the paced budget is capped and the
baseline's is not.

**Against top-N**, the advantage is not length: on the longer game top-N is
*shorter*, being fixed at eight beats regardless of game length. The
advantage is structure. Top-N locates the turning point in both games, so
scoring alone is not what it lacks; it terminates the commentary before the
game ends, and spends three of eight beats on half-moves adjacent to another
selection. This is the evidence that constraints, and not merely scoring,
carry the contribution — and the reason both baselines exist rather than one.

**A correction recorded for transparency.** An earlier version of this
comparison reported the every-move baseline at 594 s because each of its
lines received a full 45-word treatment. That is not what move-by-move
commentary is — the systems in the literature emit one short sentence per
move — so the baseline was a strawman and the advantage overstated by
roughly a factor of four. The flat arm now emits one short line per move,
and the figures above are the corrected ones.

### D. Hallucination and the verification gate

Measured on live model output over 32 generated beats:

| | Hallucinations / 100 beats | Cost |
|---|---|---|
| Gate disabled | **6.2** (2 of 32) | $0.0571 |
| Gate enabled | **0.0** (0 of 32) | $0.0617 |

The ungated rate of 6.2% is substantially below the 22.0% reported by
Hebbar et al. [5] for an untooled model, which is the expected consequence of
supplying facts rather than requesting them; the gate then removes the
remainder at a cost increase of approximately 8%.

An earlier measurement of this quantity returned 18.8 per 100 beats. On
inspection, the majority of those "errors" were the checker mishandling
apostrophes and British spelling variants rather than the model inventing
anything.

### E. Cost and latency

Measured end-to-end at 1,000,000 nodes per position, single-threaded,
`claude-opus-5`:

| | Opera Game (33 plies) | Blitz game (79 plies) |
|---|---|---|
| Engine analysis | 53.0 s | 123.3 s |
| Script generation | 23.5 s | 20.0 s |
| API calls | 6 | 6 |
| **Cost** | **$0.0486** | **$0.0464** |
| Verification | clean | clean |

Script generation cost and latency are approximately constant in game
length, because the number of beats is bounded by the duration budget rather
than by the number of moves — a direct consequence of pacing. Engine
analysis scales linearly at approximately 1.55 s per position and dominates
total runtime for longer games.

### F. Software quality

The implementation comprises approximately 11,600 lines of Python with 551
automated tests, covering the deterministic components (classification,
motifs, pacing, verification, timeline arithmetic) and the API surface.
Single-threaded engine analysis is verified bit-identical across repeated
runs.

---

## VII. Planned Human Evaluation

**This study has not been conducted.** Its design, hypotheses, thresholds,
sample size and analysis plan were committed to the repository before any
data collection, and are reported here as a pre-registration. No human
ratings are claimed anywhere in this paper.

The component results of Section VI establish that the commentary is
*correct*. They cannot establish that it is *worth listening to*, which is a
claim about human experience and the central claim of this work.

### A. Conditions

Five arms, each rendered identically so participants cannot distinguish them
by format: **(A)** flat, one line per half-move; **(B)** top-N by swing;
**(C)** the system; **(D)** the system's pacing with sentences assembled
mechanically from the facts rather than written by a model; **(E)**
commentary written by a recruited club player (1800+) given the same PGN and
engine analysis but no system output.

A and B isolate pacing; **D isolates the language model**, having identical
pacing and identical facts and differing only in that nothing wrote the
prose; E is the ceiling.

### B. Pre-registered hypotheses

| | Hypothesis | Supported if |
|---|---|---|
| H1 | Pacing improves engagement | C > A on engagement, median Δ ≥ 1.0, p < 0.05 |
| H2 | Constraints matter, not just scoring | C > B on pacing, median Δ ≥ 0.5 |
| H3 | Near the human ceiling | C within 0.5 Likert points of E on informativeness |
| H4 | The model earns its cost | C > D on engagement, median Δ ≥ 0.5 |
| H5 | Grounding holds up to listeners | C ≥ E on perceived accuracy |
| H6 | Cost and latency | < $0.10 and < 60 s model time per game |

H3 is deliberately a **non-inferiority** claim. The intuitive hypothesis —
that this is "as informative as" human commentary — is not testable as
stated, having no threshold and no matched corpus.

**H6 is already settled** by Section VI-E: $0.0486 and 23.5 s of model time
against thresholds of $0.10 and 60 s. Note that this bounds *model* time;
engine analysis exceeds 60 s on longer games and is reported separately.

### C. Design

Within-subjects, *n* = 30 (minimum 24), stratified 10/10/10 across beginner,
casual and club-strength listeners. Each participant hears four clips, each
a different game in a different condition, assigned by Latin square so that
condition does not confound with presentation order or game difficulty.
Five 5-point Likert items per clip — informativeness, engagement, perceived
accuracy, pacing, naturalness — worded as statements about the listener's
experience and never mentioning automation.

Analysis: Friedman test across conditions per item, Wilcoxon signed-rank for
the pre-specified pairs, Holm–Bonferroni correction, with median differences
and bootstrap 95% confidence intervals reported alongside every p-value.

### D. What a negative result would mean

If C does not exceed A on engagement, the pacing hypothesis is not
supported, and that is the finding. The component results of Section VI
stand regardless, having been measured against ground truth rather than
opinion.

---

## VIII. Limitations

**A. Reproducibility constrains engine configuration.** Stockfish's Lazy SMP
shares a transposition table between racing threads, so a fixed node budget
does *not* determine the result above one thread. Measured: two four-thread
runs of the same game disagreed on 27 of 33 half-moves, enough to relocate
the detected turning point. All reported analysis is single-threaded, which
costs roughly 1.55 s per position.

**B. Motif detection is rule-based.** Twelve well-defined patterns.
Deflection, overloading and zugzwang are not detected. The pin detector
trades recall for precision — 47.0% with its filter against 63.5% without —
because the unfiltered version produced 17 "pins" on a single short game and
drowned the two that mattered. Both figures are reported.

**C. Two move-quality labels are unimplemented.** `brilliant` and `great`
exist in the data model and are never assigned; they require combined
sacrifice-soundness and only-move detection. The classifier produces six
labels.

**D. Broadcast-quality speech is metered.** The highest-quality backend
bills per character, placing a paced game at roughly 1,600 characters. The
near-zero per-game cost and the best available delivery cannot be had
simultaneously.

**E. Delivery is shaped more coarsely than the text.** Clips are separated by
a fixed gap regardless of content, and the two narration personas differ by
a single instruction each. A commentator's rhythm — pausing longer before a
decisive move — is only partially reproduced.

**F. English only.** This is not an oversight of configuration. The
notation-to-speech converter is a rule-based *English* grammar, the prompts
are English, and the verifier's vocabulary is English. Translating generated
output after verification would also void the gate's guarantee, since the
translated string has not been checked.

**G. Human evaluation is pending**, as stated in Section VII.

---

## IX. Future Work

**Live commentary, and why it is not a small change.** Lichess streams moves
as they are played, and every per-position component — evaluation, motif
detection, opening theory, classification, verification, speech — functions
unchanged on streamed input. Pacing does not. Budget allocation requires
knowing the game's length; the narrative archetype cannot be determined
before the game concludes; the turning point is by definition the largest
error *of the game*. A naive live system would therefore reproduce the
every-move baseline in real time.

Two designs preserve part of the contribution: running on a fixed delay of
several moves to obtain a lookahead window, or pacing within fixed segments
against a per-segment budget. Both are genuine extensions, and both make
explicit that pacing is fundamentally a lookahead problem.

**Rating-conditioned thresholds.** A +2.00 position is substantially less
decided between 1200-rated players than between masters; the logistic
constant is currently global.

**Multilingual output**, requiring a spoken-form grammar and verifier
vocabulary per target language.

**Learned salience weights**, fitted to listener preference data once the
study of Section VII provides it.

---

## X. Conclusion

We have described a system that converts chess move notation into spoken
commentary with a synchronised board, without video. Its contributions are
pacing — formulated as constrained selection and measured against two
implemented baselines — grounding by construction, and a verification gate
whose licence is derived symmetrically from the facts it checks against.

Component validation against externally labelled corpora gives 91.6% mean
motif recall over 14,000 puzzles and κ = 0.932 over 24,255 annotated moves.
The verification gate reduces observed hallucination from 6.2 to 0.0 per 100
beats, at approximately $0.05 per game and under a minute of model time.

The evaluation that remains is the one only listeners can provide, and its
protocol is fixed and published in advance. We report it here as designed
rather than as completed, and note that several of the measurements above —
the classification thresholds, the motif recall figure, the hallucination
rate, and the every-move baseline — reached their final values only after an
initial result was found to be wrong. Inspecting individual cases rather
than aggregates is what located each of those errors.

---

## References

[1] H. Jhamtani, V. Gangal, E. Hovy, G. Neubig, and T. Berg-Kirkpatrick,
"Learning to Generate Move-by-Move Commentary for Chess Games from
Large-Scale Social Forum Data," *ACL*, 2018.

[2] H. Zang, Z. Yu, and X. Wan, "Automated Chess Commentator Powered by
Neural Chess Engine," *ACL*, 2019.

[3] A. Lee, D. Wu, E. Dinan, and M. Lewis, "Improving Chess Commentaries by
Combining Language Models with Symbolic Reasoning Engines,"
arXiv:2212.08195, 2022.

[4] J. Kim, J. Goh, I. Hwang, J. Cho, and J. Ok, "Bridging the Gap between
Expert and Language Models: Concept-guided Chess Commentary Generation and
Evaluation," *NAACL*, 2025. arXiv:2410.20811.

[5] S. A. Hebbar, P. Sheng, S. Oh, and P. Viswanath, "Hallucinations on the
Board: Tool-Augmented Evaluation of LLM Chess Commentary,"
arXiv:2608.04240, 2026.

[6] J. Rao et al., "MatchTime: Towards Automatic Soccer Game Commentary
Generation," arXiv:2406.18530, 2024.

[7] "AI-Generated Game Commentary: A Survey," arXiv:2506.17294, 2025.

[8] Lichess, "Accuracy metric." https://lichess.org/page/accuracy

[9] Lichess open database and puzzle database (CC0).
https://database.lichess.org/

[10] R. Barzilay and M. Lapata, "Collective Content Selection for
Concept-to-Text Generation," *HLT/EMNLP*, 2005.

---

## Figures to prepare

| # | Figure | Source |
|---|---|---|
| 1 | Pipeline diagram (six stages, shared vs chess-specific shaded) | Section III |
| 2 | Win-probability trace with selected beats marked | any analysed game |
| 3 | Motif recall bar chart, sorted | Section VI-A |
| 4 | Pacing comparison, 2.1× and 5.1× | Section VI-C |
| 5 | Hallucination before/after gate | Section VI-D |
| 6 | Studio screenshot: review screen, verified banner | UI |
| 7 | Studio screenshot: flagged beat with findings | UI |

Sample commentary for Figures/Appendix: `sample_opera_game.json`,
`sample_lichess_blitz.json`.
