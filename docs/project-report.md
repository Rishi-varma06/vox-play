# Voxplay -- Project Report Sections

Automatic spoken commentary for chess games from engine analysis alone.

Covers: problem identification, literature survey, limitations of existing
systems, and project objectives. Companion document: `weekly-diary.md`.

---

## 1. Identification of the Problem

### 1.1 The gap

Millions of chess games are played every day on Lichess, Chess.com and in
club and college settings. Almost none of them are ever explained to the
person who played them.

A player finishing a game today has two options, and both fail in opposite
directions:

**Engine analysis tools** (Lichess computer analysis, Chess.com Game Review)
are accurate, instant and free. They return a move list annotated with
centipawn values, accuracy percentages and labels such as "blunder" or
"inaccuracy". What they do not return is an *account of the game*: where it
turned, why a move mattered, what one side was trying to do and where that
plan broke. The output is a cold report to be read, not a story to be
followed.

**Human commentary** does exactly that job, and does it well. It is also
available for a vanishingly small fraction of games -- world championship
matches, elite tournaments, a handful of streamed events. A club game, a
college match, or an amateur's own 1400-rated blitz game will never receive
it, because a human commentator costs hours of preparation per game.

The gap is therefore not a gap in *accuracy*. Engines already know what
happened with superhuman reliability. The gap is in **narration**: turning
correct structured facts into something a person would actually want to
listen to, at a cost per game low enough to apply to every game rather than a
chosen few.

### 1.2 Why chess is the right domain

Automatic commentary for a physical sport has to solve perception first --
what happened, who did it, when -- before it can say anything about it. That
perception step is the hardest and riskiest part of such a system, and it is
where most of the engineering effort goes.

Chess removes that problem entirely:

- The event stream is **already digital and exact**. A PGN file records every
  move unambiguously, and both major platforms publish it for free.
- The value of every position is **already computable**. A chess engine
  supplies ground truth about who stands better and by how much, at a quality
  no human commentator can match.
- Move quality is therefore **derivable rather than judged** -- the difference
  between the evaluation before and after a move is an objective quantity.

This lets the project spend all of its effort on the part that is genuinely
unsolved -- commentary quality and pacing -- rather than on perception. The
decision to skip vision is a scoping decision, not a shortcut: it
concentrates the work where the open research question actually lies.

### 1.3 Problem statement

> Given only a chess game's move notation and the evaluations an engine
> produces from it, automatically generate spoken commentary that selects,
> paces and narrates the moments that matter, with no fabricated content, at
> negligible cost per game -- and present it synchronised to a board so it
> can be watched rather than read.

---

## 2. Literature Survey

Work relevant to this project falls into four strands: chess commentary
generation, factual grounding and hallucination in that setting, commentary
generation for games and sports more broadly, and the position-evaluation
models the whole task rests on.

### 2.1 Chess commentary generation

**Jhamtani, Gangal, Hovy, Neubig and Berg-Kirkpatrick (2018)**, *Learning to
Generate Move-by-Move Commentary for Chess Games from Large-Scale Social
Forum Data* (ACL 2018), established the task and its benchmark. They scraped
the GameKnot forum to build a dataset of over **298,000 move-commentary pairs
across roughly 11,000 games**, and categorised commentary into six types:
move description, move quality, move comparison, rationale, contextual and
general. They proposed an end-to-end neural model conditioned on pragmatic
aspects of the board state, and reported human ratings comparable to ground
truth on correctness and fluency.

This paper defines the paradigm almost all later work inherits: **one comment
per move**.

**Zang, Yu and Wan (2019)**, *Automated Chess Commentator Powered by Neural
Chess Engine* (ACL 2019), improved generation quality by embedding a neural
chess engine inside the generation model, using it for board encoding, move
prediction and situation analysis, and training engine and generator jointly.
They evaluated across five commentary categories on the same benchmark.

**Lee, Wu, Dinan and Lewis (2022)**, *Improving Chess Commentaries by
Combining Language Models with Symbolic Reasoning Engines*, argued that
language models "lack grounding in the real world and struggle with tasks
involving complex reasoning", and coupled a symbolic chess engine with a
controllable language model. Human evaluators preferred their hybrid output
over prior baselines. This is the closest architectural ancestor of the
present project: facts from a symbolic engine, fluency from a language model.

**Kim, Goh, Hwang, Cho and Ok (2025)**, *Bridging the Gap between Expert and
Language Models: Concept-guided Chess Commentary Generation and Evaluation*
(NAACL 2025), framed the central tension directly: expert models play at
superhuman strength but cannot explain themselves, while LLMs "can produce
fluent commentary but are prone to hallucinations due to their limited
decision-making capabilities". Their method (CCC) generates prioritised,
concept-based explanations, and they contribute **GCC-Eval**, a GPT-based
evaluation framework scoring informativeness and linguistic quality.

