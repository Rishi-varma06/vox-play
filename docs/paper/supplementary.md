# Supplementary material

**Voxplay: Pacing and Verifiable Grounding for Spoken Chess Commentary
Generated from Move Notation**

This document contains material referenced from the paper and omitted there
for length. It carries no author or institutional identification and may be
submitted as anonymous supplementary material.

Contents:

- **S1** — the five prompts, verbatim as sent
- **S2** — a worked beat: fact block in, spoken line out, licence checked
- **S3** — correction history
- **S4** — reproducing the measurements
- **S5** — the pre-registered evaluation protocol, in full
- **S6** — per-signal ablation of the salience weights
- **S7** — per-detector motif quality

---

## S1. Prompts, verbatim

Four prompts are sent during generation, beneath one system prompt that is
attached to every request and cached across the beats of a game. Placeholders
in angle brackets are filled at run time; everything else is sent as written.

### S1.1 System prompt

```
You are a chess commentator narrating a completed game for audio.
Your voice is warm, clear and knowledgeable -- a person talking, not a report being read.

ABSOLUTE RULES:
1. Every chess fact you state must appear in the FACTS block you are given.
   Never invent a move, an evaluation, a tactic, a player name, or a result.
   If a fact is not given to you, leave it out. An omitted detail is always
   better than a wrong one.
2. You are writing for TEXT-TO-SPEECH. Never write chess notation. Never write
   "Nf3", "Qxh7#", "1.e4", "+2.3" or "#". The FACTS block gives you the spoken
   form of every move -- use it, or rephrase it in your own words.
3. Never state a numeric evaluation. The FACTS block describes the position in
   words; use those words.
4. Do not describe what happens later in the game than the moment you are
   narrating, unless the facts for this beat say the result explicitly.
5. Vary your phrasing. Do not open consecutive lines with the same word, and do
   not reuse a run of four or more words from the previous lines shown to you.
6. Never refer to yourself, to being an AI, to engines, evaluations, or to this
   being generated.

Write only the words to be spoken. No headings, no stage directions, no notation.
```

Rule 1 is the grounding claim of Section IV-F, rule 2 is why notation is
converted by rule before the model sees it, and rule 4 is what the
spoiler-free licence enforces mechanically rather than by instruction.

### S1.2 Prompt 1 — outline, one call per game

Shown with the values for the 1858 Opera Game.

```
Plan the narration for one chess game before writing it.

FACTS
- players: Paul Morphy vs Duke Karl and Count Isouard
- opening: Philidor Defense
- length: 33 half-moves
- shape of the game: unknown
- result: White won

RUNNING ORDER
  1. intro -- every game needs an opening line
  2. opening -- opening: Philidor Defense
  3. bridge -- covers the run-up to the finish
  4. finish -- how the game ended
  5. outro -- sign-off

Measured and precise. Explain why things matter.
Your listener is a beginner: explain any chess term the moment you use it.


In no more than 80 words, state the through-line of this game: what it is
about, and what a listener should come away understanding. This is a note to
yourself, not something anyone hears. Do not write any commentary yet.
```

The last two lines before the instruction are the style and audience
settings; they are the only part of any prompt a user can change.

### S1.3 Prompt 2 — one beat

```
THROUGH-LINE OF THE GAME
<outline from prompt 1>

LINES YOU HAVE ALREADY SPOKEN (do not repeat their phrasing)
  - <each line already accepted, in order>

THIS BEAT: opening

FACTS
- opening played: Philidor Defense
- ECO code: C41
- moves: 1. pawn to e four; 1... pawn to e five; 2. knight to f three;
  2... pawn to d six; 3. pawn to d four; 3... bishop to g four;
  4. pawn takes the pawn on e five; 4... bishop takes the knight on f three
- target length: about 40 words

Measured and precise. Explain why things matter.
Your listener is a beginner: explain any chess term the moment you use it.


Write the spoken words for this beat only.
```

The FACTS block is produced by `beat_facts()` and is the complete and only
factual basis for the line. The verifier's licence for the same beat is
derived from the same function, which is the symmetry Section IV-G relies on.

