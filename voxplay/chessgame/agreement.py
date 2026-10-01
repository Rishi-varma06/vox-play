"""
Agreement between our move classifier and Lichess's own annotations.

Lichess publishes both halves of what this needs: `[%eval]` on every move of
an analysed game, and its own verdict as a NAG on the move itself -- `?!` for
an inaccuracy, `?` for a mistake, `??` for a blunder. Both projects classify
from the same published win-probability mapping, so the comparison is
meaningful rather than two unrelated scales put side by side.

Two measurements, and the difference between them is the point
--------------------------------------------------------------
**Rule agreement** runs our classifier on *Lichess's own evaluations*. Any
disagreement is then attributable to the classification rule alone -- the
thresholds, the win-probability conversion, the best-move exemption -- since
both sides are reading identical numbers. This is the clean test of the part
of the system this project actually wrote.

**Pipeline agreement** runs our classifier on *our own Stockfish analysis* of
the same games. Disagreements here also include everything that differs
between two engines at two search depths. This is the honest end-to-end
figure, and it should be the lower of the two.

Reporting only the first would flatter the system; reporting only the second
would blame the rule for the engine's disagreements.
"""

from __future__ import annotations

import io
import math
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import chess
import chess.pgn
from pydantic import BaseModel, Field

from .evaluation import (
    ClassificationThresholds,
    DEFAULT_THRESHOLDS,
    classify_move,
    win_loss_for_move,
    win_pct_white,
)
from .models import Color, Evaluation, MoveQuality

GAMES_API = "https://lichess.org/api/games/user/{user}"
TV_API = "https://lichess.org/api/tv/channels"
USER_AGENT = "voxplay/0.1 (university capstone; classifier agreement study)"

#: Seconds between bulk requests. The puzzle endpoint rate-limited us at
#: 0.7s; the games export is heavier still, so this errs well slow.
REQUEST_DELAY = 3.0

#: Lichess's annotation NAGs. 6 is `?!`, 2 is `?`, 4 is `??`; anything else
#: (including no NAG at all) means Lichess did not call the move an error.
NAG_TO_QUALITY: Dict[int, MoveQuality] = {
    6: MoveQuality.INACCURACY,
    2: MoveQuality.MISTAKE,
    4: MoveQuality.BLUNDER,
}

#: The four classes compared. Everything Lichess leaves unmarked, and
#: everything we call best/good/book, collapses into NOT_AN_ERROR -- the
#: question is whether the two agree that a move was an error and how bad.
NOT_AN_ERROR = "ok"
CLASSES = (NOT_AN_ERROR, "inaccuracy", "mistake", "blunder")


class AgreementError(RuntimeError):
    """Raised when annotated games cannot be obtained."""


def _label(quality: Optional[MoveQuality]) -> str:
    if quality in (MoveQuality.INACCURACY, MoveQuality.MISTAKE, MoveQuality.BLUNDER):
        return quality.value
    return NOT_AN_ERROR


class AnnotatedMove(BaseModel):
    """One move with both the evaluation around it and Lichess's verdict."""
    game_id: str
    ply: int
    san: str
    color: Color
    win_before: float
    win_after: float
    lichess: str = NOT_AN_ERROR


class AgreementReport(BaseModel):
    """Confusion matrix and agreement statistics for one comparison."""
    basis: str
    moves: int = 0
    matrix: Dict[str, Dict[str, int]] = Field(default_factory=dict)

    @property
    def observed(self) -> float:
        """Share of moves where both sides gave the same label."""
        if not self.moves:
            return 0.0
        same = sum(self.matrix.get(c, {}).get(c, 0) for c in CLASSES)
        return same / self.moves

    @property
    def expected(self) -> float:
        """Agreement two independent raters would reach by chance alone."""
        if not self.moves:
            return 0.0
        total = 0.0
        for cls in CLASSES:
            ours = sum(self.matrix.get(cls, {}).values())
            theirs = sum(row.get(cls, 0) for row in self.matrix.values())
            total += (ours / self.moves) * (theirs / self.moves)
        return total

    @property
    def kappa(self) -> float:
        """
        Cohen's kappa. Reported instead of raw agreement because most moves
        are not errors: a classifier that called everything "ok" would score
        over 90% raw agreement and zero skill.
        """
        expected = self.expected
        if expected >= 1.0:
            return 0.0
        return (self.observed - expected) / (1.0 - expected)

    def error_recall(self, cls: str) -> Optional[float]:
        """Of the moves Lichess called `cls`, how many did we call `cls`?"""
        theirs = sum(row.get(cls, 0) for row in self.matrix.values())
        if not theirs:
            return None
        return self.matrix.get(cls, {}).get(cls, 0) / theirs

    def table(self) -> str:
        """ASCII confusion matrix; rows are ours, columns are Lichess's."""
        width = 12
        lines = [
            f"  basis: {self.basis}   moves: {self.moves}",
            "",
            "  ours \\ lichess " + "".join(c.rjust(width) for c in CLASSES),
            "  " + "-" * (15 + width * len(CLASSES)),
        ]
        for ours in CLASSES:
            row = self.matrix.get(ours, {})
            cells = "".join(str(row.get(c, 0)).rjust(width) for c in CLASSES)
            lines.append(f"  {ours:<13} {cells}")
        lines.append("")
        lines.append("  raw agreement %.1f%%   expected by chance %.1f%%   "
                     "kappa %.3f" % (100 * self.observed, 100 * self.expected,
                                     self.kappa))
        for cls in CLASSES[1:]:
            recall = self.error_recall(cls)
            if recall is not None:
                lines.append("    agreed on %-11s %5.1f%%" % (cls, 100 * recall))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reading annotated games
