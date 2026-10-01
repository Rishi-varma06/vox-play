"""
Scoring the motif detectors against human-tagged puzzles.

The detectors in motifs.py are hand-written rules. Asserting that they work
is worth nothing; the Lichess puzzle database is millions of positions whose
tactical themes were tagged by people, released CC0, and its theme names are
the ones this project's Motif enum already uses. That makes it a direct
comparison rather than a mapping exercise.

A note on what can honestly be measured
---------------------------------------
Recall is clean. Of the puzzles a human tagged "fork", how many does the fork
detector fire on? A miss is unambiguously a miss.

Precision is not. Lichess tags the theme that makes a puzzle *interesting*,
not every geometric feature present on the board. A puzzle tagged only
"backRankMate" may well also contain a genuine pin, so a detector firing
"pin" there is not necessarily wrong. Treating every untagged detection as a
false positive would therefore understate the detectors and produce a number
that looks rigorous while meaning little.

So this module reports recall on positives and, separately, a **false
positive rate against a control sample** -- puzzles drawn from other themes,
where the motif in question is not what the puzzle is about. That pair says
what matters: does the detector find the thing, and does it fire everywhere
regardless. Both numbers go in the report with this caveat attached.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

import chess
from pydantic import BaseModel, Field

from .models import Motif
from .motifs import detect_motifs

PUZZLE_API = "https://lichess.org/api/puzzle/next"

#: Lichess asks API clients to identify themselves and be considerate.
USER_AGENT = "voxplay/0.1 (university capstone; motif detector validation)"

#: Seconds between requests. Measured, not guessed: 0.7s earned a stream of
#: HTTP 429s from this endpoint. Lichess publishes this data for free and
#: asks clients to be considerate, so the default errs slow.
REQUEST_DELAY = 2.0

#: How long to wait after a 429. Lichess's API guidance is to stop for a full
#: minute rather than retry tighter, and a rate-limited request must not count
#: against the attempt budget or a single throttle burst ends the sampling.
RATE_LIMIT_BACKOFF = 62.0

#: Themes this project detects AND Lichess tags, so they can be compared
#: directly. Lichess themes we do not attempt (attraction, deflection,
#: interference, zugzwang, xRayAttack) are out of scope, not failures.
SCORABLE: Dict[Motif, str] = {
    Motif.FORK: "fork",
    Motif.PIN: "pin",
    Motif.SKEWER: "skewer",
    Motif.DISCOVERED_ATTACK: "discoveredAttack",
    Motif.DOUBLE_CHECK: "doubleCheck",
    Motif.HANGING_PIECE: "hangingPiece",
    Motif.SACRIFICE: "sacrifice",
    Motif.BACK_RANK_MATE: "backRankMate",
    Motif.SMOTHERED_MATE: "smotheredMate",
    Motif.PROMOTION: "promotion",
    Motif.EN_PASSANT: "enPassant",
    Motif.ADVANCED_PAWN: "advancedPawn",
}

#: Drawn from for control samples. Chosen because they are common and are not
#: themselves any of the motifs above, so a detector firing on them is
#: evidence of over-firing rather than an untagged true positive.
CONTROL_THEMES = ("mateIn2", "endgame", "middlegame", "crushing", "advantage")


class ValidationError(RuntimeError):
    """Raised when puzzles cannot be obtained or scored."""


class Puzzle(BaseModel):
    """One Lichess puzzle: the position, the move, and the human tags."""
    id: str
    fen: str
    solution: List[str] = Field(default_factory=list)
    themes: List[str] = Field(default_factory=list)
    rating: int = 0

    @property
    def move(self) -> Optional[chess.Move]:
        """The move the solver is expected to play."""
        if not self.solution:
            return None
        try:
            return chess.Move.from_uci(self.solution[0])
        except ValueError:
            return None


#: Themes whose Lichess definition does not ask the question our detector
#: answers, so a recall figure would be a category error rather than a
#: measurement. Excluded from the headline mean and marked in the table.
#:
#: hangingPiece: Lichess tags a puzzle this way when the SOLUTION TAKES an
#: undefended piece. We detect a move that LEAVES one hanging, which by
#: construction cannot include the piece just captured. Measured on 1000
#: tagged puzzles: 100% have a piece already hanging before the move and 100%
#: of solutions capture an undefended one.
#:
#: Detecting captures-of-hanging instead was tried and reverted. It took
#: recall to 100% but control firing to 56.9%, and on a real game it labelled
#: an ordinary recapture and the acceptance of a famous queen sacrifice as
#: pieces "left undefended". Separating a won piece from a recapture or an
#: accepted sacrifice needs move history, which neither a detector nor a
#: puzzle FEN carries. The commentary-relevant question is the one we keep.
NOT_COMPARABLE: Dict[Motif, str] = {
    Motif.HANGING_PIECE: "we detect leaving a piece hanging, not taking one",
}

#: Below this many tagged puzzles a recall figure is not worth printing. A
#: first run collected zero puzzles for ten of twelve themes and the table
#: dutifully reported "0.0% recall" for every one of them, which reads as a
#: detector that never fires rather than a sample that never arrived. A number
#: that looks like a measurement but is not is worse than a blank.
MIN_SAMPLE = 20


class ThemeScore(BaseModel):
    """How one detector did against one human-tagged theme."""
    motif: Motif
    theme: str
    positives: int = 0
    detected_in_positives: int = 0
    controls: int = 0
    fired_in_controls: int = 0

    @property
    def comparable(self) -> bool:
        """False when the theme does not ask what this detector answers."""
        return self.motif not in NOT_COMPARABLE

    @property
    def measured(self) -> bool:
        """True when enough puzzles arrived for the figures to mean anything."""
        return self.positives >= MIN_SAMPLE and self.comparable

    @property
    def recall(self) -> Optional[float]:
        if not self.positives:
            return None
        return self.detected_in_positives / self.positives

    @property
    def false_positive_rate(self) -> Optional[float]:
        if not self.controls:
            return None
        return self.fired_in_controls / self.controls


class ValidationReport(BaseModel):
    """Every detector's score, plus the corpus it was measured on."""
    scores: List[ThemeScore] = Field(default_factory=list)
    puzzles_used: int = 0

    @property
    def usable(self) -> List[ThemeScore]:
        return [s for s in self.scores if s.measured]

    def table(self) -> str:
        """Fixed-width summary, ASCII only for the project's terminal."""

        def pct(value: Optional[float]) -> str:
            return "     -" if value is None else "%5.1f%%" % (100 * value)

        lines = [
            "motif              theme              n   recall    control  fired",
            "-" * 68,
        ]
        ordered = sorted(
            self.scores, key=lambda s: (not s.measured, -(s.recall or 0.0))
        )
        for score in ordered:
            if not score.comparable:
                note = "   (not comparable: %s)" % NOT_COMPARABLE[score.motif]
            elif not score.measured:
                note = "   (sample too small)"
            else:
                note = ""
            lines.append(
                "%-18s %-16s %4d   %s    %5d  %s%s"
                % (score.motif.value, score.theme, score.positives,
                   pct(score.recall), score.controls,
                   pct(score.false_positive_rate), note)
            )

        lines.append("-" * 68)
        usable = self.usable
        if usable:
            mean = sum(s.recall for s in usable) / len(usable)
            lines.append(
                "%-18s %-16s %4d   %5.1f%%   (%d of %d themes had n >= %d)"
                % ("MEAN", "", self.puzzles_used, 100 * mean,
                   len(usable), len(self.scores), MIN_SAMPLE)
            )
        else:
            lines.append(
                "NO USABLE RESULT: no theme reached n >= %d. The corpus is too "
                "small to\nmeasure anything; collect more puzzles before "
                "reporting these figures." % MIN_SAMPLE
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def puzzle_fen(raw: Dict, game: Optional[Dict]) -> Optional[str]:
    """
    The position the solver actually faces.

    /api/puzzle/daily hands back `fen` directly, but /api/puzzle/next -- the
    only endpoint that filters by theme, and therefore the one this module
    needs -- does not. It gives the game's moves and `initialPly` instead.

    `initialPly` is the zero-based index of the opponent's move that sets the
    puzzle up, so the position is the one after replaying initialPly + 1
    half-moves. Verified against the daily puzzle, which supplies both: the
    board and side to move agree at ply + 1 and not at ply, and solution[0] is
    legal there. (The FEN strings themselves differ because Lichess normalises
    the halfmove clock and move number, so compare boards, not strings.)
    """
    if raw.get("fen"):
        return raw["fen"]

    pgn = (game or {}).get("pgn")
    initial_ply = raw.get("initialPly")
    if not pgn or initial_ply is None:
        return None

    board = chess.Board()
    for san in pgn.split()[: int(initial_ply) + 1]:
        try:
            board.push_san(san)
        except ValueError:
            return None
    return board.fen()


def _cache_path(cache_dir: Path, theme: str) -> Path:
    return Path(cache_dir) / f"puzzles_{theme}.json"


def load_cached(cache_dir: Path, theme: str) -> List[Puzzle]:
    path = _cache_path(Path(cache_dir), theme)
    if not path.exists():
        return []
    try:
        return [Puzzle(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
    except Exception:  # noqa: BLE001 - a corrupt cache is not fatal
        return []


def _save_cache(cache_dir: Path, theme: str, puzzles: Sequence[Puzzle]) -> None:
    path = _cache_path(Path(cache_dir), theme)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([p.model_dump() for p in puzzles], indent=1), encoding="utf-8"
    )


def fetch_puzzles(
    theme: str,
    count: int,
    cache_dir: Path | str = "data/puzzles",
    difficulty: str = "normal",
    delay: float = REQUEST_DELAY,
    progress: bool = False,
) -> List[Puzzle]:
    """
    Collect `count` distinct puzzles tagged `theme`, caching to disk.

    The endpoint returns one random puzzle per call, so duplicates arrive and
    are discarded. Everything already cached counts toward the target, which
    means a second run costs nothing and the corpus grows across runs.
    """
    import requests

    cache_dir = Path(cache_dir)
    puzzles = load_cached(cache_dir, theme)
    seen = {p.id for p in puzzles}

    attempts = 0
    budget = count * 4 + 20  # random sampling repeats; do not spin forever
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    consecutive_failures = 0
    while len(puzzles) < count and attempts < budget:
        attempts += 1
        try:
            response = session.get(
                PUZZLE_API, params={"angle": theme, "difficulty": difficulty}, timeout=20
            )
            if response.status_code == 429:
                # Back off hard and do not spend an attempt on it.
                attempts -= 1
                if progress:
                    print(f"      {theme}: rate limited, pausing "
                          f"{RATE_LIMIT_BACKOFF:.0f}s")
                time.sleep(RATE_LIMIT_BACKOFF)
                continue
            response.raise_for_status()
            payload = response.json()
            raw = payload["puzzle"]
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001 - one bad call is not fatal
            consecutive_failures += 1
            if progress:
                print(f"      {theme}: request failed ({type(exc).__name__})")
            if consecutive_failures >= 5:
                # The service is unhappy or unreachable; stop rather than
                # keep knocking. Whatever is cached is still usable.
                if progress:
                    print(f"      {theme}: giving up after repeated failures")
                break
            time.sleep(delay * 3)
            continue

        if raw["id"] not in seen:
            fen = puzzle_fen(raw, payload.get("game"))
            if fen is None:
                time.sleep(delay)
                continue
            seen.add(raw["id"])
            puzzles.append(Puzzle(
                id=raw["id"], fen=fen, solution=raw.get("solution", []),
                themes=raw.get("themes", []), rating=raw.get("rating", 0),
            ))
            if progress and len(puzzles) % 10 == 0:
                print(f"      {theme}: {len(puzzles)}/{count}")
        time.sleep(delay)

    _save_cache(cache_dir, theme, puzzles)
    return puzzles[:count]


# ---------------------------------------------------------------------------
# The full database
# ---------------------------------------------------------------------------

DATABASE_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"

#: Fixed so a validation run is reproducible. Reservoir sampling with a
#: pinned seed gives an unbiased sample that is also the same sample twice.
SAMPLE_SEED = 20260101


def puzzle_from_row(row: Dict[str, str]) -> Optional[Puzzle]:
    """
    One CSV row to a Puzzle, in the position the solver actually faces.

    The CSV convention differs from the API's, and getting it wrong scores
    every detector against the wrong board. Here `FEN` is the position BEFORE
    the opponent's move: Moves[0] is that move, and Moves[1] is the first move
    of the solution. So the puzzle position is reached by pushing Moves[0],
    and the move under test is Moves[1].
    """
    moves = (row.get("Moves") or "").split()
    if len(moves) < 2:
        return None
    try:
        board = chess.Board(row["FEN"])
        setup = chess.Move.from_uci(moves[0])
        if setup not in board.legal_moves:
            return None
        board.push(setup)
    except (ValueError, KeyError):
        return None

    try:
        rating = int(row.get("Rating") or 0)
    except ValueError:
        rating = 0

    return Puzzle(
        id=row.get("PuzzleId", ""),
        fen=board.fen(),
        solution=moves[1:],
        themes=(row.get("Themes") or "").split(),
        rating=rating,
    )


def stream_rows(archive: Path | str) -> Iterable[Dict[str, str]]:
    """
    Raw CSV rows from the compressed archive, decompressed on the fly.

    Streamed rather than extracted: the archive is about 290MB compressed and
    several times that on disk, and a single pass is all this needs. Rows are
    handed back unparsed because building a board for all five million of
    them, when a few thousand will be sampled, is most of the runtime.
    """
    import csv
    import io

    import zstandard

    archive = Path(archive)
    if not archive.exists():
        raise ValidationError(
            f"puzzle database not found at {archive}. Download it from "
            f"{DATABASE_URL} (about 290MB, CC0)."
        )

    decompressor = zstandard.ZstdDecompressor()
    with archive.open("rb") as raw, decompressor.stream_reader(raw) as stream:
        text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
        yield from csv.DictReader(text)


def stream_database(archive: Path | str) -> Iterable[Puzzle]:
    """Every puzzle in the archive, as parsed Puzzle objects."""
    for row in stream_rows(archive):
        puzzle = puzzle_from_row(row)
        if puzzle is not None:
            yield puzzle


def sample_database(
    archive: Path | str,
    themes: Sequence[str],
    per_theme: int,
    control_themes: Sequence[str] = CONTROL_THEMES,
    controls: int = 500,
    max_rows: Optional[int] = None,
    progress: bool = True,
) -> tuple[Dict[str, List[Puzzle]], List[Puzzle]]:
    """
    Reservoir-sample every requested theme in a single pass over the archive.

    One pass for all themes at once -- rescanning five million rows per theme
    would turn minutes into an hour. Reservoir sampling draws uniformly from
    the whole file rather than taking whichever puzzles happen to come first,
    and the fixed seed makes the draw repeatable.
    """
    import random

    rng = random.Random(SAMPLE_SEED)
    wanted = set(themes)
    control_set = set(control_themes)

    # Reservoirs hold raw rows. Boards are built only for what survives the
    # sampling, turning five million board constructions into a few thousand.
    reservoirs: Dict[str, List[Dict[str, str]]] = {theme: [] for theme in themes}
    seen_counts: Dict[str, int] = {theme: 0 for theme in themes}
    control_rows: List[Dict[str, str]] = []
    control_seen = 0

    for index, row in enumerate(stream_rows(archive)):
        if max_rows is not None and index >= max_rows:
            break
        if progress and index and index % 1_000_000 == 0:
            print(f"      scanned {index:,} rows")

        tags = set((row.get("Themes") or "").split())

        for theme in wanted & tags:
            seen_counts[theme] += 1
            bucket = reservoirs[theme]
            if len(bucket) < per_theme:
                bucket.append(row)
            else:
                slot = rng.randrange(seen_counts[theme])
                if slot < per_theme:
                    bucket[slot] = row

        if tags & control_set and not (tags & wanted):
            # A control must not be about any motif under test, or it is not
            # a control -- score_theme filters per theme, this filters broadly.
            control_seen += 1
            if len(control_rows) < controls:
                control_rows.append(row)
            else:
                slot = rng.randrange(control_seen)
                if slot < controls:
                    control_rows[slot] = row

    def build(rows: Iterable[Dict[str, str]]) -> List[Puzzle]:
        return [p for p in (puzzle_from_row(r) for r in rows) if p is not None]

    sampled = {theme: build(rows) for theme, rows in reservoirs.items()}
    control_pool = build(control_rows)

    if progress:
        print(f"      drew from {sum(seen_counts.values()):,} theme-tagged puzzles")
    return sampled, control_pool


def run_validation_from_database(
    archive: Path | str,
    motifs: Optional[Sequence[Motif]] = None,
    per_theme: int = 1000,
    controls: int = 1000,
    max_rows: Optional[int] = None,
    progress: bool = True,
) -> ValidationReport:
    """Score every detector against a sample drawn from the full database."""
    targets = [m for m in (motifs or list(SCORABLE)) if m in SCORABLE]
    themes = [SCORABLE[m] for m in targets]

    if progress:
        print(f"  Scanning the puzzle database for {len(themes)} themes ...")
    sampled, control_pool = sample_database(
        archive, themes, per_theme, controls=controls,
        max_rows=max_rows, progress=progress,
    )

    report = ValidationReport()
    for motif in targets:
        theme = SCORABLE[motif]
        report.scores.append(
            score_theme(motif, theme, sampled.get(theme, []), control_pool)
        )
    report.puzzles_used = sum(s.positives for s in report.scores) + len(control_pool)
    return report


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def detected_motifs(puzzle: Puzzle, whole_line: bool = True) -> Set[Motif]:
    """
    Motifs our detectors find in the puzzle's solution.

    `whole_line` is the difference between measuring the detectors and
    measuring an artefact of the comparison. A Lichess theme describes the
    whole solution, not its first move: a puzzle tagged "promotion" may
    promote on the third move of the line, and one tagged "backRankMate" is
    usually named for how the line ENDS. Scoring only the first move gave
    promotion 10.5% recall, which is impossible for a detector whose entire
    test is `move.promotion is not None` -- the detector was right and the
    question was wrong.

    So the line is walked and the motifs from every move the SOLVER makes are
    unioned. The opponent's replies are played but not scored; they are not
    the player's tactics.

    Note this is the validation question, not the commentary one. Commentary
    asks what a single move created, which is what detect_motifs answers and
    what `whole_line=False` reproduces.

    A puzzle whose position or moves will not parse is skipped rather than
    counted against the detectors -- that is a data problem, not a miss.
    """
    try:
        board = chess.Board(puzzle.fen)
    except ValueError:
        return set()

    found: Set[Motif] = set()
    for index, uci in enumerate(puzzle.solution):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        if index % 2 == 0:  # the solver's move; odd indices are the reply
            found.update(hit.motif for hit in detect_motifs(board, move))
        board.push(move)
        if not whole_line:
            break
    return found


def score_theme(
    motif: Motif,
    theme: str,
    positives: Iterable[Puzzle],
    controls: Iterable[Puzzle],
) -> ThemeScore:
    """Recall on puzzles tagged `theme`, and firing rate on puzzles that are not."""
    score = ThemeScore(motif=motif, theme=theme)

    for puzzle in positives:
        score.positives += 1
        if motif in detected_motifs(puzzle):
            score.detected_in_positives += 1

    for puzzle in controls:
        if theme in puzzle.themes:
            continue  # not a control for this motif after all
        score.controls += 1
        if motif in detected_motifs(puzzle):
            score.fired_in_controls += 1

    return score


def run_validation(
    motifs: Optional[Sequence[Motif]] = None,
    count: int = 40,
    controls: int = 60,
    cache_dir: Path | str = "data/puzzles",
    delay: float = REQUEST_DELAY,
    progress: bool = True,
) -> ValidationReport:
    """Fetch a corpus and score every scorable detector against it."""
    targets = list(motifs) if motifs else list(SCORABLE)

    if progress:
        print(f"  Building control sample ({controls} puzzles) ...")
    control_pool: List[Puzzle] = []
    per_theme = max(1, controls // len(CONTROL_THEMES))
    for theme in CONTROL_THEMES:
        control_pool.extend(
            fetch_puzzles(theme, per_theme, cache_dir, delay=delay, progress=progress)
        )

    report = ValidationReport()
    for motif in targets:
        theme = SCORABLE.get(motif)
        if theme is None:
            continue
        if progress:
            print(f"  Sampling '{theme}' ({count} puzzles) ...")
        positives = fetch_puzzles(theme, count, cache_dir, delay=delay, progress=progress)
        report.scores.append(score_theme(motif, theme, positives, control_pool))

    report.puzzles_used = sum(s.positives for s in report.scores) + len(control_pool)
    return report