### S1.4 Prompt 3 — batched, for full-coverage sheets

Used only when the user selects every-move coverage, in runs of six beats.

```
THROUGH-LINE OF THE GAME
<outline>

LINES YOU HAVE ALREADY SPOKEN (do not repeat their phrasing)
  - <previous>

You are writing 3 consecutive lines of spoken commentary. Each one
covers the moves in its own block and nothing else. Let the run read as
continuous speech -- vary the sentence shapes, and do not begin every line
the same way -- but keep each line able to stand on its own, because the
listener can jump straight to any of them.

--- LINE 1 (intro) ---
- players: Paul Morphy vs Duke Karl and Count Isouard
- event: Paris Opera
- opening: Philidor Defense
- length: 33 half-moves
- shape of the game: unknown
- result: White won
- target length: about 35 words

--- LINE 2 (opening) ---
- opening played: Philidor Defense
- ECO code: C41
- moves: 1. pawn to e four; 1... pawn to e five; ...
- target length: about 40 words

--- LINE 3 (bridge) ---
- covering moves 5 to 15 in a single sentence -- summarise, do not list them
- pieces traded: 9
- no errors in this stretch
- target length: about 22 words

Measured and precise. Explain why things matter.
Your listener is a beginner: explain any chess term the moment you use it.


Return exactly 3 lines, each prefixed with its number and a pipe,
like:
1| <the spoken words for line 1>
2| <the spoken words for line 2>

No other text, no blank lines between them.
```

The reply is split on the `N|` prefixes and the whole batch is rejected if
the shape is wrong. A partially parsed batch would attach one move's
commentary to a different move — an error in which every individual claim is
still true, so the verifier cannot catch it.

### S1.5 Prompt 4 — repair

Appended to whichever beat prompt produced the rejected line, so the model
sees its original facts and the specific finding together.

```
<the beat prompt that produced the rejected line>

YOUR PREVIOUS ATTEMPT WAS REJECTED
- <one line per verifier finding, e.g. "claims tactics no detector found: ['fork']">
Rewrite it using only the facts above.
```

One retry is attempted by default. Naming the unsupported claim rather than
asking generically for a rewrite is what makes the retry effective: the model
invents when it is reaching past its facts, and the finding tells it which
reach to withdraw.

---

## S2. A worked beat

The turning point of the 1858 Opera Game, half-move 18.

**Facts supplied** (abridged to the fields the licence covers):

```
THIS BEAT: turning_point
FACTS
- move: knight to b five
- verdict: mistake
- cost: 23 points of winning chances
- better was: bishop to c four
- tactic: discovered attack
- position after: White is clearly better
- target length: about 65 words
```

**Licence derived from the same facts:** squares `{b5, c4}`; motifs
`{discovered_attack}`; names `{Morphy, Duke Karl, Count Isouard}`; evaluation
`White ahead`; result **not** revealable.

**Accepted line:** a sentence naming the move in spoken form, the verdict in
words, the engine's preference, and the tactic — every one of which appears
above.

**A line that fails:** *"A beautiful fork on h7 wins material, pure Kasparov
at his very best."* Three checks fire at once — `notation_leak` on `h7`,
`ungrounded_name` on Kasparov, and `ungrounded_tactic` on the fork, since no
detector found one. This is the case shown in Fig. 3 of the paper, and it is
caught identically whether a model or a person typed it.

---

## S3. Correction history

Each quantity below was reported at an earlier value during development and
corrected after inspecting individual cases rather than aggregates. They are
recorded because the failure modes recur, and because two of them were found
only because a number was *identical* where it had no reason to be.