# ---------------------------------------------------------------------------

def _evaluation(score: Optional[chess.engine.PovScore]) -> Optional[Evaluation]:
    """A python-chess PovScore to our White-POV Evaluation."""
    if score is None:
        return None
    white = score.white()
    return Evaluation(cp=white.score(), mate=white.mate())


def annotated_moves(game: chess.pgn.Game) -> List[AnnotatedMove]:
    """
    Every move of an analysed game, with the win probability either side of
    it and Lichess's own verdict.

    Lichess attaches `[%eval]` to the position AFTER a move, so a move's
    "before" is the previous node's evaluation. The first move has no
    predecessor; the starting position is equal by definition, so 50% stands
    in -- and Lichess does not annotate opening moves as errors in practice,
    so this affects nothing measurable.
    """
    game_id = game.headers.get("GameId") or game.headers.get("Site", "").rsplit("/", 1)[-1]
    out: List[AnnotatedMove] = []

    board = game.board()
    previous = 50.0
    for ply, node in enumerate(game.mainline(), start=1):
        if node.move is None:
            continue
        colour = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
        san = board.san(node.move)
        board.push(node.move)

        evaluation = _evaluation(node.eval())
        if evaluation is None:
            previous = previous  # unanalysed tail; stop contributing moves
            break
        after = win_pct_white(evaluation)

        verdict = NOT_AN_ERROR
        for nag in node.nags:
            if nag in NAG_TO_QUALITY:
                verdict = NAG_TO_QUALITY[nag].value
                break

        out.append(AnnotatedMove(
            game_id=game_id, ply=ply, san=san, color=colour,
            win_before=previous, win_after=after, lichess=verdict,
        ))
        previous = after

    return out


def read_games(pgn_text: str) -> Iterable[chess.pgn.Game]:
    stream = io.StringIO(pgn_text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            return
        yield game


def fetch_annotated_games(
    users: Sequence[str],
    per_user: int = 40,
    cache: Path | str = "data/games/annotated.pgn",
    delay: float = REQUEST_DELAY,
    progress: bool = True,
) -> str:
    """
    Download analysed games for each user, caching the PGN to disk.

    One request per user rather than one per game; `analysed=true` filters to
    games that actually carry computer analysis, so nothing is downloaded that
    cannot be scored.
    """
    import requests

    cache = Path(cache)
    if cache.exists() and cache.stat().st_size > 0:
        if progress:
            print(f"      using cached games at {cache}")
        return cache.read_text(encoding="utf-8")

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT, "Accept": "application/x-chess-pgn",
    })

    chunks: List[str] = []
    for index, user in enumerate(users):
        if progress:
            print(f"      fetching {per_user} analysed games for {user} ...")
        try:
            response = session.get(
                GAMES_API.format(user=user),
                params={
                    "max": per_user, "analysed": "true", "evals": "true",
                    "literate": "true", "clocks": "false", "opening": "true",
                },
                timeout=120,
            )
            if response.status_code == 429:
                if progress:
                    print("      rate limited; pausing 62s")
                time.sleep(62)
                continue
            response.raise_for_status()
            chunks.append(response.text)
        except Exception as exc:  # noqa: BLE001 - one user failing is not fatal
            if progress:
                print(f"      {user}: {type(exc).__name__}")
        if index + 1 < len(users):
            time.sleep(delay)

    text = "\n\n".join(chunks)
    if not text.strip():
        raise AgreementError(
            "no annotated games could be downloaded. Lichess may be rate "
            "limiting; try again later or pass a cached PGN."
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def featured_users(limit: int = 8) -> List[str]:
    """
    Usernames currently featured on Lichess TV, across time controls.

    Picking names by hand would bias the sample toward whatever strength the
    author happened to think of; the TV channels span bullet through
    classical and change constantly, which is closer to a spread.
    """
    import requests

    try:
        response = requests.get(
            TV_API, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        response.raise_for_status()
        channels = response.json()
    except Exception as exc:  # noqa: BLE001 - surfaced as AgreementError
        raise AgreementError(f"could not read Lichess TV channels: {exc}") from exc

    seen: List[str] = []
    for channel in channels.values():
        name = (channel.get("user") or {}).get("name")
        if name and name not in seen:
            seen.append(name)
    return seen[:limit]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def classify_with_our_rule(
    move: AnnotatedMove, thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS
) -> str:
    """Our classifier's verdict, computed from whatever evaluations it is given."""
    loss = win_loss_for_move(move.win_before, move.win_after, move.color)
    after_mover = (
        move.win_after if move.color == Color.WHITE else 100.0 - move.win_after
    )
    return _label(classify_move(
        loss, win_pct_after_mover=after_mover, thresholds=thresholds
    ))


def compare(
    pairs: Iterable[Tuple[str, str]], basis: str
) -> AgreementReport:
    """Build a confusion matrix from (ours, lichess) label pairs."""
    report = AgreementReport(basis=basis)
    matrix: Dict[str, Dict[str, int]] = {c: {d: 0 for d in CLASSES} for c in CLASSES}
    count = 0
    for ours, theirs in pairs:
        matrix[ours][theirs] += 1
        count += 1
    report.matrix = matrix
    report.moves = count
    return report


def rule_agreement(
    moves: Sequence[AnnotatedMove],
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
) -> AgreementReport:
    """
    Our rule against Lichess's, both reading Lichess's evaluations.

    Isolates the classification rule: any disagreement here is the thresholds
    or the win-probability conversion, not two engines differing.
    """
    return compare(
        ((classify_with_our_rule(m, thresholds), m.lichess) for m in moves),
        basis="our rule on Lichess evaluations",
    )
