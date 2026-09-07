# Stage 2 Baseline -- Live API Commentary Quality
Generated: 2026-05-21  
Model: claude-sonnet-4-6  
Mode: live Anthropic API (not mock)  
Matches: 4 (demo_t20_001, eval_collapse_001, eval_chase_001, eval_last_over_001)  
Total ball events: 70  
Total flags: 20

This is the official baseline that Part B prompt improvements must beat.

---

## Aggregate Results

| Check                      | Flags | Ball events | Rate  |
|----------------------------|------:|------------:|------:|
| repeated_opening           |     5 |          70 |  7.1% |
| phrase_repetition          |     4 |          70 |  5.7% |
| length_violation           |     3 |          70 |  4.3% |
| grounding_number           |     7 |          70 | 10.0% |
| grounding_shot             |     1 |          70 |  1.4% |
| **TOTAL**                  |**20** |      **70** |**28.6% of events have >= 1 flag** |

---

## Per-Match Breakdown

| Match              | Events | Balls | Rep.Open | Phr.Rep | Length | Ungrd# | Ungrd.Shot | Total |
|--------------------|-------:|------:|---------:|--------:|-------:|-------:|-----------:|------:|
| demo_t20_001       |     29 |    21 |    0 (0%)|  0 (0%) |  0 (0%)|  2 (10%)|   1 (5%) |     3 |
| eval_collapse_001  |     24 |    14 |    1 (7%)|  1 (7%) |  1 (7%)|  0 (0%) |   0 (0%) |     3 |
| eval_chase_001     |     26 |    16 |   2 (12%)|  2 (12%)|  0 (0%)|  1 (6%) |   0 (0%) |     5 |
| eval_last_over_001 |     32 |    19 |   2 (11%)|  1 (5%) | 2 (11%)| 4 (21%) |   0 (0%) |     9 |

---

## Flag-by-Flag Audit (what actually triggered, and whether it's genuine)

### repeated_opening (5 flags, 7.1%)

All genuine. The model repeated sentence-opening phrases across closely spaced ball events:

- **eval_collapse** [009]: "Collins strikes" -- same as [007]. Two LBW/wicket events described identically.
- **eval_chase** [008]: "Bowled him!" -- same as [003]. Then again at [009]: "Bowled him!" -- same as [008]. Three wickets in a row all opened the same way.
- **eval_last_over** [023]: "Torres strikes" -- same as [017]. Two consecutive Torres wicket balls.
- **eval_last_over** [028]: "Priya Singh" -- same as [027]. Consecutive balls to the same batter, same name opens both lines.

**Pattern:** Wickets and boundaries trigger repeated openers more than dot balls. "Bowled him!" / "Collins/Torres strikes" are the most common offenders.

---

### phrase_repetition (4 flags, 5.7%)

All genuine. The model reused 4-gram phrases within a 5-ball window:

- **eval_collapse** [017]: "down for and this" -- also in [013]. Both wicket balls described collapsing innings with "X down for Y, and this innings..."
- **eval_chase** [008]: "duck the eagles are" -- also in [003]. "Eagles are N down" phrasing reused.
- **eval_chase** [019]: "midwicket boundary for six" -- also in [013]. Slog sweep six described identically twice in 6 balls.
- **eval_last_over** [024]: "down needed off balls" -- also in [022]. Chase commentary template "X down, Y needed off Z balls" repeated.

**Pattern:** The model has structural templates for (a) collapse commentary and (b) chase-math summaries that surface repeatedly within a few balls.

---

### length_violation (3 flags, 4.3%)

All genuine -- ball-level commentary exceeded 2 sentences:

- **eval_collapse** [013]: Stumping described in 3 sentences. Third sentence: "Five down for 48, and this innings is disintegrating by the ball."
- **eval_last_over** [004]: Wicket described in 3 sentences. Third sentence: "Titans lose their first wicket and Mukherjee strikes at a crucial moment!"
- **eval_last_over** [017]: Wicket described in 3 sentences. Third sentence: "That's a huge blow as they chase 182."

**Pattern:** Wicket events consistently trigger a third sentence of "context-setting" that the model adds even though the limit is 2. Non-wicket ball events produced zero length violations.

---

### grounding_number (7 flags, 10.0%)

Mixed. Approximately half are false positives (correct cricket math that the checker can't verify); the rest are genuine hallucinations.

**Genuine hallucinations (wrong number):**
- **demo_t20_001** [023]: Commentary says "formidable 186" -- actual score at end of innings was 182. Off by 4.
- **eval_chase** [025]: Commentary says "9 needed from 22 balls" -- `balls_remaining` in event data is 52. "22 balls" is fabricated.
- **eval_last_over** [024]: Commentary says "28 needed off 7 balls" -- `balls_remaining` is 13. "7 balls" is fabricated.

**False positives (correct cricket math, not in known_nums):**
The checker does not compute `target - score` (runs needed). So any time the model correctly states runs needed (e.g. "179 still needed", "52 needed off 33", "35 needed off 22", "15 needed off five"), the runs figure is flagged as ungrounded even though it equals `target - score` exactly.

- **demo_t20_001** [028]: "179 still needed" = 183 - 4. Correct. False positive.
- **eval_last_over** [022]: "52 needed off 33 balls" -- 52 = 182 - 130. Correct. False positive.
- **eval_last_over** [023]: "35 needed off 22 balls" -- 35 = 182 - 147. Correct. False positive.
- **eval_last_over** [027]: "15 needed off five" -- 15 = 182 - 167. Correct. False positive.

**Checker improvement opportunity (not a prompt issue):** Add `target - score` to `known_nums` to eliminate the false-positive category. This would reduce grounding_number from 7 flags to 3.

---

### grounding_shot (1 flag, 1.4%)

- **demo_t20_001** [015]: "Pushed into the leg side" for a 1-run event with no shot_tag. Borderline -- "pushed" is a common transition verb, not necessarily an invented shot type.

---

## Unchecked issues observed in transcripts (not measured by any check)

These appeared in the transcripts but are NOT captured by the current heuristics. They are documented here for awareness, not as Part B targets -- we have no rate data on them.

1. **Team-name hallucination** (eval_last_over [032]): "INDIA WIN off the last ball" -- the teams are Titans vs Raiders. The model substituted a real-world team name. One occurrence, unchecked.

2. **Over-start commentary length**: Over-start events produced multi-sentence blocks (4-6 sentences each). The length check only applies to ball events. These are not flagged. Whether this is a problem requires a human read.

3. **Innings-start commentary length**: Same as above -- multi-sentence, not checked.

---

## What Part B must improve (measured targets only)

To be counted as an improvement, a Part B prompt change must reduce rates below these baselines on the same 4 matches using the same model:

| Target check         | Baseline rate | Note |
|----------------------|:-------------:|------|
| repeated_opening     | 7.1%          |      |
| phrase_repetition    | 5.7%          |      |
| length_violation     | 4.3%          | wicket events only in practice |
| grounding_number     | 10.0% (raw) / 4.3% (genuine-only) | see false-positive note above |
| grounding_shot       | 1.4%          | too few instances for significance |

Transcripts for human review: `output/eval_live/`