### 2.2 Factual grounding and hallucination

**Hebbar, Sheng, Oh and Viswanath (2026)**, *Hallucinations on the Board:
Tool-Augmented Evaluation of LLM Chess Commentary*, is the most directly
relevant recent result. They introduce **ACT-Eval**, which decomposes
commentary into *atomic claims* and routes each to engine-backed tools and
expert-annotated references. On a benchmark of **325 position-move pairs**
with a five-category error taxonomy, they report:

- a strong contemporary model produced **incorrect claims 22.0% of the time**
  without tool support;
- smaller open-weight models **exceeded 40% error rates**;
- tool augmentation substantially improved factual accuracy;
- but conceptual and strategic coverage remained limited across all models.

The implication is decisive for system design: an LLM asked to reason about a
board will assert false chess facts often enough to matter, and grounding it
in engine-supplied facts is the demonstrated mitigation.

### 2.3 Commentary for games and sports generally

Automatic sports commentary has moved from template systems to neural and
multimodal ones. **Barzilay and Lapata (2005)** introduced content selection
for generating sports summaries from statistics; **van der Lee et al. (2017)**
built PASS, a data-to-text system producing tailored soccer reports;
**Taniguchi et al. (2019)** generated live soccer commentary from play data.
Recent multimodal systems such as **MatchTime (2024)** and work on soccer
highlight commentary combine vision and language, and a **2025 survey of
AI-generated game commentary** consolidates the field. Related work also
covers esports and imperfect-information card games such as Guandan.

Two observations carry over. First, **content selection** -- deciding what to
mention -- is treated as a first-class problem in the data-to-text tradition,
which the chess commentary line has largely skipped. Second, these systems
overwhelmingly target *text*; spoken delivery is usually left as an
afterthought or an off-the-shelf TTS call.

### 2.4 Position evaluation and move classification

The task depends on converting engine evaluations into a comparable quantity.
Raw centipawn loss is unsuitable: the same 100 centipawns is decisive near
equality and irrelevant in a won position. Lichess publishes the mapping the
field has largely standardised on:

```
Win% = 50 + 50 * (2 / (1 + exp(-0.00368208 * centipawns)) - 1)
```

with move classification driven by the *drop in winning chances* rather than
centipawns -- broadly, a loss of over 30 percentage points is a blunder, and
10 to 20 points an inaccuracy. Accuracy per game is derived from the same
win-percentage losses. This project adopts that mapping so that its
classifications are directly comparable with Lichess's own annotations, which
makes those annotations usable as an evaluation baseline.

### 2.5 Summary and the gap

| Work | Grounding | Selection / pacing | Output | Factuality check |
|---|---|---|---|---|
| Jhamtani et al. 2018 | board state | none -- every move | text | human rating |
| Zang et al. 2019 | neural engine | none -- every move | text | automatic + human |
| Lee et al. 2022 | symbolic engine | none -- every move | text | human preference |
| Kim et al. 2025 | expert model concepts | concept prioritisation | text | GCC-Eval |
| Hebbar et al. 2026 | engine tools | n/a (evaluation) | n/a | ACT-Eval, atomic claims |
| **Voxplay (this work)** | **engine + rule-based motifs** | **budgeted beat selection** | **speech + synced board** | **grounded facts + gate** |

Three gaps follow from the table:

1. **Pacing is absent.** Nearly all prior work produces one comment per move.
   Nobody asks which of a hundred half-moves deserve airtime, or how long
   each deserves. In audio this is not a refinement -- a 40-move game
   narrated move by move runs past ten minutes and is unlistenable.
2. **Output stops at text.** The literature generates written commentary.
   Spoken delivery, and synchronising it to a board, is unaddressed.
3. **Factuality is measured but rarely enforced.** ACT-Eval shows how to
   *measure* hallucination after the fact. Architecting the pipeline so the
   model is never in a position to invent a chess fact is a design response
   the literature has not fully taken up.

The contribution of this project is therefore **not** "an LLM writes chess
commentary", which is now well covered. It is **pacing, spoken delivery, and
factual grounding by construction.**

---

## 3. Limitations of Existing Systems

### 3.1 Engine analysis tools (Lichess, Chess.com)

| Limitation | Consequence |
|---|---|
| Output is a table of per-move labels | The reader must assemble the story themselves |
| No selection -- every move is scored equally | The one move the game turned on looks like the other 79 |
| No explanation of *why* | "Blunder, -3.2" says nothing about what was missed |
| Written only | Cannot be listened to while doing anything else |
| No narrative arc | Nothing distinguishes a collapse from a grind |

### 3.2 Human commentary

| Limitation | Consequence |
|---|---|
| Requires hours of expert preparation per game | Economically impossible below elite level |
| Available for a tiny fraction of games | Amateur games are never covered |
| Not reproducible or on demand | Cannot be requested for a game just played |

