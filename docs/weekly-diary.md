# Voxplay -- Weekly Project Diary

Running record of work, evidence and problems. Every entry is backed by
commits in this repository, so dates and claims are checkable rather than
recalled. Companion document: `project-report.md`.

**Repository:** https://github.com/Rishi-varma06/vox-play

---

## Summary to date

| Week | Dates | Focus | Commits | Tests |
|---|---|---|---|---|
| 1 | 07 Sep -- 13 Sep 2026 | Pivot to chess; analysis, motifs, pacing, script, CLI | 7 | 320 |
| 2 | 14 Sep -- 20 Sep 2026 | No recorded activity | 0 | 320 |
| 3 | 21 Sep -- 27 Sep 2026 | Spoken audio and the synchronised board player | 2 | 358 |
| 4 | 28 Sep -- 04 Oct 2026 | Literature survey, docs, validation, verification gate | in progress | 444 |

**Current scale:** ~12,500 lines of Python across implementation and tests,
444 automated tests passing.

---

## Week 1 -- 07 September to 13 September 2026

**Objectives addressed:** 1, 2, 3, 6

### The decision that shaped the project

The project existed as a **cricket** commentary engine: an event schema, a
Claude-backed commentary engine, six grounding checks, a text-to-speech layer
and both a terminal and a web scorer. Its blocker was structural rather than
technical -- producing the event stream required a person to score the match
ball by ball, or a vision model that had not been built.

The decision was taken to **pivot to chess**, because chess supplies the event
stream and the ground-truth evaluation for free. The pivot was implemented as
an *addition* rather than a rewrite: the cricket domain was left intact and a
chess domain was added alongside it.

This had an unplanned benefit. Objective 6 -- that the pipeline should extend
beyond one sport -- had been scheduled as a risky end-of-project experiment.
Adding a second domain to a core written for the first *is* that experiment,
so the objective became evidence from the project's own history instead of a
claim to be tested later.

### Work completed

| Component | Description |
|---|---|
| Version control | The existing 4,300-line codebase was under no version control; a baseline snapshot was taken before any change (`fbd555d`) |
| Ingest | PGN files, Lichess API and Chess.com archives to an event stream, including clock times and derived think time |
| Engine analysis | Stockfish 19 integration with a node-based budget and an offline mock backend |
| Evaluation model | Centipawns to win probability to move classification |
| Motif detection | Twelve tactical patterns, built on a static exchange evaluator written for the purpose |
| Beat selection | Salience scoring, narrative arc extraction, game archetypes, plus both comparison baselines |
| Script generation | Notation-to-speech conversion, grounded prompting, two-pass generation |
| Command line | `voxplay chess <pgn or lichess link>` |

### Problems encountered and resolved

**The classifier called a famous winning combination a mistake.** The first
run against real engine output labelled two moves of Morphy's Opera Game
combination as inaccuracies. Both were the engine's *own first choice*. The
apparent loss of win probability was the search revising its earlier
assessment one ply deeper -- the engine correcting itself, not the player
erring. Resolved by the rule that a move the engine ranks first can never be
classified as an error.

**Motif detection had good recall and poor precision.** The naive
implementation produced 36 motifs on a 33-half-move game, including 17
"pins", most of which were a queen notionally pinning a pawn to another pawn.
These buried the two pins that mattered. Three filters were added -- pawns
excluded from pins and skewers, forks required to have two targets worth a
knight or more, and standing patterns reported only on the move that creates
them. Output fell to 13 motifs, all meaningful.

**A leaked engine subprocess hung the test suite.** A test constructed an
analysis engine without closing it; on a machine with Stockfish installed
this left a child process and pytest never exited.

### Validation obtained

Analysis of the 1858 Opera Game at one million nodes per position:

```
Paul Morphy                  accuracy 97.9%   errors 0
Duke Karl and Count Isouard  accuracy 88.2%   errors 2  (Nf6, b5)
```

`Nf6` and `b5` are precisely the two moves chess literature identifies as
Black's errors, `b5` being the move that permits the winning sacrifice. The
classifier found them with no chess knowledge beyond the win-probability
model. The motif layer independently reproduced the published annotation,
including the discovered attack created by queenside castling.

**Commits:** `fbd555d`, `2dc343d`, `520f14f`, `686a89a`, `f13516b`,
`3892030`, `aa7654c`

---

## Week 2 -- 14 September to 20 September 2026

No recorded activity. No commits were made in this period.

---

## Week 3 -- 21 September to 27 September 2026

**Objective addressed:** 4

### Work completed

