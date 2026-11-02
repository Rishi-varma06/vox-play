# Voxplay: Pacing and Verifiable Grounding for Spoken Chess Commentary Generated from Move Notation

> **DRAFT.** Only the three lines below are unfilled; replace the bracketed
> text in each and the paper is submission-ready. Section VI reports a
> pre-registered human evaluation that **has not been conducted**; it is
> written as a protocol and must not be presented as a result. Every other
> number is produced by a script in `docs/paper/studies/` at the commit
> containing this file. For a double-blind venue, build with
> `python docs/paper/build_pdf.py --blind`, which replaces the author block
> and removes every pointer that would identify the group.

**Authors:** First Author, Second Author, Third Author, Fourth Author
**Guide:** Guide Name, Designation
**Department:** Department, Institution

---

## Abstract

Millions of chess games are played online daily and almost none are
explained. Engine analysis yields a table of labels; human commentary exists
only for elite play. Prior work emits one comment per move as text and
measures factual accuracy after generation rather than constraining it.
Voxplay turns move notation alone into spoken commentary synchronised to an
animated board. First, commentary is *paced*: beat selection is budgeted
maximum-weight selection under separation constraints, solved greedily within
1.3% of optimal. Over 173 standard Lichess games it runs 3.6× shorter than
move-by-move narration and covers the opening and finish in every game,
against 23% and 8% for an unconstrained top-*N* baseline. Second, the model
never derives a chess fact; facts come from deterministic components
validated externally — 91.9% mean recall and 89.9% precision over 14,000
human-tagged puzzles, and κ = 0.955 against Lichess annotations on a held-out
6,864 moves. Third, every sentence is checked against the facts licensing it,
and the check is auditable: run over grounded text where any finding is by
construction a false positive, it exposed a licence defect we then fixed,
leaving 0.66 per 100 beats. Generation costs about US$0.06 per game. A
pre-registered listener study and a larger hallucination evaluation remain to
be run.

**Keywords** — automatic sports commentary, data-to-text generation, content
selection, grounded generation, hallucination mitigation, chess.

---

## I. Introduction

A chess game is a complete and unambiguous record of events: every move is
known, every position computable, and a strong engine can quantify the
consequence of each decision. Despite this, what an ordinary player gets
after a game is a table — moves tagged *blunder*, *mistake* or *inaccuracy*
with numeric evaluations beside them. It states that a move was bad. It does
not state what was missed, which of eighty moves decided the outcome, or why.
A table also cannot be listened to.

Human commentary supplies all of that, and is economically restricted to
elite play: it requires an expert working for hours per game. Between the
engine report and the broadcast there is nothing. This paper describes a
system occupying that gap. The input is the move list — the record a player
would write on a scoresheet — and the output is spoken audio with a board
that follows the narration. No video and no vision model is involved.

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

Table: Positioning against prior chess-commentary systems
| Work | Selection / pacing | Output | Factuality |
|---|---|---|---|
| [1], [2], [3] | none — every move | text | human or automatic rating |
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
generation: everything the narration may assert is computed before the model
is invoked. The dependency graph is acyclic and the data model imports
nothing internal, which is what allows beat selection to be tested without an
engine and verification without an API key.

The implementation is roughly 11,600 lines of Python with 551 automated
tests, using python-chess for board logic, Stockfish for analysis and
`claude-opus-5` for realisation, with three speech backends behind one
interface. Source, exact versions and every study script are in the
repository.

---

## IV. Method

### A. Evaluation in win probability

Centipawn differences are not comparable across positions: losing 100
centipawns from +900 is irrelevant, whereas losing 100 from equality can
decide the game, so classifying on raw centipawns over-reports blunders in
decided positions and under-reports them in sharp equal ones.

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

A move is `best` if it is the engine's top choice, `book` if it is also still
within opening theory, and otherwise `good`, `inaccuracy`, `mistake` or
`blunder` as *L* falls below 5, in [5, 10), in [10, 15) or at or above 15.

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
*sound*; soundness is decided in the analysis layer, which has the evaluation.

