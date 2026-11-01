# Voxplay: Pacing and Verifiable Grounding for Spoken Chess Commentary Generated from Move Notation

> **DRAFT.** Author block, guide, department and venue are placeholders — see
> `[TODO]` markers. Section VI reports a pre-registered human evaluation that
> **has not been conducted**; it is written as a protocol and must not be
> presented as a result. Every other number is reproducible from the scripts
> in `docs/paper/studies/` at the commit containing this file.

**Authors:** `[TODO: four names + registration numbers]`
**Guide:** `[TODO: name, designation]`
**Department:** `[TODO: department, institution]`

---

## Abstract

Millions of chess games are played online each day and almost none are ever
explained. Engine analysis yields a table of labels; human commentary exists
only for elite play. Prior work emits one comment per move as text, and
measures factual accuracy after generation rather than constraining it. We
present Voxplay, which turns move notation alone into spoken commentary
synchronised to an animated board. First, commentary is *paced*: beat
selection is budgeted maximum-weight selection under separation constraints,
solved greedily within 1.3% of an exact dynamic program. Over 106 Lichess
games the paced output is 3.5× shorter than move-by-move narration and covers
the opening, the decisive move and the finish in every game, against 9% for
an unconstrained top-*N* baseline.
Second, the model never derives a chess fact; facts come from
deterministic components validated against external labels — mean 91.9%
recall and 89.9% precision over 14,000 human-tagged puzzles, and Cohen's
κ = 0.939 against Lichess annotations on a held-out split of 12,337 moves.
Third, every sentence is checked against the facts licensing it; the gate
raised no false positive over 412 grounded beats. Generation costs about
US$0.06 per game. A pre-registered listener study and a larger hallucination
evaluation are specified but not run.

**Keywords** — automatic sports commentary, data-to-text generation, content
selection, grounded generation, hallucination mitigation, text-to-speech,
chess.

---

## I. Introduction

A chess game is a complete and unambiguous record of events. Every move is
known, every position is computable, and a strong engine can quantify the
consequence of each decision. Despite this, the artefact available to an
ordinary player who has just finished a game is a table: a list of moves
tagged *blunder*, *mistake* or *inaccuracy* with numeric evaluations beside
them. The table states that a move was bad. It does not state what was
missed, which of eighty moves decided the outcome, or why. A table also
cannot be listened to.

Human commentary supplies all of that and is economically restricted to elite
play, requiring an expert working for hours per game. Between the engine
report and the broadcast there is nothing. This paper describes a system
occupying that gap. The input is the move list — the record a player would
write on a scoresheet — and the output is spoken audio with a board that
follows the narration. No video is processed and no vision model is involved.

Chess admits this treatment because its facts are computable and checkable:
whether a move was a blunder, whether a fork exists, whether a player left
theory, how much winning probability a decision cost. This permits a pipeline
in which the language model cannot invent a domain fact, because it is never
asked to produce one — and is equally why the approach generalises less
readily to domains whose ground truth must itself be inferred.

### A. Contributions

1. **Pacing as budgeted selection.** We formalise *what to say* as
   maximum-weight selection under a spoken-duration budget and minimum
   separation, give the objective and constraints explicitly, name the
   solver, and bound its suboptimality against an exact dynamic program.
   Two baselines are implemented in the same codebase, so the contribution
   is isolated rather than asserted.

2. **Grounding by construction.** All chess facts are computed by
   independently validated deterministic components and supplied as a closed
   fact block, reducing the model's task to realisation.

3. **A verification gate with a symmetric licence.** Every sentence is
   checked against the claims its facts support, derived from the same
   function that builds those facts, so the two cannot drift apart without a
   test failing. We report the gate's false-positive rate, not only its
   catch rate.

4. **Speech with a measured timeline.** Audio offsets are measured from the
   rendered waveform rather than estimated from word counts, which is what
   makes board synchronisation correct rather than approximate.

---

## II. Related Work

### A. Chess commentary generation

Jhamtani et al. [1] introduced the task with a dataset mined from forum
discussion, training models to produce a comment per move. Zang et al. [2]
coupled generation to a neural engine so commentary could reference
evaluation. Lee et al. [3] combined language models with symbolic reasoning
engines to improve factual grounding. Kim et al. [4] introduced
concept-guided generation with a dedicated metric, prioritising which chess
concepts a comment should express. All four generate text, and all four
operate per move. Concept prioritisation [4] selects what to say *about a
move*; none selects which moves to speak about at all, which is what a
listener experiences as pacing.

### B. Factuality in commentary

Hebbar et al. [5] measured hallucination directly, reporting that GPT-5.4
without tool support produced incorrect sub-claims 22.0% of the time, with
smaller open-weight models exceeding 40%. Their contribution is
tool-augmented *evaluation* — measurement after generation. The present work
addresses the complementary half: constraining what may be asserted during
generation, and refusing to speak a sentence that fails. Ji et al. [6] survey
hallucination in natural language generation and distinguish intrinsic from
extrinsic error; the architecture here targets extrinsic error specifically,
by making the admissible fact set finite and explicit.

### C. Content selection in data-to-text

