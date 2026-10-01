# Voxplay -- Listener Study Protocol

Pre-registered design for the human evaluation required by Objective 5.

**Status: pre-registration. Written and committed BEFORE any data is
collected.** Everything below -- hypotheses, thresholds, sample size, and the
statistical tests -- is fixed at the commit that introduces this file. Any
change after data collection begins must be recorded as an amendment, dated,
with the reason, rather than edited silently. That is the whole point of
writing it down first: a threshold chosen after seeing the data is not a
threshold.

---

## 1. What this study is for

Three claims in this project cannot be settled by measurement against a
database. They are claims about what a person experiences:

1. That **pacing** -- selecting and budgeting moments rather than narrating
   every move -- is what makes commentary engaging rather than mechanical.
2. That commentary built only from engine data is **as informative** as
   commentary a person wrote.
3. That the **language model earns its place** over correct facts delivered
   plainly.

Everything else about the system is already measured: motif recall 91.6%,
classification agreement with Lichess kappa 0.932, zero verified
hallucinations on grounded output. None of that says whether anyone wants to
listen.

---

## 2. Conditions

All four arms are implemented and produce the same shape of output, so a
listener cannot tell them apart by format. Each is generated from the same
game and rendered with the same voice.

| Arm | What it is | Produced by |
|---|---|---|
| **A. Flat** | one spoken line per half-move, no selection | `select_every_move()` |
| **B. Top-N** | the N largest evaluation swings, no spacing or coverage rules | `select_top_n()` |
| **C. System** | salience scoring under a duration budget, with coverage and spacing | `select_beats()` |
| **D. Template** | the system's pacing, but sentences assembled mechanically from the facts instead of written by a model | `TemplateGenerator` |
| **E. Human** | commentary written by a strong club player on the same game | recruited annotator |

**A and B isolate pacing** (B has scoring but no constraints; A has neither).
**D isolates the language model** -- it has identical pacing and identical
facts, and differs only in that nothing wrote the prose. **E is the ceiling.**

A full five-arm design is expensive. If recruitment is tight, drop **B**
first: A already establishes the naive baseline, and C-vs-A is the primary
pacing contrast. Do not drop D -- without it the study cannot say whether the
model contributed anything beyond correct facts.

---

## 3. Hypotheses and pre-registered thresholds

Stated as directional predictions with the threshold that counts as support.
A result that misses its threshold is reported as a failure to support, not
reinterpreted.

| ID | Hypothesis | Supported if |
|---|---|---|
| **H1** | Pacing improves engagement | C rated higher than A on *engagement*, median difference >= 1.0 Likert point, Wilcoxon p < 0.05 after correction |
| **H2** | Constraints matter, not just scoring | C rated higher than B on *pacing*, median difference >= 0.5, p < 0.05 |
| **H3** | Engine-derived commentary is near the human ceiling | C within **0.5 Likert points** of E on *informativeness* (the interval for the difference must include values below 0.5) |
| **H4** | The model earns its cost | C rated higher than D on *engagement*, median difference >= 0.5, p < 0.05 |
| **H5** | Grounding holds up to listeners | C rated no lower than E on *perceived accuracy* |
| **H6** | Cost | under **$0.10** and under **60 seconds** of compute per game, measured not estimated |

H3 is deliberately a *non-inferiority* claim rather than "as good as". The
original project hypothesis -- that this is as informative as commentary that
took a human hours -- is not testable as stated, because "as informative" has
no threshold and no matched corpus exists. This is the defensible version of
the same claim.

**H6 is settled, before the study runs.** Measured from API usage across five
games: **$0.057 per game** and **23 seconds** of model time, against
thresholds of $0.10 and 60 seconds. With the verification gate enabled it is
$0.062. Engine analysis adds 23-37 seconds single-threaded, and the offline
voice costs nothing per game. H6 is supported and needs no listeners; the
remaining hypotheses are the ones only listeners can settle.

---

## 4. Materials

**Six games**, chosen before listening, stratified so results are not an
artefact of one kind of game:

| | Archetype | Rating band |
|---|---|---|
| 1 | miniature | under 1200 |
| 2 | collapse | 1200-1600 |
| 3 | comeback | 1200-1600 |
| 4 | smooth conversion | 1600-2000 |
| 5 | slugfest | 1600-2000 |
| 6 | grind / endgame | 2000+ |

Archetypes come from `extract_arc()`. Games are drawn from the Lichess
database, filtered to analysed games, and **selected by archetype and rating
only** -- never by listening to the commentary first, which would select for
flattering output.

Record each game's id in an appendix so the study is reproducible.

### The human arm

Recruit **one strong club player (1800+)** to write spoken-style commentary
for all six games, paid or credited. Give them the PGN and the engine
analysis -- the same information the system has -- but no system output, so
they are not anchored by it.

Do not use published annotations from books. They are copyrighted, they are
written to be read rather than heard, and their authors were not working
under the same constraints. A recruited annotator is cheaper, cleaner, and
comparable.

---

## 5. Participants

**Target n = 30**, minimum 24.