### D. Opening deviation by position index

Comparing a game's move sequence prefix by prefix against known openings
misses every transposition, reporting a game that reaches a known position by
a different move order as a novelty. We instead replay each line in the CC0
`lichess-org/chess-openings` database (~3,500 openings) and index the
resulting **position**, so transpositions resolve at the cost of a one-time
index build.

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

Each beat *m* costs *c(m)* spoken **words**, fixed by beat type: 65 for the
turning point and 45 for an ordinary moment. Writing *x_m* ∈ {0, 1} for the
decision to speak about *m*, and *P* ⊆ *M* for the candidates that clear a
salience floor σ(m) ≥ σ_min = 0.25 and are not already covered by a mandatory
beat, selection solves:

> (3)  maximise  Σ_{m∈P} σ(m)·x_m

> (4)  subject to  Σ_{m∈P} c(m)·x_m ≤ B

> (5)  x_i + x_j ≤ 1  whenever  |ply(i) − ply(j)| < g

with separation parameter *g* = 4 half-moves.

**Seconds and words.** The budget a listener cares about is a duration, but
the quantity the selector adds up is a word count, so the two are related by
the measured speaking rate *r* = 1.85 words per second (below). The target
duration *T* scales with game length and is clamped at both ends:

> (6)  T(M) = min( 360, max( 150, 2·|M| ) )  seconds

The word budget of (4) is that duration converted to words, less the words
already committed to the mandatory intro, opening, finish and outro beats *R*
— which supply the coverage requirement directly rather than as a constraint:

> (7)  B = ⌊ r·T(M) ⌋ − Σ_{j∈R} c(j)

For a 33-half-move game *T* = 150 s, so *r*·*T* = 277 words; the mandatory
beats cost 153, leaving *B* = 124 — a turning point and one further moment,
or two ordinary moments. Working in integer words is also what makes the
exact solve below a finite dynamic program.

A fixed budget does not transfer across game lengths: 120 s is generous
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
adjacent: the script narrates four consecutive half-moves in detail and then
skips twenty, which is precisely the failure of the top-*N* baseline.

**Measured speaking rate.** *r* = 1.85 written words per second is measured,
not nominal: a 134-word script rendered at a nominal 155 wpm produced 72.6 s
of audio, an effective 111 wpm. The gap comes from clause-boundary pauses,
numeral expansion ("98 percent" is three spoken words) and per-clip padding.
Budgeting at the nominal rate promises a two-minute recap and delivers three.

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
which the verifier therefore cannot detect. Generation parameters are stated in
Section V-F. Four prompts are sent beneath one cached system prompt — an
outline, a per-beat realisation, a batched variant for full-coverage sheets,
and a repair prompt appending the verifier's specific finding — and all five
are reproduced verbatim in supplementary Section S1, with a worked beat in
S2. The system prompt states the constraint this argument depends on: every
chess fact stated must appear in the FACTS block, notation must never be
written in any form, and no numeric evaluation may be stated. The
architecture does not rely on the model obeying these, because the fact block
is the only chess content in the context and the output is checked against a
licence derived from the same function that built it.

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
Without it a line closes as though the game ended there and begins without
preceding intonation, and a six-beat recap is heard as six announcements
rather than one person speaking.

---

## V. Experimental Validation

All component results are measured against externally produced labels this
project did not generate. Every number below is produced by a script in
`docs/paper/studies/`; supplementary Section S4 names the script behind each
table and figure.

**Corpus and inclusion criterion.** The corpus is a PGN export of analysed
Lichess games (CC0), seeded from Lichess TV — which carries the variant
channels, so the raw export of 339 games proved to be **174 standard and 165
variant games**. Every result below uses the standard subset only: Stockfish
declines Antichess outright; for Horde, Atomic, King of the Hill, Racing
Kings and Three-check the win condition differs, so a standard engine's
evaluation does not mean what (1) assumes; and although Chess960 shares the
rules, the engine wrapper does not set `UCI_Chess960`.