The selection problem predates the commentary literature. Barzilay and
Lapata [7] framed collective content selection as a constrained decision over
candidate records rather than independent per-record choices. Wiseman et
al. [8] showed on RotoWire that fluent generation from structured records
does not imply faithful or well-selected content, and introduced extractive
metrics to separate the two. Puduppully et al. [9] made selection and
ordering explicit as a content plan preceding realisation. Our formulation is
in that tradition; the addition is a *duration* budget, a constraint that
becomes meaningful only once output is spoken rather than read.

### D. Commentary in other sports

MatchTime [10] generates soccer commentary from video, and SoccerNet-Caption
[11] poses dense captioning of broadcasts with timestamped commentaries. Both
take video, where ground truth is inferred rather than computed. A survey of
AI-generated game commentary [12] confirms the pattern: dense per-event text,
with factuality treated as a post-hoc metric.

### E. Positioning

| Work | Selection / pacing | Output | Factuality |
|---|---|---|---|
| Jhamtani et al. [1] | none — every move | text | human rating |
| Zang et al. [2] | none — every move | text | automatic + human |
| Lee et al. [3] | none — every move | text | human preference |
| Kim et al. [4] | concept prioritisation | text | concept metric |
| Hebbar et al. [5] | evaluation only | — | tool-augmented eval |
| **Voxplay** | **budgeted beat selection** | **speech + synced board** | **licensed + gated** |

That a language model can write chess commentary is established. Our claims
are pacing, spoken delivery, and grounding enforced by construction.

---

## III. System Architecture

The pipeline has six stages: ingest notation into structured move events;
evaluate every position with Stockfish; detect tactical motifs and opening
deviation; select and pace beats; realise the selected facts as prose; and
verify, then speak with a measured timeline. Figure 1 shows the arrangement.

The architectural invariant is the boundary between fact computation and
generation. Everything the narration may assert is computed before the model
is invoked. The dependency graph is acyclic and the data model imports
nothing internal, which is what allows beat selection to be tested without an
engine and verification to be tested without an API key.

The implementation is approximately 11,600 lines of Python with 551 automated
tests, using python-chess for board logic, Stockfish as the analysis engine,
and `claude-opus-5` for realisation. Three speech backends sit behind a single
interface. Source, exact dependency versions and every study script in
Section V are in the repository.

---

## IV. Method

### A. Evaluation in win probability

Centipawn differences are not comparable across positions: losing 100
centipawns from +900 is irrelevant, whereas losing 100 from an equal position
can decide the game. Classifying on raw centipawns therefore over-reports
blunders in decided positions and under-reports them in sharp equal ones.

Every evaluation is mapped to win probability using the logistic published by
Lichess [13], with centipawns *c* clamped to ±1000 and λ = 0.00368208:

> (1)  W(c) = 50 + 50 ( 2 / (1 + e^(-λc)) - 1 )

The mapping saturates at the extremes, so the same 100-centipawn delta is
worth approximately 12 percentage points near equality and under one point at
+900. Every downstream component — classification, salience, pacing — reads
*W*; centipawns do not propagate past this stage. The constant is adopted
from Lichess so that our classifications are directly comparable with
Lichess's own annotations, which Section V-B exploits.

Engine analysis is budgeted by **nodes**, not depth or wall-clock time.
Time-limited search returns different results on different machines and under
different load, which would make every number here irreproducible; a node
budget is deterministic for a fixed engine build. All reported analysis uses
1,000,000 nodes per position, `MultiPV` 3, and a **single search thread** —
see Section VII-A for why the thread count is not a free parameter. Positions
rather than moves are evaluated: the position after move *i* is the position
before move *i*+1, so an *N*-move game requires *N*+1 evaluations.

### B. Move classification

Let *L* be the win probability lost by a move, that is the mover's *W* before
the move minus their *W* after it. Labels follow thresholds θ = (5, 10, 15):

| Label | Condition |
|---|---|
| `best` | the engine's top move |
| `book` | top move, still within opening theory |
| `good` | *L* < 5 |
| `inaccuracy` | 5 ≤ *L* < 10 |
| `mistake` | 10 ≤ *L* < 15 |
| `blunder` | *L* ≥ 15 |

A commentary-specific variant caps severity at `inaccuracy` once the mover's
*W* exceeds 90, since describing a move as a blunder while the player remains
completely winning misdescribes the position. The cap is disabled for the
agreement experiment of Section V-B so that comparison remains clean.

**Status of these thresholds.** Community documentation describes the bands
as approximately 10/20/30 points, which is about twice too lenient (Section
V-B). Fitting θ to maximise agreement recovers 5/10/15 — exactly Lichess's
own documented criterion of a 0.1/0.2/0.3 winning-chances delta on a −1…1
scale, expressed on a 0–100 scale. The claim we make is correspondingly
modest: since the system adopts Lichess's win-probability formula and
recovers Lichess's thresholds, a high κ against Lichess annotations shows
that our implementation is **faithful**, not that a new classification method
has been found. Section V-B fits on one split and reports on another, so the
figure is not a measure of how well three parameters fit their own data.

### C. Tactical motif detection