| Component | Description |
|---|---|
| Speech synthesis | Offline provider requiring no network, no API key and no ffmpeg; WAV output assembled with the standard library |
| Timeline | Per-beat start and duration measured from the rendered audio, together with the half-moves each beat describes |
| Board player | Self-contained HTML page carrying board, audio, captions, evaluation bar, move list and clickable beat markers |

The timeline is the component that made the player possible. Because each
spoken beat already recorded which half-moves it was about, the board only had
to follow it.

### Problems encountered and resolved

**The speech library deadlocked on the second clip.** `pyttsx3` synthesised
the first clip in 0.1 seconds and never returned from the second, with or
without a fresh engine per clip. Since a game needs one clip per beat, this
was fatal on Windows. Driving the platform speech API directly through COM
instead produced five clips at 0.02 seconds each with no hang.

**All six audio clips silently overwrote one file.** `Path.with_suffix()`
replaces everything after the *last* dot, and game identifiers derived from
PGN dates contain dots: `..._1858.11.02_000_intro` became `..._1858.11.wav`.
Every clip collapsed onto a single filename. The failure was invisible -- the
assembled track was the correct length and the timeline reported six cues --
because it was the final clip repeated six times. Caught only by noticing
that six beats of very different word counts all reported identical
durations. The same latent bug was present in the cricket speech providers.

**The speaking-rate documentation was wrong.** The speech API documents its
rate dial as 200 words per minute at zero with threefold change at the
extremes. Measurement across nine settings gave 188.5 words per minute at
zero with approximately 1.12x per step. The documented curve had been
selecting 138 words per minute when 150 was requested.

### A measurement that corrected the pacing model

The duration model had assumed 2.5 words per second throughout the project.
Measured against rendered audio, the true figure is **1.85** -- a 40 per cent
error. The raw speaking rate is 155 words per minute but the *effective* rate
is 111, because clause boundaries insert pauses, numerals expand when spoken
("98 percent" is three spoken words rather than two), and each clip carries
about a second of padding.

Every beat budget in the system depended on this constant, so the system had
been promising a two-minute recap and delivering nearly three. The constant
was recalibrated and a drift metric added:

| | Before | After |
|---|---|---|
| Predicted duration vs. actual | 1.56x | **1.03x** |

This error was undetectable until audio existed to measure against, which is
itself an argument for building the output stage rather than deferring it.

**Commits:** `230cfac`, `bee6b94`

---

## Week 4 -- 28 September to 04 October 2026 (current)

**Objective addressed:** 5 (preparation)

### Work in progress

- Literature survey completed and written up: chess commentary generation,
  hallucination and factual grounding, sports and game commentary generally,
  and position-evaluation models. Fourteen sources reviewed, with the five
  most relevant read in the original.
- Problem identification, limitations of existing systems and objectives
  written up as report sections (`docs/project-report.md`).
- This diary established.

### Principal finding from the survey

The survey settled what this project can and cannot claim as novel.
"A language model writes chess commentary" is well covered -- Jhamtani et al.
(2018) established the benchmark, and there is a continuous line of work
through to NAACL 2025. What the literature has **not** addressed is:

1. **Pacing.** Essentially all prior work emits one comment per move. No
   content-selection stage exists in this line, although the wider
   data-to-text tradition treats content selection as a first-class problem.
2. **Spoken, synchronised output.** The literature generates text.
3. **Grounding by construction.** Hebbar et al. (2026) measured a strong
   model producing incorrect chess claims 22.0 per cent of the time without
   tool support, and smaller models exceeding 40 per cent. They measure the
   problem after the fact; designing the pipeline so the model is never in a
   position to invent a chess fact is a response the literature has not fully
   taken up.

This external measurement independently supports a design decision already
taken in week 1 -- that motif detection and evaluation must be computed by
tested code and handed to the model as facts, never asked of the model.

### Motif validation (Objective 5)

Detectors scored against 1,000 human-tagged puzzles per theme plus 2,000
controls. **Mean recall 91.6%** across the eleven comparable motifs, six of
them at 100%. Full table in `project-report.md`.

Getting there took three corrections, and each was worth more than the
number:

**The obvious approach failed.** Sampling the puzzle API one call at a time
ran for four hours, collected 25 puzzles across twelve themes, and was rate
limited out. Ten themes got nothing. The run was abandoned in favour of the
published database -- one 290MB download, no rate limits, every theme, 54
seconds to score 14,000 puzzles.

**The harness lied about an empty sample.** With no puzzles collected it
printed "0.0% recall" for ten themes and a confident mean of 58.3% computed
over two themes with six puzzles each. That reads as detectors that never
fire rather than a sample that never arrived. Recall now returns nothing
below a sample, undersized themes are excluded from the mean, and a run with
nothing usable says so in words.