An earlier version of this work applied no such criterion. It reported 14
games lost to "engine failures" that were in fact Stockfish refusing
Antichess, leaving a corpus roughly half variants scored by a standard-chess
engine. Filtering on a stated inclusion criterion, rather than discarding
whatever fails, is the difference between a sample and a residue; Section
VII-B records what it cost.

### A. Motif detection: precision and recall

Detectors were scored against the Lichess puzzle database (CC0) [15]. For
each of the twelve detectors, 1,000 puzzles carrying its own theme were drawn
by reservoir sampling over 6.5 million rows, with 2,000 controls from five
themes that are not motifs (`mateIn2`, `endgame`, `middlegame`, `crushing`,
`advantage`) carrying no theme under test — 14,000 puzzles in all. A detector
is credited if it fires anywhere among the solver's moves in the solution
line, since a Lichess theme describes the whole solution, not its first
move.

Precision is reported at this 1,000-to-2,000 sampling ratio, which is not the
prevalence of motifs in running commentary, and is a **lower bound** for a
second reason: Lichess tags are not exhaustive, so a control firing may be an
untagged true instance. That caveat is measurable — on a control sample of
the same size the back-rank-mate detector fired on 232 puzzles, and replaying
each line shows **all 232 do finish in checkmate on a back rank**. Its
apparent precision of 81.2% is an artefact of tagging, not a defect.

Table: Motif detector quality against 14,000 human-tagged puzzles
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

The hanging-piece detector is excluded from the mean because the theme does
not ask what the detector answers: Lichess tags `hangingPiece` when the
solution *takes* an undefended piece, whereas we detect a move that *leaves*
one hanging. Detecting captures instead reached full recall but fired on
56.9% of controls and labelled the acceptance of a famous queen sacrifice as
a piece left undefended, so it was reverted. The pin detector is the one
genuine weakness, trading recall for precision — 49.2% with its filter
against 63.5% without — because the unfiltered version reported 17 pins on a
single short game, obscuring the two that were material.

### B. Classification agreement, fitted and tested on disjoint splits

Lichess publishes both halves of what this comparison needs: `[%eval]` on
every move of an analysed game, and its own verdict as a NAG. The corpus is
the 174 standard games of 13,230 moves described above. Games — not moves —
were shuffled under a fixed seed and split in half. θ was grid-searched on
the training half alone over all integer triples in [2, 30], and κ is
reported on the held-out half.

Table: Classification thresholds fitted and tested on disjoint splits
| | Train, 87 games | Held out, 87 games |
|---|---|---|
| Moves | 6,366 | 6,864 |
| θ fitted on train | 5 / 10 / 15 | — |
| κ, fitted θ | 0.957 | **0.955** |
| κ, documented θ = 10/20/30 | — | 0.343 |
| Raw agreement | — | 98.7% |
| Agreement expected by chance | — | 71.9% |

Held-out κ is within 0.002 of training κ, so there is no measurable
overfitting — unsurprising, since the search recovers a published constant
rather than discovering a free parameter. Per-class held-out agreement:
inaccuracy 95.2%, mistake 86.1%, blunder 95.3%. We report κ rather than raw
agreement because the classes are heavily skewed toward non-errors. As
Section IV-B states, this establishes implementation fidelity, not a new
method; the substantive finding is that the widely circulated 10/20/30 bands
are about twice too lenient, yielding κ = 0.343 held out.

### C. Pacing over 173 games

Two games cannot show a distribution. The selector was run over all 173
standard games in the corpus that fall between 20 and 200 half-moves —
13,251 half-moves, median length 68 (IQR 55–92, range 21–196) — each analysed
at 1,000,000 nodes per position single-threaded, against both implemented
baselines. No run failed.

