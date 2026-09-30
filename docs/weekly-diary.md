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
| 4 | 28 Sep -- 04 Oct 2026 | Literature survey and project documentation | in progress | 358 |

**Current scale:** 11,118 lines of Python across implementation and tests,
358 automated tests passing, 9 commits.

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

### Planned for next week

- Score motif detection against the Lichess puzzle database (CC0,
  human-tagged themes) for precision and recall per motif.
- Measure classification agreement against Lichess's own annotations.
- Draft the listener-study protocol and register thresholds before running
  it.
- Begin recruiting listeners -- identified as the schedule's long pole.

---

## Outstanding work

| Item | Objective | Notes |
|---|---|---|
| Motif scoring against the Lichess puzzle database | 5 | Free labelled ground truth; yields a precision/recall table |
| Classification agreement with Lichess annotations | 5 | Comparable because the same win-probability mapping is used |
| Listener study | 5 | Long pole; both baselines already implemented |
| Verification gate for chess claims | 3 | Extends the existing grounding checks |
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