| Quantity | First reported | Corrected | Cause |
|---|---|---|---|
| Motif recall | 46.5% | 91.9% | The validator scored only the first move of each puzzle, whereas a Lichess theme describes the whole solution line. |
| Classification thresholds | 10 / 20 / 30 | 5 / 10 / 15 | Taken from community documentation rather than measured. They agreed with none of 2,133 moves Lichess tags as inaccuracies. |
| Hallucination rate | 18.8 per 100 beats | 6.2 per 100 beats | The checker mishandled apostrophes and British spelling variants, counting them as invented text. |
| Every-move baseline | 594 s | 249.7 s | The baseline gave every move a full 45-word treatment, which is not what per-move commentary is. The comparison was a strawman. |
| Paced duration | 117.8 s on both games | 142.2 / 154.1 s | The measurement script passed a fixed 120 s budget instead of the length-scaled budget the system ships with. Two different games producing byte-identical durations was the tell. |
| Speaking rate | 155 wpm nominal | 111 wpm measured | Clause-boundary pauses, numeral expansion and per-clip padding. |
| Evaluation corpus | 339 games | 174 standard games | The corpus was exported from Lichess TV, which carries the variant channels, so about half of it was Chess960, Horde, Antichess, Atomic, King of the Hill, Racing Kings or Three-check. Engine failures on 14 games turned out to be Stockfish refusing Antichess, which is what led to the rest being found. |
| Engine rate | 1.43 s per position | 1.18 s per position | The 1.43 came from three games timed in one sequential process on a loaded machine, and disagreed by about 1.7x with the per-game totals reported alongside it. Re-measured with two repeats per game on an idle machine at the configuration the paper claims. |
| Gate before/after | 450 beats vs 456 | 456 vs 456 | The two arms had been measured over different corpora, 59 games against 60, because one game failed in the earlier run. Both were re-measured over the same 60. |
| Win-probability example | 12 points near equality, under 1 at +900 | 9.1 and 1.5 | Arithmetic never checked against the formula it illustrates. |
| Classifier agreement | κ = 0.932 on the mixed corpus | κ = 0.955 held out on standard games | Fitted and reported on the same 24,255 moves, about half of which were variants. Splitting the games and restricting to standard chess both raised it. |
| Gate false positives | reported as zero | 15 per 456 beats, then 3 after a fix | The zero was measured on the variant-contaminated corpus. On standard games the gate rejected opening names it had itself supplied, because the licence joined the whole name while the check splits on whitespace. |

The last of these is the most instructive. The failures were initially
attributed to memory pressure, because they appeared during a run with
several studies in flight. Re-running them alone at low concurrency failed
identically, which ruled that explanation out and led to the variant headers.
An exclusion that cannot be reproduced in isolation has not been explained.

---

## S4. Reproducing the measurements

Every number in the paper is produced by a script, and no number is copied by
hand between a run and the text. Running the scripts in this order
regenerates the results tables and both data figures:

| Script | Produces |
|---|---|
| `motif_precision.py` | Table I — per-detector precision, recall and F1 |
| `control_firings.py` | the check that control firings are untagged true instances, quoted in Section V-A |
| `threshold_split.py` | Table II — thresholds fitted and tested on disjoint splits |
| `pacing_study.py` | Table III, and the greedy optimality quoted in Section V-D |
| `weight_ablation.py` | the ablation of Section S6 |
| `why_missed.py` | the diagnosis of the two missed decisive moves in Section V-C |
| `gate_false_positives.py` | the gate false-positive rate quoted in Section V-E |
| `make_figures.py` | Figs. 1 and 3 |

All of them read the same two corpora: a PGN export of analysed Lichess games
and the Lichess puzzle database, both CC0. The engine is pinned by node
budget rather than depth or time, and runs single-threaded, so repeated runs
of the same script on the same corpus are bit-identical.

---

## S5. Pre-registered evaluation protocol


**The listener study has not been conducted.** Its design and analysis plan
were committed before any data collection and are reported as a
pre-registration; no human ratings are claimed anywhere in this paper.
Section V establishes that the commentary is *correct*, not that it is *worth
listening to*, which is the central claim of this work. Five arms are
rendered identically — flat one-line-per-half-move (A), top-*N* by swing (B),
the system (C), the system's pacing with sentences assembled mechanically
from the same facts (D), and a recruited club player given the same PGN and
analysis (E) — so that A and B isolate pacing, D isolates the language model
and E is the ceiling. Within-subjects, *n* = 30, stratified by playing
strength, Latin square over four clips each, five 5-point Likert items,
Friedman per item with Wilcoxon signed-rank on pre-specified pairs and
Holm–Bonferroni correction. The pre-registered thresholds are C > A on
engagement by a median of 1.0, C > B on pacing by 0.5, C non-inferior to E on
informativeness within 0.5, and C > D on engagement by 0.5. If C does not
exceed A the pacing hypothesis is not supported, and that is the result we
would report.