Table: Pacing against both baselines over 173 standard games
| | Paced | Top-*N* | Every move |
|---|---|---|---|
| Median duration | **143.2 s** | 194.6 s | 514.6 s |
| IQR | 142.2–178.4 | constant | 416.2–696.2 |
| Range | 117.8–348.6 | 194.6 | 158.9–1483.2 |
| Median beats | 7 | 8 | — |
| Opening covered | **100%** | 22.5% | 100% |
| Decisive move covered | 97.1% | 98.3% | 100% |
| Finish covered | **100%** | 7.5% | 100% |
| Games with no adjacent pick | **173/173** | 1/173 | — |

Median reduction against every-move narration is **3.6×** (IQR 2.9–3.9,
range 1.4–4.2). Duration, however, is the weaker half of the result, and two
comparisons must not be conflated.

**Against every-move narration** the advantage is duration, widening with
game length because the paced budget is clamped by (6) and the baseline's is
not. **Against top-*N*** the advantage is structural: top-*N* emits
exactly *N* = 8 beats of fixed cost, so its 194.6 s is **constant by
construction**, identical on every game. We say so explicitly because an
identical figure across games invites the suspicion of a measurement error.
It is also worth conceding the one column top-*N* wins — it covers the
decisive move slightly *more* often, 98.3% against 97.1% — so ranking by
swing is not what it lacks. What it lacks is everything the constraints
supply: it names the opening in under a quarter of games, reaches the finish
in one in thirteen, and in all but one of 173 games spends beats on
half-moves adjacent to another selection, four per game on average against
none. This is the evidence that the constraints rather than the scoring carry
the contribution, and the reason both baselines exist rather than one.

### D. Greedy optimality and weight sensitivity

Equations (3)–(5) are solved greedily, and Section IV-E claimed only that the
exact solution is available for comparison. Running both over the same 173
games gives the gap directly, as the ratio of the greedy selection's total
salience to the dynamic program's optimum:

The greedy selection reaches a mean **0.987** of the dynamic program's
optimum, is **exactly optimal on 138 of 173 games (80%)**, and its worst
observed case is 0.750. Greedy is therefore optimal on four games in five and
within 1.3% of the optimum on average. The gap arises where a
high-scoring candidate blocks two slightly lower-scoring ones that together
score more — the standard failure of greedy knapsack. Whether closing it is
worth an exact solve is a question about listeners rather than about the
objective, since the objective is itself a proxy; we report the gap and keep
the greedy solver, whose cost is negligible beside the engine's.

The six weights of (2) are set by hand. The relevant question is whether
each one measurably changes the output. Over 80 standard games the selector
was run at the shipped weights and again with each signal zeroed in turn.
*Retained* is the share of the shipped selection the variant still picks;
*value* is the variant's total salience scored under the **shipped** weights
as a fraction of the shipped selection's, so a variant picking worse moves
scores below 1.0 on the objective the system actually maximises; *decisive
beat* is the share of games giving the turning point a beat of its own, a
stricter test than the coverage row of Table V and 87.5% at the shipped
weights.

Table: Sensitivity of selection to each salience weight
| Signal removed | Retained | Jaccard | Value | Decisive beat |
|---|---|---|---|---|
| swing | **45.1%** | **0.323** | **0.782** | 81.2% |
| narrative | 80.4% | 0.750 | 0.984 | **56.2%** |
| motif | 81.1% | 0.745 | 0.974 | 90.0% |
| criticality | 87.3% | 0.825 | 0.985 | 86.2% |
| material | 92.0% | 0.883 | 0.996 | 87.5% |
| think time | 100.0% | 1.000 | 1.000 | 87.5% |

Three observations. **Swing dominates selection**: removing it retains under
half the picks and costs a fifth of the objective, the intended design, now
measured rather than asserted. **Narrative weight does a narrow but
irreplaceable job**: dropping it changes few picks and costs 1.6% of
salience, yet the decisive move loses its own beat in a third of games —
exactly the behaviour it exists to produce, and invisible to the objective.
**Think time is inert on this corpus**, not unimportant: the games were
exported without clocks, so the signal is identically zero and its weight is
untested here rather than shown unnecessary.

### E. The verification gate