Twelve motifs are detected from board geometry alone, with no engine and no
language model: fork, pin, skewer, discovered attack, hanging piece,
sacrifice, back-rank mate, smothered mate, double check, promotion, en
passant and advanced pawn. A model asked whether a position contains a fork
will answer confidently either way, and a listener who plays chess will
detect the error. Detector names match Lichess puzzle theme labels so that
validation is a direct comparison rather than a mapping exercise.

Geometry establishes that material was given up but not that a sacrifice was
*sound*; soundness is decided in the analysis layer, which has the
evaluation.

### D. Opening deviation by position index

Comparing a game's move sequence prefix by prefix against known openings
misses every transposition, reporting a game that reaches a known position by
a different move order as a novelty. We instead replay each line in the CC0
`lichess-org/chess-openings` database (approximately 3,500 openings) and index
the resulting **position**, so transpositions resolve correctly at the cost of
a one-time index build.

### E. Pacing as budgeted selection

Let *M* be the half-moves of a game. For each *m* ∈ *M* we compute six
normalised signals in [0, 1] — evaluation swing *s*, narrative role *n*,
strongest tactical motif *t*, criticality *k* (the gap between the best and
second-best move), material swing *a*, and think time *h* relative to that
player's own baseline — and combine them with weights **w**:

> (2)  σ(m) = w_s·s + w_n·n + w_t·t + w_k·k + w_a·a + w_h·h

with **w** = (1.00, 0.60, 0.45, 0.35, 0.25, 0.20) in the order written. Swing
saturates at the blunder threshold, so any blunder scores the maximum on that
term. Section V-D measures how much each weight changes the output.

Each beat *m* costs *c(m)* spoken words, fixed by beat type: 65 for the
turning point and 45 for an ordinary moment. Duration is words divided by a
measured speaking rate *r*. Writing *x_m* ∈ {0, 1} for the decision to speak
about *m*, and *P* ⊆ *M* for the candidates that clear a salience floor
σ(m) ≥ σ_min = 0.25 and are not already covered by a mandatory beat,
selection solves:

> (3)  maximise  Σ_{m∈P} σ(m)·x_m

> (4)  subject to  Σ_{m∈P} c(m)·x_m ≤ B

> (5)  x_i + x_j ≤ 1  whenever  |ply(i) − ply(j)| < g

The word budget *B* is the game's spoken budget less the words reserved for
the mandatory intro, opening, finish and outro beats, which supply the
coverage requirement directly rather than as a constraint. The separation
parameter *g* = 4 half-moves. The spoken budget scales with game length,
clamped at both ends:

> (6)  budget(M) = min( 360, max( 150, 2.0·|M| ) )  seconds

A fixed budget does not survive contact with real games: 120 s is generous
for a 17-move miniature and leaves a 68-move game with one beat every twenty
moves, at which point bridging lines claim that nothing happened across
stretches where plenty did.

**Solver, complexity and optimality.** Equations (3)–(5) are solved
**greedily**: candidates are sorted by σ descending and accepted if they fit
the remaining budget and violate no separation constraint. Sorting is
*O*(|M| log |M|) and the acceptance scan is *O*(|M|·|S|) for a selected set
*S* bounded by *B* / min *c*, so the selector is *O*(|M| log |M|) in
practice.

Greedy is not optimal in general. The problem is, however, exactly solvable.
With candidates in ply order, constraint (5) between *consecutive* chosen
candidates implies it for every pair, so (3)–(5) is a knapsack on a line and
admits an exact *O*(|P|·B) dynamic program over (candidate index, remaining
word budget), which is tractable because *c* is integral in words. We
implement that dynamic program solely as a reference and report the greedy
solution's measured optimality gap in Section V-D, rather than asserting that
the gap is small.

Without separation, the highest-scoring moves in a tactical flurry are all
adjacent and the script narrates four consecutive half-moves in detail and
then skips twenty — precisely the failure of the unconstrained top-*N*
baseline.