Replacing the pilot of Section V-E: at least 20 games generated twice each
with the gate disabled and enabled, not fewer than 400 beats per arm,
segmented into atomic claims. Two annotators, at least one club-strength,
label each claim independently as supported, unsupported or unverifiable,
working from the board and engine output and **not** from the checker's
findings; inter-annotator agreement is reported as Cohen's κ before any rate
is. Reported per arm: unsupported claims per 100 beats with exact binomial
intervals, and for the gated arm the regeneration rate and the gate's
false-positive rate against the annotators' labels. The same protocol is
applied to one open-weight model.

---

## S6. Per-signal ablation of the salience weights

Over 80 standard games the selector was run at the shipped weights of (2) and
again with each signal zeroed in turn. *Retained* is the share of the shipped
selection the variant still picks; *jaccard* is the overlap of the two
selected ply sets; *value* is the variant's total salience scored under the
**shipped** weights as a fraction of the shipped selection's, so a variant
that picks worse moves scores below 1.0 on the objective the system is
actually maximising; *decisive beat* is the share of games in which the
turning point is selected as a beat of its own, which stands at 88.6% for the
shipped weights. It is taken over the 79 of 80 games that have a turning
point at all; the one drawn game without one is excluded rather than scored
as a miss.

| Signal removed | Retained | Jaccard | Value | Decisive beat |
|---|---|---|---|---|
| swing | 45.1% | 0.323 | 0.782 | 82.3% |
| narrative | 80.4% | 0.750 | 0.984 | 57.0% |
| motif | 81.1% | 0.745 | 0.974 | 91.1% |
| criticality | 87.3% | 0.825 | 0.985 | 87.3% |
| material | 92.0% | 0.883 | 0.996 | 88.6% |
| think time | 100.0% | 1.000 | 1.000 | 88.6% |

Think time is identically zero on this corpus because the games were exported
without clock annotations, so the row records that the weight is untested
here rather than that it is unnecessary. Produced by `weight_ablation.py`.

---

## S7. Per-detector motif quality

Against 14,000 puzzles from the Lichess puzzle database (CC0): 1,000 per
detector carrying that detector's own theme, plus 2,000 controls drawn from
five themes that are not motifs. Precision is at that 1,000-to-2,000
sampling ratio and is a lower bound, because Lichess theme tags are not
exhaustive and a control firing may be an untagged true instance.

| Detector | Recall | Precision | F1 |
|---|---|---|---|
| double check | 100.0% | 100.0% | 1.000 |
| smothered mate | 100.0% | 100.0% | 1.000 |
| en passant | 100.0% | 100.0% | 1.000 |
| promotion | 100.0% | 99.7% | 0.999 |
| discovered attack | 94.0% | 91.0% | 0.925 |
| fork | 100.0% | 82.4% | 0.904 |
| back-rank mate | 100.0% | 81.2%* | 0.896 |
| skewer | 96.8% | 82.2% | 0.889 |
| advanced pawn | 85.2% | 88.9% | 0.870 |
| sacrifice | 86.1% | 86.7% | 0.864 |
| pin | 49.2% | 77.1% | 0.601 |
| **Mean, 11 comparable** | **91.9%** | **89.9%** | **0.904** |
| hanging piece, excluded | 26.1% | 33.2% | 0.292 |

*All 232 control firings of the back-rank detector were verified by
replaying each solution line to its end: every one finishes in checkmate on
a back rank, so its apparent precision is an artefact of tagging rather than
a defect. Produced by `motif_precision.py` and `control_firings.py`.
