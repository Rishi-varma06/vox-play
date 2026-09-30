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


class ThemeScore(BaseModel):
    """How one detector did against one human-tagged theme."""
    motif: Motif
    theme: str
    positives: int = 0
    detected_in_positives: int = 0
    controls: int = 0
    fired_in_controls: int = 0

    @property
    def recall(self) -> float:
        return self.detected_in_positives / self.positives if self.positives else 0.0

    @property
    def false_positive_rate(self) -> float:
        return self.fired_in_controls / self.controls if self.controls else 0.0


class ValidationReport(BaseModel):
    """Every detector's score, plus the corpus it was measured on."""
    scores: List[ThemeScore] = Field(default_factory=list)
    puzzles_used: int = 0

    def table(self) -> str:
        """Fixed-width summary, ASCII only for the project's terminal."""
        lines = [
            "motif              theme              n   recall    control  fired",
            "-" * 68,
        ]
        for score in sorted(self.scores, key=lambda s: -s.recall):
            lines.append(
                "%-18s %-16s %4d   %5.1f%%    %5d  %5.1f%%"
                % (score.motif.value, score.theme, score.positives,
                   100 * score.recall, score.controls,
                   100 * score.false_positive_rate)
            )
        scored = [s for s in self.scores if s.positives]
        if scored:
            mean = sum(s.recall for s in scored) / len(scored)
            lines.append("-" * 68)
            lines.append("%-18s %-16s %4d   %5.1f%%"
                         % ("MEAN", "", self.puzzles_used, 100 * mean))
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
# Scoring
# ---------------------------------------------------------------------------

def detected_motifs(puzzle: Puzzle) -> Set[Motif]:
    """
    Motifs our detectors find in the puzzle's solution move.

    A puzzle whose FEN or solution will not parse is skipped rather than
    counted against the detectors -- that is a data problem, not a miss.
    """
    move = puzzle.move
    if move is None:
        return set()
    try:
        board = chess.Board(puzzle.fen)
    except ValueError:
        return set()
    if move not in board.legal_moves:
        return set()
    return {hit.motif for hit in detect_motifs(board, move)}


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