Two quantities matter and only one is usually reported. The gate's
**false-positive rate** is measurable without a language model: the template
generator assembles sentences mechanically from the same fact block the gate
licences, so every claim it makes is true by construction and any
error-severity finding against its output is a false positive.

The measurement found a defect. Over 450 beats from 59 standard games the
gate raised **15 false positives**, 3.3 per 100, twelve of them one cause:
the name check strips punctuation per whitespace-delimited token, so the
supplied opening "Nimzo-Larsen Attack" reaches it as `NimzoLarsen`, while the
licence joined the whole string and admitted only `NimzoLarsenAttack`. An
opening the model had been handed came back as an invented name — exactly the
asymmetry Section IV-G says the shared derivation prevents, surviving because
no test used a hyphenated opening.

With the licence corrected to mirror the check's tokenisation and a
regression test added, the rate falls to **3 over 456 beats**, 0.66 per 100
(exact 95% upper bound 1.91), with 57 of 60 games clean. The residue is the
same class of gap: two beats name a square and one a promotion that the
licence does not derive from the facts they were given. We report rather than
pursue zero, because the claim worth making is not that the gate is perfect
but that its false-positive rate is **measurable on grounded text**, and that
measuring it locates real asymmetries. All 113 warnings were
`phrase_repetition`, which template prose incurs by design and which is
excluded.

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

**Generation parameters.** The model is requested as `claude-opus-5` and the
API reports that same identifier back, so no dated snapshot is exposed to pin
further. Adaptive extended thinking is enabled on every call, and with it
enabled the API does not accept a sampling temperature: **temperature is not
a free parameter of this system**. Reasoning effort is `low` for beat
realisation, batching and repairs, `medium` for the outline; token ceilings
are 2,000 for the outline and 1,000 per beat; the system prompt is identical
across a game and cached. Each figure below is a single run, and we do not
report variance across repeated runs.

Measured end to end at 1,000,000 nodes per position, single-threaded:

Table: Cost and latency, end to end
| | Opera Game, 33 plies | Blitz, 79 plies |
|---|---|---|
| Engine analysis | 29.6 s | 66.6 s |
| Script generation, 7/8 calls | 22.4 s | 29.5 s |
| **Cost** | **$0.0572** | **$0.0631** |

Generation cost and latency are approximately constant in game length,
because the number of beats is bounded by the duration budget rather than the
number of moves — a direct consequence of pacing. Engine analysis scales
linearly at about 1.43 s per position and dominates runtime on longer games.

Results depending on a single proprietary model are a weakness. Reproducing
Section V-E with an open-weight model is specified in Section VI-C.

---

## VI. Planned Human Evaluation

**This study has not been conducted.** Its design, hypotheses, thresholds,
sample size and analysis plan were committed before any data collection and
are reported here as a pre-registration; no human ratings are claimed
anywhere in this paper. Section V establishes that the commentary is
*correct*. It cannot establish that it is *worth listening to*, which is the
central claim of this work.

### A. Conditions and design

Five arms, rendered identically so participants cannot tell them apart by
format: **(A)** flat, one line per half-move; **(B)** top-*N* by swing;
**(C)** the system; **(D)** the system's pacing with sentences assembled
mechanically from the same facts; **(E)** a recruited club player (1800+)
given the same PGN and analysis but no system output. A and B isolate pacing,
D isolates the language model — identical pacing and facts, nothing wrote the
prose — and E is the ceiling.

Table: Pre-registered hypotheses for the listener study
| | Hypothesis | Supported if |
|---|---|---|
| H1 | Pacing improves engagement | C > A on engagement, median Δ ≥ 1.0, *p* < 0.05 |
| H2 | Constraints matter, not only scoring | C > B on pacing, median Δ ≥ 0.5 |
| H3 | Near the human ceiling | C within 0.5 Likert points of E on informativeness |
| H4 | The model earns its cost | C > D on engagement, median Δ ≥ 0.5 |
| H5 | Grounding holds up to listeners | C ≥ E on perceived accuracy |