### 3.3 Academic chess commentary systems

| Limitation | Evidence |
|---|---|
| **Move-by-move paradigm** | Jhamtani et al. 2018 and successors emit one comment per move; no content selection stage exists |
| **Hallucination** | 22.0% incorrect claims for a strong model without tools; over 40% for smaller open-weight models (Hebbar et al. 2026) |
| **Text-only output** | No system in the reviewed line produces synchronised audio |
| **Evaluation is fluency-weighted** | Metrics emphasise linguistic quality; pacing and listenability are not measured at all |
| **Single-domain** | Systems are built for chess specifically, with no demonstrated reuse |

### 3.4 Limitations acknowledged in the present work

Stated here rather than hidden, since they bound what the project can claim:

- **Motif detection is rule-based.** It covers twelve well-defined tactical
  patterns. Higher-order motifs (deflection, overloading, zugzwang) are not
  detected. Measured recall is in section 4, Objective 5.
- **The pin detector deliberately trades recall for precision.** Pawn pins
  are excluded, which costs measurable recall against human tags (45.7% with
  the filter against 63.5% without) and buys specificity (7.9% control firing
  against 21.6%). The filter exists because unfiltered pin detection produced
  17 "pins" on a single 33-half-move game, almost all of them a queen
  notionally pinning a pawn to another pawn, drowning the two that mattered.
  The trade is the right one for commentary and the wrong one for matching a
  puzzle database; both numbers are reported rather than the flattering one.
- **The hanging-piece detector does not correspond to the Lichess theme of
  the same name**, and the mismatch is a difference of question rather than a
  defect. See Objective 5.
- **The listener study has not been run.** Claims about engagement and pacing
  remain untested until it is.
- **Opening-deviation detection is unimplemented**, so commentary cannot yet
  say when players left theory.
- **Engine analysis must be single-threaded to be reproducible.** Stockfish's
  parallel search shares a transposition table between racing threads, so a
  fixed node budget does not pin the result above one thread. This was
  measured, not assumed: two four-thread runs of the same game disagreed on
  27 of 33 half-moves, enough to move the detected turning point to a
  different move.
- **Synthetic speech quality is functional, not broadcast-grade.** The
  offline voice exists to make cost-per-game near zero; a premium backend is
  the intended comparison arm.

---

## 4. Objectives

Six objectives, each with the criterion by which it is judged complete.

### Objective 1 -- Ingest and evaluate

Accept a game as a PGN file, a Lichess link or a Chess.com link, and evaluate
every position with a chess engine.

*Success criterion:* a full game analysed end to end, reproducibly, in under
one minute.
**Status: complete.** 137 positions of a 136-half-move game analysed in 36.9
seconds; single-threaded runs are bit-identical across repetitions.

### Objective 2 -- Classify quality and detect significant moments

Classify every move from the change in winning chances, and detect tactical
motifs and larger structural moments.

*Success criterion:* classifications agree with published annotations of
well-studied games; motifs reproduce documented tactics.
**Status: substantially complete.** On the 1858 Opera Game the classifier
independently identified the two moves chess literature names as Black's
errors; the motif layer reproduces the published annotation, including the
discovered attack created by queenside castling. Opening-deviation detection
is outstanding.

### Objective 3 -- Generate a paced commentary script

Convert the structured analysis into a commentary script using a language
model, paced around what matters rather than listing every move.

*Success criterion:* the system produces a shorter, better-targeted script
than both a flat move-by-move baseline and an unconstrained top-N baseline,
with both baselines implemented for comparison.
**Status: complete.** Salience scoring with budget, coverage and spacing
constraints. Measured on the same game: 105 seconds for the system, 144 for
top-N (which selects adjacent half-moves and omits the checkmate) and 594 for
flat move-by-move.

### Objective 4 -- Speak it, synchronised to a board

Convert the script to spoken audio and present it against an automatically
generated animated board, with no source video.

*Success criterion:* a single artefact a person can play, in which the board
follows the narration.
**Status: complete.** Offline speech synthesis with a measured timeline, and
a self-contained HTML player carrying board, audio, captions, evaluation bar
and move list in one file.

### Objective 5 -- Validate

Validate the system against annotated master games and real user feedback.

*Success criterion:* precision and recall for motif detection against the
Lichess puzzle database; agreement with Lichess annotations for move
classification; and a blind listener study against the implemented baselines.

**Status: motif validation complete; classification agreement and the
listener study outstanding.**

Motif detectors scored against the Lichess puzzle database (CC0), 1,000
human-tagged puzzles per theme and 2,000 control puzzles drawn from unrelated
themes, sampled by reservoir with a fixed seed:

| Motif | Recall | Control firing |
|---|---|---|
| fork | 100.0% | 9.9% |
| doubleCheck | 100.0% | 0.0% |
| backRankMate | 100.0% | 10.7% |
| smotheredMate | 100.0% | 0.0% |
| promotion | 100.0% | 0.1% |
| enPassant | 100.0% | 0.0% |
| skewer | 95.5% | 9.6% |
| discoveredAttack | 93.3% | 5.0% |
| sacrifice | 87.3% | 6.2% |
| advancedPawn | 84.0% | 6.2% |
| pin | 47.0% | 6.2% |
| **Mean (11 comparable)** | **91.6%** | |
| hangingPiece | *not comparable* | |

*Recall* is the share of human-tagged puzzles where our detector fires.
*Control firing* is how often it fires on puzzles that are not about that
theme -- reported instead of precision because Lichess tags the theme that
makes a puzzle interesting, not every geometric feature on the board, so an
untagged detection is not necessarily wrong.

`pin` is low by design (see section 3.4). `hangingPiece` is excluded because
Lichess tags a puzzle that way when the solution *takes* an undefended piece,
while this project detects a move that *leaves* one hanging -- which by
construction cannot be the piece just captured. Measured on 1,000 tagged
puzzles: 100% have a piece already hanging before the move and 100% of
solutions capture an undefended one. Detecting captures-of-hanging instead
was implemented and reverted: it reached 100% recall but 56.9% control
firing, and on a real game it labelled an ordinary recapture and the
acceptance of a famous queen sacrifice as pieces "left undefended".
Separating a won piece from a recapture or an accepted sacrifice requires
move history that neither a detector nor a puzzle position carries.

### Objective 6 -- Build for reuse beyond chess

Structure the system so the commentary and voice pipeline can serve other
sports.

*Success criterion:* a second domain running through the same core without
modifying it.
**Status: met by construction.** The core was originally built for cricket
and the chess domain was added alongside it, sharing the event contract,
commentary engine, evaluation checks and speech layer. The reuse claim is
demonstrated by the project's own history rather than asserted.

---

## 5. Hypothesis

> Commentary generated purely from engine data can be as informative to a
> non-expert listener as commentary that took a human hours to prepare, at
> negligible cost per game -- and pacing around critical moments rather than
> narrating a flat move list is what makes it engaging rather than robotic.

**Operational form, to be tested in Objective 5:**

> Non-expert listeners rate engine-derived commentary within 0.5 Likert
> points of human-written annotation on informativeness, at under $0.10 and
> under 60 seconds of compute per game; and rate the paced system higher than
> both the flat and top-N baselines on engagement, with thresholds registered
> before the study is run.

---

## References

1. Jhamtani, H., Gangal, V., Hovy, E., Neubig, G., and Berg-Kirkpatrick, T.
   (2018). *Learning to Generate Move-by-Move Commentary for Chess Games from
   Large-Scale Social Forum Data.* ACL 2018.
   https://aclanthology.org/P18-1154/
2. Zang, H., Yu, Z., and Wan, X. (2019). *Automated Chess Commentator Powered
   by Neural Chess Engine.* ACL 2019.
   https://aclanthology.org/P19-1597/
3. Lee, A., Wu, D., Dinan, E., and Lewis, M. (2022). *Improving Chess
   Commentaries by Combining Language Models with Symbolic Reasoning
   Engines.* arXiv:2212.08195. https://arxiv.org/abs/2212.08195
4. Kim, J., Goh, J., Hwang, I., Cho, J., and Ok, J. (2025). *Bridging the Gap
   between Expert and Language Models: Concept-guided Chess Commentary
   Generation and Evaluation.* NAACL 2025. arXiv:2410.20811.
   https://arxiv.org/abs/2410.20811
5. Hebbar, S. A., Sheng, P., Oh, S., and Viswanath, P. (2026). *Hallucinations
   on the Board: Tool-Augmented Evaluation of LLM Chess Commentary.*
   arXiv:2608.04240. https://arxiv.org/abs/2608.04240
6. *AI-Generated Game Commentary: A Survey.* (2025). arXiv:2506.17294.
   https://arxiv.org/abs/2506.17294
7. Rao, J. et al. (2024). *MatchTime: Towards Automatic Soccer Game Commentary
   Generation.* arXiv:2406.18530. https://arxiv.org/abs/2406.18530
8. Lichess. *Accuracy metric.* https://lichess.org/page/accuracy
9. Lichess open database and puzzle database (CC0).
   https://database.lichess.org/
10. Barzilay, R., and Lapata, M. (2005). *Collective Content Selection for
    Concept-to-Text Generation.* HLT/EMNLP 2005.

> Note on reference 10 and the other classical data-to-text citations in
> section 2.3: these were identified through secondary sources during the
> survey and should be read in the original before being cited in the final
> report.