**The first real measurement was measuring the wrong thing.** Scored against
the solution's first move only, the mean was 46.5% and `promotion` scored
10.5% -- impossible for a detector whose entire test is whether the move
carries a promotion. A Lichess theme names the whole solution line, not its
first move. Walking the line and scoring every move the solver makes took the
mean from 46.5% to 86.0%. The detector had been right; the question was
wrong.

**A diagnosed defect was fixed, measured, and then reverted.** The
hanging-piece detector scored 26.8% recall against a 26.3% control rate --
no signal at all. Diagnosis was unambiguous: of 1,000 tagged puzzles, 100%
have a piece already hanging and 100% of solutions capture an undefended one,
while our detector reports pieces a move *leaves* hanging, which cannot
include the one just taken. The alternative detector reached 100% recall, but
control firing rose to 56.9% and on the Opera Game it called an ordinary
recapture and the acceptance of Morphy's queen sacrifice pieces "left
undefended". Telling a won piece from a recapture needs move history that
neither a detector nor a puzzle position has, so the change was reverted and
the theme is now reported as not comparable with the reason attached.

### Classification agreement (Objective 5)

Our move classifier against Lichess's own annotations on 24,255 moves from
339 analysed games: **Cohen's kappa 0.932**, raw agreement 97.8%. Full
confusion matrix in `project-report.md`.

**The measurement found a real error in the system, which is why it was worth
doing.** The project had been using the thresholds community documentation
gives -- about 10, 20 and 30 points of winning chances. Measured against
ground truth those scored kappa 0.355 and agreed with *none* of Lichess's
2,133 inaccuracies: they are roughly twice too lenient. The confusion matrix
made it obvious, because the disagreement was not noise but a clean one-band
shift, every inaccuracy called fine and every blunder called a mistake.

Fitting thresholds to maximise agreement gives 5 / 10 / 15 and kappa 0.932.
The values are round because Lichess thresholds a winning-chances delta of
0.1 / 0.2 / 0.3 on a -1..1 scale, which is 5 / 10 / 15 on a 0-100 one. The
documentation this project trusted described a different quantity.

The corrected thresholds changed behaviour everywhere they are used: the
salience model's swing saturation was hard-coded to the old blunder
threshold, so every genuine blunder had been scoring half the salience it
deserved. It is now tied to the threshold rather than repeated.

Both halves of this week's validation work followed the same pattern -- the
first number was wrong, the confusion matrix or recall table showed *how* it
was wrong, and the diagnosis was more useful than the final figure.

### Verification gate (Objective 3)

The third of the three things this project claims are new, and the last one
that was still only a design. Every generated beat is now checked against the
facts it was given, and a beat that invents something is regenerated once
with the failure fed back rather than shipped.

Five factual checks: notation reaching the speech backend, a square named
aloud that is not in the beat's facts, a tactic no detector found, a claim
about who is better that the evaluation contradicts, and a name not present
in the game data.

Validated in both directions, which matters more than it sounds: a verifier
that never fires proves nothing. The template generator passes with zero
findings, and every category of invention is caught when injected
deliberately.

Building it surfaced a flaw in its own design. The first licence -- the set
of claims a beat is allowed to make -- was built only from the moves played,
so it flagged "better was king to d eight" as an invented square. That is the
engine's suggested alternative, a fact the model had been handed. The licence
must mirror the facts block exactly: anything the facts give but the licence
omits becomes a false positive, and anything the licence allows that the facts
never supplied becomes a blind spot.

The gate also caught a real repetition in the template generator's own
output, which is a style warning rather than a defect but a fair one.

### Planned for next week
- Draft the listener-study protocol and register thresholds before running
  it.
- Begin recruiting listeners -- identified as the schedule's long pole.

---

## Outstanding work

| Item | Objective | Notes |
|---|---|---|
| Listener study | 5 | **The only remaining research item.** Protocol pre-registered in `listener-study-protocol.md`; blocked on recruiting participants |
| Opening-deviation detection | 2 | Requires the Lichess opening explorer |
| Live language-model commentary path | 3 | Implemented but unverified; the API key currently has no credit |

---

## Diary conventions

One entry per week, added at the end of the week, containing:

1. **Objectives addressed** -- which of the six.
2. **Work completed** -- what exists now that did not before.
3. **Problems encountered and resolved** -- including failures, because these
   are the parts that will not be reconstructable later.
4. **Validation obtained** -- measurements, with the numbers.
5. **Commits** -- so every claim can be checked against the repository.
6. **Planned for next week.**