H3 is deliberately a non-inferiority claim; "as informative as human
commentary" is not testable as stated, having neither a threshold nor a
matched corpus. If C does not exceed A on engagement the pacing hypothesis is
not supported, and that is the result we would report.

Within-subjects, *n* = 30 (minimum 24), stratified across beginner, casual
and club-strength listeners. Each hears four clips, each a different game in
a different condition, assigned by Latin square so condition does not
confound with order or difficulty, and rates five 5-point Likert items —
informativeness, engagement, perceived accuracy, pacing, naturalness — worded
as statements about their own experience and never mentioning automation.
Analysis: Friedman across conditions per item, Wilcoxon signed-rank for the
pre-specified pairs, Holm–Bonferroni correction, with median differences and
bootstrap 95% intervals beside every *p*-value.

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
specific model accounts for the result.

---

## VII. Limitations

**A. Reproducibility constrains engine configuration.** Stockfish's Lazy SMP
shares a transposition table between racing threads, so a fixed node budget
does not determine the result above one thread: two four-thread runs of one
game disagreed on 27 of 33 half-moves, enough to relocate the turning point.
All reported analysis is single-threaded, at about 1.43 s per position.

**B. Standard chess only.** Nothing here is evidence about variants, and the
system is not configured for them: the engine wrapper does not set
`UCI_Chess960` and (1) is a standard-chess mapping. Because the available
corpus was roughly half variants, the evaluation set is 174 games rather than
339 — statistical power paid to avoid measuring the wrong thing. Supporting
Chess960 is a small change to the wrapper and would roughly double it.

**C. Motif detection is rule-based.** Twelve well-defined patterns.
Deflection, overloading and zugzwang are not detected, and the pin detector
recovers under half of tagged pins (Section V-A).

**D. Two move-quality labels are unimplemented.** `brilliant` and `great`
exist in the data model and are never assigned; they require combined
sacrifice-soundness and only-move detection. The classifier produces six
labels.

**E. The hallucination result is a pilot.** Section V-E reports 32 beats with
overlapping confidence intervals and non-independent labelling. The gate's
false-positive rate is established on grounded text; its catch rate on model
output is not yet established at usable precision.

**F. Delivery is shaped more coarsely than the text, and metered.** Clips are
separated by a fixed gap regardless of content, so a commentator's rhythm —
pausing longer before a decisive move — is only partially reproduced. The
highest-quality backend also bills per character, so near-zero per-game cost
and the best available delivery cannot be had at once.

**G. English only.** The notation-to-speech converter is a rule-based
*English* grammar, the prompts are English, and the verifier's vocabulary is
English. Translating output after verification would void the gate's
guarantee, since the translated string has not been checked.

**H. The think-time weight is untested.** The evaluation corpus was exported
without clocks, so that signal is identically zero throughout Section V-D and
nothing here justifies its weight.

**I. Human evaluation is pending**, as stated in Section VI.

**J. Several reported quantities reached their final values only after an
initial measurement was found to be wrong**, in every case located by
inspecting individual cases rather than aggregates. Supplementary Section
S3 records each correction and its cause.

---

## VIII. Future Work

**Live commentary** is not a small change. Every per-position component works
unchanged on streamed input, but pacing does not: the budget needs the game's
length, the archetype cannot be fixed before the game ends, and the turning
point is by definition the largest error *of the game*. A naive live system
would reproduce the every-move baseline in real time. A fixed delay of
several moves, or pacing within fixed segments, preserves part of the
contribution; both make explicit that pacing is a lookahead problem.

Also: **rating-conditioned thresholds**, since λ in (1) is global while a
+2.00 position is less decided between 1200-rated players than between
masters; **multilingual output**, needing a spoken-form grammar and verifier
vocabulary per language; **Chess960 support**, which would double the corpus;
and **learned salience weights** replacing the hand-set **w** of (2).

---

## IX. Conclusion

We have described a system converting chess move notation into spoken
commentary with a synchronised board, without video. Its contributions are
pacing — formalised as budgeted selection in (3)–(5), solved greedily within
1.3% of an exact dynamic program and measured over 173 games against two
implemented baselines — grounding by construction, and a gate whose licence
is derived symmetrically from the facts it checks.