**Measured speaking rate.** Budgeting uses *r* = 1.85 written words per
second, which is measured rather than nominal. A 134-word script rendered at
a nominal 155 wpm produced 72.6 s of audio, an effective 111 wpm. The
discrepancy arises from clause-boundary pauses, numeral expansion ("98
percent" is three spoken words) and per-clip padding. Budgeting at the
nominal rate promises a two-minute recap and delivers nearly three.

### F. Script generation

One call establishes a through-line for the game; each beat is then realised
in a separate call receiving the outline, the lines already spoken, and a
**fact block** constituting the complete and only basis for that sentence. If
a fact is not in the block, the narrator may not state it.

Notation is converted to spoken form by rule, not by prompt. Asking a model
to read "Qxh7#" aloud succeeds most of the time and then produces "queen x h
seven hash" on the move that matters. The conversion is built from structured
fields rather than by parsing SAN, so there is no string to misparse.

For full-coverage sheets, beats are generated in runs of six per call and
parsed back by index, rejecting the batch entirely if the response shape is
wrong. A partially parsed batch would attach one move's commentary to a
different move — an error in which every individual claim remains true, and
which the verifier therefore cannot detect. Generation parameters are stated
in Section V-F and the prompts in Appendix A.

### G. The verification gate

Each sentence is checked against a **licence**: the set of claims the beat's
facts support, comprising permitted squares, permitted motifs, permitted
names, the position evaluation, and whether the result may be revealed. Seven
checks run — notation leakage into speech, ungrounded squares, ungrounded
tactics, evaluation contradiction, ungrounded names, cross-beat repetition
and length against the beat's budget. The first five are factual defects; the
last two are stylistic warnings and are excluded from all error rates below.

The licence is derived from the same function that builds the fact block.
This symmetry matters in both directions: a fact supplied but not licensed
produces a false positive, and a claim licensed but never supplied produces a
blind spot. A beat that fails is **regenerated** with the specific failure
fed back rather than shipped.

The same gate checks text a user has edited by hand, so an invented claim
typed by a person is caught identically (Figure 4).

### H. Speech and timeline

Audio offsets are measured from the rendered waveform, never estimated. Clips
produced in compressed formats are transcoded before any offset is taken, so
the rule that durations come from a WAV header holds regardless of backend —
the choice of voice cannot silently desynchronise the board.

Each clip is synthesised with its neighbouring lines supplied as unspoken
context, following the cross-utterance conditioning of Xu et al. [14].
Without it, a line generated in isolation closes as though the game ended
there and begins without preceding intonation, and a six-beat recap is heard
as six announcements rather than one person speaking.

---

## V. Experimental Validation

All component results are measured against externally produced labels this
project did not generate. The script producing each table is named with it;
all are in `docs/paper/studies/`.

### A. Motif detection: precision and recall

Detectors were scored against the Lichess puzzle database (CC0) [15]. For
each of the twelve detectors, 1,000 puzzles carrying that detector's own
theme were drawn by reservoir sampling over 6.5 million rows, together with
2,000 control puzzles drawn from five themes that are not motifs at all
(`mateIn2`, `endgame`, `middlegame`, `crushing`, `advantage`) and carrying no
theme under test — 14,000 puzzles in total. A detector is credited if it
fires anywhere among the solver's moves in the solution line, since a Lichess
theme describes the whole solution rather than its first move.

Precision is reported at this sampling ratio of 1,000 positives to 2,000
negatives, which is not the prevalence of motifs in running commentary, and
is a **lower bound** for a second reason: Lichess tags are not exhaustive, so
a control firing may be an untagged true instance rather than an error. That
caveat is measurable. On a control sample of the same size the
back-rank-mate detector fired on 232 puzzles, and replaying each line to its
end shows **all 232 do finish in checkmate on a back rank**. Its apparent
precision of 81.2% is an artefact of tagging, not a defect.

| Detector | n | Recall | Precision | F1 |
|---|---|---|---|---|
| double check | 1000 | 100.0% | 100.0% | 1.000 |
| smothered mate | 1000 | 100.0% | 100.0% | 1.000 |
| en passant | 1000 | 100.0% | 100.0% | 1.000 |
| promotion | 1000 | 100.0% | 99.7% | 0.999 |
| discovered attack | 1000 | 94.0% | 91.0% | 0.925 |
| fork | 1000 | 100.0% | 82.4% | 0.904 |
| back-rank mate | 1000 | 100.0% | 81.2%* | 0.896 |
| skewer | 1000 | 96.8% | 82.2% | 0.889 |
| advanced pawn | 1000 | 85.2% | 88.9% | 0.870 |
| sacrifice | 1000 | 86.1% | 86.7% | 0.864 |
| pin | 1000 | 49.2% | 77.1% | 0.601 |
| **Mean, 11 comparable** | | **91.9%** | **89.9%** | **0.904** |
| hanging piece, excluded | 1000 | 26.1% | 33.2% | 0.292 |

*All 232 control firings verified as genuine back-rank mates.
Source: `motif_precision.py`, `control_firings.py`.

The hanging-piece detector is excluded from the mean because the theme does
not ask what the detector answers: Lichess tags a puzzle `hangingPiece` when
the solution *takes* an undefended piece, whereas we detect a move that
*leaves* one hanging. Detecting captures instead was implemented and
reverted — it reached full recall but fired on 56.9% of controls and labelled
the acceptance of a famous queen sacrifice as a piece left undefended, since
separating a won piece from a recapture needs move history that a puzzle FEN
does not carry.

The pin detector is the one genuine weakness, and trades recall for
precision: 49.2% with its filter against 63.5% without, because the
unfiltered version produced 17 "pins" on a single short game and drowned the
two that mattered.

### B. Classification agreement, fitted and tested on disjoint splits

Lichess publishes both halves of what this comparison needs: `[%eval]` on
every move of an analysed game, and its own verdict as a NAG. The corpus is
339 analysed games, 24,255 moves. Games — not moves — were shuffled under a
fixed seed and split in half. θ was grid-searched on the training half alone
over all integer triples in [2, 30], and κ is reported on the held-out half.

| | Train, 169 games | Held out, 170 games |
|---|---|---|
| Moves | 11,918 | 12,337 |
| θ fitted on train | 5 / 10 / 15 | — |
| κ, fitted θ | 0.926 | **0.939** |
| κ, documented θ = 10/20/30 | — | 0.363 |
| Raw agreement | — | 98.1% |
| Agreement expected by chance | — | 68.8% |

Source: `threshold_split.py`.

Held-out κ is marginally *higher* than training κ, so there is no measurable
overfitting — unsurprising, since the search recovers a published constant
rather than discovering a free parameter. Per-class agreement on held-out
data: inaccuracy 92.0%, mistake 91.8%, blunder 97.3%. We report κ rather than
raw agreement because the class distribution is heavily skewed toward
non-errors and raw agreement would flatter the result. As stated in Section
IV-B, this establishes implementation fidelity, not a new method; the
substantive finding is that the widely circulated 10/20/30 bands are
approximately twice too lenient and yield κ = 0.363 on held-out data.

### C. Pacing over 120 games

Two games cannot show a distribution. The selector was run over 120 Lichess
games of 20–200 half-moves, each analysed at 1,000,000 nodes per position
single-threaded, against both implemented baselines. Fourteen runs were lost
to engine processes failing under concurrent load — a property of the
harness rather than of the games — leaving **106 games and 7,609 half-moves**,
with a median game length of 65 half-moves (IQR 49–85, range 27–196).

| Spoken duration | Paced | Top-*N* | Every move |
|---|---|---|---|
| Median | **143.2 s** | 194.6 s | 491.9 s |
| IQR | 142.2–166.5 | constant | 370.8–643.2 |
| Range | 131.4–348.6 | 194.6 | 204.3–1483.2 |
| Median beats | 7 | 8 | — |

Median reduction against every-move narration is **3.5×** (IQR 2.6–3.9,
range 1.5–4.2). Duration alone, however, is the weaker half of the result.

| | Paced | Top-*N* |
|---|---|---|
| Opening covered | **100%** | 36.8% |
| Decisive move covered | 99.1% | 99.1% |
| Finish covered | **100%** | 9.4% |
| Games with no adjacent pick | **106/106** | 0/106 |
| Mean adjacent picks per game | **0.00** | 3.98 |

Source: `pacing_study.py`.

Two comparisons, which must not be conflated.

**Against every-move narration** the advantage is duration, and it widens
with game length because the paced budget is clamped by (6) and the
baseline's is not.

**Against top-*N*** the advantage is not duration. Top-*N* emits exactly
*N* = 8 beats of fixed cost, so its duration is **constant at 194.6 s by
construction**, identical on every game in the corpus and independent of game
length. We state this explicitly because an identical figure across games
invites the suspicion of a measurement error, and it is instead a property of
the baseline.

The structural comparison is the informative one. Top-*N* locates the
decisive move exactly as often as the paced selector — 99.1% in both arms —
so ranking by swing is not what it lacks. What it lacks is everything the
constraints supply: it names the opening in barely a third of games, reaches
the finish in under one in ten, and in *every* game spends beats on
half-moves adjacent to another selection, averaging four such picks per game
against none. This is the evidence that the constraints rather than the
scoring carry the contribution, and the reason both baselines exist rather
than one.

### D. Greedy optimality and weight sensitivity

Equations (3)–(5) are solved greedily, and Section IV-E claimed only that the
exact solution is available for comparison. Running both over the same 106
games gives the gap directly, as the ratio of the greedy selection's total
salience to the dynamic program's optimum:

| Greedy ÷ exact optimum | |
|---|---|
| Mean | **0.987** |
| Minimum over 106 games | 0.743 |
| Exactly optimal | 81 / 106 games (76%) |

Greedy is therefore optimal on roughly three games in four and within 1.3% of
the optimum on average, with a worst observed case of 74%. The gap arises
where a high-scoring candidate blocks two slightly lower-scoring ones that
together score more — the standard failure of greedy knapsack. Whether
closing it is worth an exact solve is a question about listeners rather than
about the objective, since the objective is itself a proxy; we report the gap
and keep the greedy solver, whose cost is negligible beside the engine's.

The six weights of (2) are hand-set, which is a fair objection. The test is
whether each one changes the output. Over 60 games the selector was run at
the shipped weights and again with each signal zeroed in turn. *Retained* is
the share of the shipped selection the variant still picks; *value* is the
variant's total salience scored under the **shipped** weights as a fraction
of the shipped selection's, so a variant that picks worse moves scores below
1.0 on the objective the system is actually maximising; *decisive beat* is
the share of games in which the turning point is selected as a beat of its
own, which is a stricter test than the coverage column of Section V-C and
stands at 76.7% for the shipped weights.

| Signal removed | Retained | Jaccard | Value | Decisive beat |
|---|---|---|---|---|
| swing | **48.9%** | **0.382** | **0.787** | 71.7% |
| narrative | 82.8% | 0.776 | 0.973 | **51.7%** |
| motif | 82.7% | 0.794 | 0.953 | 76.7% |
| criticality | 86.4% | 0.816 | 0.985 | 76.7% |
| material | 91.4% | 0.882 | 0.984 | 76.7% |
| think time | 100.0% | 1.000 | 1.000 | 76.7% |

Source: `weight_ablation.py`.

Three readings. **Swing carries the selection**: removing it retains under
half the picks and costs a fifth of the objective, which is the intended
design and is now measured rather than asserted. **Narrative weight does a
narrow but irreplaceable job**: dropping it changes few picks and costs
almost no salience, yet the decisive move stops being given a beat of its own
in a quarter of games — exactly the behaviour it exists to produce, and
invisible to the objective. **Think time is inert on this corpus**, not
unimportant: the games were exported without clocks, so the signal is
identically zero and its weight is untested here rather than shown to be
unnecessary. A corpus with clocks is needed to evaluate it, and we do not
claim the weight is justified.

### E. The verification gate

Two quantities matter and only one has previously been reported. The gate's
**false-positive rate** is measurable without a language model: the template
generator assembles sentences mechanically from the same fact block the gate
licences, so every claim it makes is true by construction, and any
error-severity finding against its output is a false positive.

Over 412 beats from 60 games the gate raised **no false positive at all**
(exact 95% upper bound 0.89 per 100 beats), and every one of the 60 games was
entirely clean. The 64 findings raised were all `phrase_repetition` warnings,
which template prose incurs by design and which are excluded from the error
rate. Source: `gate_false_positives.py`.

The gate's **catch rate** on model output is at present a pilot, and we
report it as such. Over 32 generated beats from two games, disabling the gate
yielded 2 unsupported claims (6.2 per 100 beats) and enabling it yielded 0,
at an 8% increase in generation cost. Exact Clopper–Pearson 95% intervals are
[0.8%, 20.8%] and [0.0%, 10.9%] respectively. **Those intervals overlap, so
this pilot does not by itself establish that the gate reduces the rate.**
Reaching a 95% upper bound below 2% requires 183 consecutive clean beats, and
below 1% requires 368.

Labelling provenance must also be stated plainly: the two errors in the pilot
were identified by the author, with the checker's own output as the starting
point. That is circular — the same component both detects and counts the
errors — and is why the figure is presented as a pilot and why Section VI-C
pre-registers two independent annotators and inter-annotator agreement.

For context, the ungated rate of 6.2 per 100 beats is far below the 22.0%
reported by Hebbar et al. [5] for an untooled model, as expected when facts
are supplied rather than requested. The comparison is indicative only, the
two studies using different corpora and different units of claim.

### F. Cost, latency and generation parameters

Measured end to end at 1,000,000 nodes per position single-threaded, model
`claude-opus-5`, no temperature set (API default), reasoning effort `low` for
beat realisation and `medium` for the outline, one run per game:

| | Opera Game, 33 plies | Blitz game, 79 plies |
|---|---|---|
| Beats selected | 6 | 7 |
| Spoken duration | 142.2 s | 154.1 s |
| Every-move baseline | 249.7 s | 597.8 s |
| Engine analysis | 29.6 s | 66.6 s |
| Script generation | 22.4 s | 29.5 s |
| API calls | 7 | 8 |
| **Cost** | **$0.0572** | **$0.0631** |
| Verification | clean | clean |

Generation cost and latency are approximately constant in game length,
because the number of beats is bounded by the duration budget rather than by
the number of moves — a direct consequence of pacing. Engine analysis scales
linearly at approximately 1.43 s per position single-threaded and dominates
runtime on longer games.

Results depending on a single proprietary model are a weakness. Reproducing
Section V-E with an open-weight model is specified in Section VI-C.

---

## VI. Planned Human Evaluation

**This study has not been conducted.** Its design, hypotheses, thresholds,
sample size and analysis plan were committed to the repository before any data
collection and are reported here as a pre-registration. No human ratings are
claimed anywhere in this paper. The component results of Section V establish
that the commentary is *correct*; they cannot establish that it is *worth
listening to*, which is a claim about human experience and the central claim
of this work.

### A. Conditions

Five arms, rendered identically so participants cannot distinguish them by
format: **(A)** flat, one line per half-move; **(B)** top-*N* by swing;
**(C)** the system; **(D)** the system's pacing with sentences assembled
mechanically from the same facts; **(E)** a recruited club player (1800+)
given the same PGN and analysis but no system output. A and B isolate pacing;
D isolates the language model, differing from C only in that nothing wrote
the prose; E is the ceiling.

### B. Hypotheses and design

| | Hypothesis | Supported if |
|---|---|---|
| H1 | Pacing improves engagement | C > A on engagement, median Δ ≥ 1.0, *p* < 0.05 |
| H2 | Constraints matter, not only scoring | C > B on pacing, median Δ ≥ 0.5 |
| H3 | Near the human ceiling | C within 0.5 Likert points of E on informativeness |
| H4 | The model earns its cost | C > D on engagement, median Δ ≥ 0.5 |
| H5 | Grounding holds up to listeners | C ≥ E on perceived accuracy |

H3 is deliberately a non-inferiority claim; "as informative as human
commentary" is not testable as stated, having neither a threshold nor a
matched corpus.

Within-subjects, *n* = 30 (minimum 24), stratified 10/10/10 across beginner,
casual and club-strength listeners. Each participant hears four clips, each a
different game in a different condition, assigned by Latin square so that
condition does not confound with order or game difficulty, and rates five
5-point Likert items per clip — informativeness, engagement, perceived
accuracy, pacing, naturalness — worded as statements about their own
experience and never mentioning automation. Analysis: Friedman across
conditions per item, Wilcoxon signed-rank for the pre-specified pairs,
Holm–Bonferroni correction, with median differences and bootstrap 95%
intervals beside every *p*-value. If C does not exceed A on engagement the
pacing hypothesis is not supported, and that is the finding.

### C. Pre-registered hallucination evaluation

To replace the pilot of Section V-E: at least 20 games spanning the
archetype distribution of Section V-C, generated twice each with the gate
disabled and enabled, yielding not fewer than 400 beats per arm. Every beat
is segmented into atomic claims. Two annotators, at least one club-strength,
label each claim independently as supported, unsupported or unverifiable,
working from the board and the engine output and **not** from the checker's
findings; inter-annotator agreement is reported as Cohen's κ before any rate
is, and a third annotator adjudicates disagreements. Reported per arm:
unsupported claims per 100 beats with exact binomial intervals, and for the
gated arm the regeneration rate, the share of regenerations that still
failed, and the gate's false-positive rate against the annotators' labels
rather than against template output. The same protocol is applied to one
open-weight model, to establish that the architecture rather than the
specific model carries the result.

---

## VII. Limitations

**A. Reproducibility constrains engine configuration.** Stockfish's Lazy SMP
shares a transposition table between racing threads, so a fixed node budget
does not determine the result above one thread: two four-thread runs of the
same game disagreed on 27 of 33 half-moves, enough to relocate the detected
turning point. All reported analysis is single-threaded, at approximately
1.43 s per position.

**B. Motif detection is rule-based.** Twelve well-defined patterns.
Deflection, overloading and zugzwang are not detected, and the pin detector
recovers under half of tagged pins (Section V-A).

**C. Two move-quality labels are unimplemented.** `brilliant` and `great`
exist in the data model and are never assigned; they require combined
sacrifice-soundness and only-move detection. The classifier produces six
labels.

**D. The hallucination result is a pilot.** Section V-E reports 32 beats with
overlapping confidence intervals and non-independent labelling. The gate's
false-positive rate is established on grounded text; its catch rate on model
output is not yet established at usable precision.

**E. Delivery is shaped more coarsely than the text, and metered.** Clips are
separated by a fixed gap regardless of content, so a commentator's rhythm —
pausing longer before a decisive move — is only partially reproduced. The
highest-quality backend also bills per character, so near-zero per-game cost
and the best available delivery cannot be had at once.

**F. English only.** The notation-to-speech converter is a rule-based
*English* grammar, the prompts are English, and the verifier's vocabulary is
English. Translating output after verification would void the gate's
guarantee, since the translated string has not been checked.

**G. The think-time weight is untested.** The evaluation corpus was exported
without clocks, so that signal is identically zero throughout Section V-D and
nothing here justifies its weight.

**H. Human evaluation is pending**, as stated in Section VI.

**I. Several reported quantities reached their final values only after an
initial measurement was found to be wrong**, in every case located by
inspecting individual cases rather than aggregates. Appendix B records each.

---

## VIII. Future Work

**Live commentary** is not a small change. Every per-position component
functions unchanged on streamed input, but pacing does not: budget allocation
needs the game's length, the archetype cannot be determined before the game
ends, and the turning point is by definition the largest error *of the game*.
A naive live system would reproduce the every-move baseline in real time.
Either a fixed delay of several moves or pacing within fixed segments
preserves part of the contribution, and both make explicit that pacing is
fundamentally a lookahead problem.

**Rating-conditioned thresholds**, since a +2.00 position is less decided
between 1200-rated players than between masters and λ in (1) is global;
**multilingual output**, requiring a spoken-form grammar and verifier
vocabulary per language; and **learned salience weights** fitted to listener
preference data once the study of Section VI provides it, replacing the
hand-set **w** of (2).

---

## IX. Conclusion

We have described a system converting chess move notation into spoken
commentary with a synchronised board, without video. Its contributions are
pacing — formalised as budgeted selection in (3)–(5), solved greedily within
1.3% of an exact dynamic program, and measured over 106 games against two
implemented baselines — grounding by construction, and a verification gate
whose licence is derived symmetrically from the facts it checks.

Validation against external labels gives mean 91.9% recall and 89.9%
precision over 14,000 human-tagged puzzles, and κ = 0.939 on a held-out split
of 12,337 annotated moves. The gate raised no false positive over 412
grounded beats; its effect on model output is a pilot with wide intervals,
and we report the protocol that would settle it rather than the conclusion we
would prefer. The evaluation that remains is the one only listeners can
provide, and its protocol is fixed in advance.

---

## References

[1] H. Jhamtani, V. Gangal, E. Hovy, G. Neubig, and T. Berg-Kirkpatrick,
"Learning to Generate Move-by-Move Commentary for Chess Games from
Large-Scale Social Forum Data," in *Proc. ACL*, 2018, pp. 1661–1671.

[2] H. Zang, Z. Yu, and X. Wan, "Automated Chess Commentator Powered by
Neural Chess Engine," in *Proc. ACL*, 2019, pp. 5952–5961.

[3] A. Lee, D. Wu, E. Dinan, and M. Lewis, "Improving Chess Commentaries by
Combining Language Models with Symbolic Reasoning Engines," arXiv:2212.08195,
2022.

[4] J. Kim, J. Goh, I. Hwang, J. Cho, and J. Ok, "Bridging the Gap between
Expert and Language Models: Concept-guided Chess Commentary Generation and
Evaluation," in *Proc. NAACL*, 2025. arXiv:2410.20811.

[5] S. A. Hebbar, P. Sheng, S. Oh, and P. Viswanath, "Hallucinations on the
Board: Tool-Augmented Evaluation of LLM Chess Commentary," arXiv:2608.04240,
2026.

[6] Z. Ji, N. Lee, R. Frieske, T. Yu, D. Su, Y. Xu, E. Ishii, Y. Bang,
A. Madotto, and P. Fung, "Survey of Hallucination in Natural Language
Generation," *ACM Computing Surveys*, vol. 55, no. 12, pp. 1–38, 2023.

[7] R. Barzilay and M. Lapata, "Collective Content Selection for
Concept-to-Text Generation," in *Proc. HLT/EMNLP*, 2005, pp. 331–338.

[8] S. Wiseman, S. M. Shieber, and A. M. Rush, "Challenges in
Data-to-Document Generation," in *Proc. EMNLP*, 2017, pp. 2253–2263.

[9] R. Puduppully, L. Dong, and M. Lapata, "Data-to-Text Generation with
Content Selection and Planning," in *Proc. AAAI*, vol. 33, 2019,
pp. 6908–6915.

[10] J. Rao, H. Wu, C. Liu, Y. Wang, and W. Xie, "MatchTime: Towards
Automatic Soccer Game Commentary Generation," arXiv:2406.18530, 2024.

[11] H. Mkhallati, A. Cioppa, S. Giancola, B. Ghanem, and
M. Van Droogenbroeck, "SoccerNet-Caption: Dense Video Captioning for Soccer
Broadcasts Commentaries," in *Proc. CVPR Workshops*, 2023, pp. 5074–5085.

[12] Q. Zheng, X. Wang, K. Cheng, Y. Lu, M. A. Ali, L. Li, Y. Wang, and
W. Li, "From Multimodal Perception to Strategic Reasoning: A Survey on
AI-Generated Game Commentary," arXiv:2506.17294, 2025.

[13] Lichess, "Accuracy metric." https://lichess.org/page/accuracy

[14] G. Xu, W. Song, Z. Zhang, C. Zhang, X. He, and B. Zhou, "Improving
Prosody Modelling with Cross-Utterance BERT Embeddings for End-to-End Speech
Synthesis," in *Proc. ICASSP*, 2021, pp. 6079–6083.

[15] Lichess open database and puzzle database (CC0).
https://database.lichess.org/

---

## Appendix A. Prompts and a worked fact block

Four prompts are sent: an outline prompt, a per-beat realisation prompt, a
batched variant for full-coverage sheets, and a repair prompt carrying the
verifier's finding. All four are reproduced verbatim in
`docs/paper/prompts.md` in the repository. The system prompt's binding
instruction is that every chess fact stated must appear in the FACTS block,
that notation must never be written in any form, and that no numeric
evaluation may be stated.

The fact block is the complete and only factual basis for a beat. For the
opening beat of the Opera Game it reads, as sent:

```
THIS BEAT: opening

FACTS
- opening played: Philidor Defense
- ECO code: C41
- moves: 1. pawn to e four; 1... pawn to e five;
  2. knight to f three; 2... pawn to d six;
  3. pawn to d four; 3... bishop to g four;
  4. pawn takes the pawn on e five;
  4... bishop takes the knight on f three
- target length: about 40 words
```

Notation has already been converted to spoken form by rule before the model
sees it, so there is no SAN string for the model to read aloud incorrectly.
The licence the verifier builds for this beat is derived from the same
function that produced these lines, and permits exactly the squares, names
and claims they contain.

## Appendix B. Corrections

Each quantity below was reported at an earlier value and corrected after
inspecting individual cases rather than aggregates.

| Quantity | First | Corrected | Cause |
|---|---|---|---|
| Motif recall | 46.5% | 91.9% | validator scored only each puzzle's first move; Lichess themes describe the whole line |
| Thresholds | 10/20/30 | 5/10/15 | taken from community documentation, not measured; agreed with none of 2,133 tagged inaccuracies |
| Hallucination | 18.8 | 6.2 per 100 beats | checker mishandled apostrophes and British spellings |
| Every-move arm | 594 s | 249.7 s | gave each move a full 45-word treatment, which per-move commentary is not |
| Paced duration | 117.8 s on both games | 142.2 / 154.1 s | measurement used a fixed 120 s budget, not the scaled budget of (6) |
| Speaking rate | 155 wpm | 111 wpm | clause pauses, numeral expansion, per-clip padding |

Two of the six were found because a number was *identical* where it had no
reason to be, which is the most reliable smell in this project's history.