Within-subjects: every listener hears every condition, on different games.
This is the right design here because it removes between-person variation in
how people use rating scales, which is large and would otherwise swamp a
0.5-point effect. It also reaches usable power at a sample size a student
project can actually recruit; the same study between-subjects would need
several times as many people.

**Stratify by chess strength**, because it plausibly moderates everything:

| Band | Target |
|---|---|
| Beginner (never played seriously / under 1000) | 10 |
| Casual (1000-1600) | 10 |
| Club (1600+) | 10 |

Recruit from the college chess club, the department, and a campus-wide
callout. Screen with two questions: self-reported rating or "never played",
and whether they have watched chess commentary before.

**Exclude** anyone who has worked on this project or heard its output before.

---

## 6. Procedure

Each participant completes one session of roughly 25 minutes.

1. **Consent** (below), then the two screening questions.
2. **Practice item.** One short clip from a game not in the set, to settle
   them into the rating scale. Not analysed.
3. **Four rated clips.** Each is a different game in a different condition,
   assigned by **Latin square** so that across participants every condition
   appears equally often in every position and on every game. This matters:
   without it, order effects and game difficulty confound with condition.
4. After each clip, the rating block (section 7). Ratings are given
   immediately, before the next clip.
5. **Forced choice** at the end: of the four heard, which would you choose to
   listen to again? One sentence on why.
6. **Debrief.**

Clips are delivered as the project's own HTML player, so every condition is
heard with the same voice, the same board, and the same visual framing.
Participants are not told which condition is which, and conditions are never
named in their hearing.

---

## 7. Measures

Five items per clip, 5-point Likert (1 = strongly disagree, 5 = strongly
agree). Worded as statements about the listener's experience, not judgements
of the system, and never mentioning computers or automation.

| Item | Statement |
|---|---|
| **Informativeness** | "I understood what happened in this game." |
| **Engagement** | "I wanted to keep listening." |
| **Perceived accuracy** | "What the commentary said seemed correct." |
| **Pacing** | "The commentary spent time on the right moments." |
| **Naturalness** | "It sounded like a person talking, not a report being read." |

Plus one free-text box: "Anything that stood out, good or bad?"

**Do not ask "was this AI generated?"** It changes how people rate everything
after it, and the answer is not a research question here.

---

## 8. Analysis plan

Fixed in advance.

- **Primary tests.** Likert data is ordinal and the design is repeated
  measures, so: **Friedman test** across conditions per item, and where it is
  significant, **Wilcoxon signed-rank** for the specific pairs named in
  section 3.
- **Multiple comparisons.** Holm-Bonferroni correction across the planned
  comparisons. Only the comparisons in the hypothesis table are planned;
  anything else found is exploratory and labelled as such.
- **Effect size.** Report the median difference and a bootstrap 95%
  confidence interval alongside every p-value. With n = 30 the p-value alone
  is not informative.
- **Non-inferiority (H3).** Report the confidence interval for C minus E on
  informativeness and compare it to the 0.5-point margin. Do not use a
  significance test for equivalence.
- **Moderation.** Report ratings split by strength band descriptively.
  Testing for an interaction at n = 10 per band is underpowered, and will be
  presented as a direction for future work rather than a result.
- **Missing data.** A participant who abandons the session mid-way is
  excluded from paired tests but reported in the participant count.

### What a negative result looks like

If C does not beat A on engagement, the pacing hypothesis is not supported,
and that is the finding. It is a real result about a real system and it goes
in the report with the same prominence as a positive one. The system's
measured properties -- recall, agreement, hallucination rate, cost -- stand
regardless, because they were measured against ground truth rather than
opinion.

---

## 9. Ethics and consent

Low-risk: adults listening to chess commentary and rating it.

- **Informed consent** before anything else, covering what the study is, how
  long it takes, that participation is voluntary, and that they may stop at
  any point without giving a reason.
- **Data collected:** ratings, free-text comments, self-reported chess
  strength, and whether they have watched chess commentary before. Nothing
  else. **No names, no email addresses, no contact details** stored with
  responses; participants get a code.
- **Storage:** responses in a single spreadsheet, no identifiers, retained
  until the project is marked and then deleted.
- **Withdrawal:** a participant may withdraw during the session. Because
  responses are anonymous, withdrawal after submission is not possible, and
  the consent text must say so.
- Follow the department's ethics process. If approval is required, obtain it
  before recruiting, not before analysing.

---

## 10. Why this is the schedule's long pole

Every other part of Objective 5 was a measurement against data that already
existed and took an afternoon each. This one needs 30 people to agree to
spend 25 minutes, scheduled around their lives, plus an annotator for the
human arm, plus ethics clearance if required. None of that compresses.

**Start recruiting before the materials are finalised.** The clips can be
generated in an hour once the games are chosen; the people cannot.

---

## Appendix A -- selected games

*To be completed before the first session, and not changed afterwards.*

| | Lichess id | Archetype | Rating band | Half-moves |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |

## Appendix B -- amendments

*Any deviation from this protocol after data collection begins, with the date
and the reason. Empty is the expected state.*

| Date | Change | Reason |
|---|---|---|
| | | |