Validation against external labels gives mean 91.9% recall and 89.9%
precision over 14,000 human-tagged puzzles, and κ = 0.955 on a held-out split
of 6,864 annotated moves. Auditing the gate against mechanically grounded
text found and then removed a licence defect, leaving 0.66 false positives
per 100 beats; its effect on model output is a pilot with wide intervals,
and we report the protocol that would settle it rather than the conclusion we
would prefer. The evaluation that remains is the one only listeners can
provide, and its protocol is fixed in advance.

---

## References

[1] H. Jhamtani, V. Gangal, E. Hovy, G. Neubig, and T. Berg-Kirkpatrick,
"Learning to generate move-by-move commentary for chess games from
large-scale social forum data," in *Proc. ACL*, 2018, pp. 1661–1671.

[2] H. Zang, Z. Yu, and X. Wan, "Automated chess commentator powered by
neural chess engine," in *Proc. ACL*, 2019, pp. 5952–5961.

[3] A. Lee, D. Wu, E. Dinan, and M. Lewis, "Improving chess commentaries by
combining language models with symbolic reasoning engines," Dec. 2022,
*arXiv:2212.08195*.

[4] J. Kim, J. Goh, I. Hwang, J. Cho, and J. Ok, "Bridging the gap between
expert and language models: Concept-guided chess commentary generation and
evaluation," in *Proc. NAACL*, 2025, *arXiv:2410.20811*.

[5] S. A. Hebbar, P. Sheng, S. Oh, and P. Viswanath, "Hallucinations on the
board: Tool-augmented evaluation of LLM chess commentary," Aug. 2026,
*arXiv:2608.04240*.

[6] Z. Ji *et al.*, "Survey of hallucination in natural language
generation," *ACM Comput. Surv.*, vol. 55, no. 12, pp. 1–38, Mar. 2023,
doi: 10.1145/3571730.

[7] R. Barzilay and M. Lapata, "Collective content selection for
concept-to-text generation," in *Proc. HLT/EMNLP*, 2005, pp. 331–338.

[8] S. Wiseman, S. Shieber, and A. Rush, "Challenges in data-to-document
generation," in *Proc. EMNLP*, 2017, pp. 2253–2263,
doi: 10.18653/v1/D17-1239.

[9] R. Puduppully, L. Dong, and M. Lapata, "Data-to-text generation with
content selection and planning," in *Proc. AAAI*, vol. 33,
no. 1, 2019, pp. 6908–6915, doi: 10.1609/aaai.v33i01.33016908.

[10] J. Rao, H. Wu, C. Liu, Y. Wang, and W. Xie, "MatchTime: Towards
automatic soccer game commentary generation," in *Proc. EMNLP*, 2024, pp. 1671–1685,
doi: 10.18653/v1/2024.emnlp-main.99.

[11] H. Mkhallati, A. Cioppa, S. Giancola, B. Ghanem, and
M. Van Droogenbroeck, "SoccerNet-Caption: Dense video captioning for soccer
broadcasts commentaries," in *Proc. IEEE/CVF CVPR Workshops*,
2023, pp. 5074–5085, doi: 10.1109/CVPRW59228.2023.00536.

[12] Q. Zheng *et al.*, "From multimodal perception to strategic reasoning:
A survey on AI-generated game commentary," Jun. 2026,
*arXiv:2506.17294v3*.

[13] Lichess, "Accuracy metric."
https://lichess.org/page/accuracy (accessed Nov. 2, 2026).

[14] G. Xu, W. Song, Z. Zhang, C. Zhang, X. He, and B. Zhou, "Improving
prosody modelling with cross-utterance BERT embeddings for end-to-end speech
synthesis," in *Proc. IEEE ICASSP*, 2021, pp. 6079–6083.

[15] Lichess, "Open database and puzzle database" (CC0).
https://database.lichess.org/ (accessed Nov. 2, 2026).

---
