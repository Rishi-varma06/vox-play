"""
Centipawns -> win probability -> move classification.

Why win probability and not centipawns
--------------------------------------
Centipawn deltas are not comparable across positions. Dropping 100cp from
+900 is irrelevant -- the game is already over. Dropping 100cp from 0.00 can
be the losing move. Classifying on raw centipawns therefore over-reports
blunders in decided positions and under-reports them in sharp equal ones.

Mapping to win probability first fixes this, because it is already saturated
at the extremes: the same 100cp is worth ~12 points of win probability near
equality and under 1 point at +900. Every classifier, salience score and
pacing decision downstream reads win probability, never centipawns.

The logistic constant below is Lichess's published mapping, which lets us
compare our classifications directly against Lichess's own annotations as an
evaluation baseline. Refitting it per rating band (a +2.00 position is far
less won between 1200s than between masters) is a planned experiment; keep
the constant in one place so that swap stays cheap.

Everything in this module is pure. No engine, no I/O, no board.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .models import Color, Evaluation, MoveQuality

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Logistic steepness for the centipawn -> win% mapping (Lichess).
WIN_PCT_SCALE = 0.00368208

#: Centipawn clamp. Beyond this the logistic is saturated anyway, and huge
#: values from forced sequences would otherwise dominate averages.
CP_CLAMP = 1000

#: Accuracy curve coefficients (Lichess).
_ACC_A = 103.1668
_ACC_B = -0.04354
_ACC_C = -3.1669


@dataclass(frozen=True)
class ClassificationThresholds:
    """
    Win-percentage loss required to earn each label.

    Defaults follow Lichess so our output is comparable to theirs out of the
    box. These are the primary knob for the classification-agreement
    experiment -- vary them, report agreement against Lichess annotations as
    a function of the thresholds.

    `decided_win_pct` caps severity once the game is already decided: above
    it, a move can be an inaccuracy but never a mistake or a blunder. Off by
    default, because Lichess does not do this and leaving it off keeps the
    agreement experiment clean. Turn it on for commentary, where announcing
    a "blunder" by a player who is still completely winning is simply wrong
    -- see COMMENTARY_THRESHOLDS.
    """
    inaccuracy: float = 10.0
    mistake: float = 20.0
    blunder: float = 30.0
    decided_win_pct: Optional[float] = None


DEFAULT_THRESHOLDS = ClassificationThresholds()

#: Same ladder, but refusing to call a move played from a won position a
#: blunder. Use this on the commentary path, not on the evaluation path.
COMMENTARY_THRESHOLDS = ClassificationThresholds(decided_win_pct=90.0)


# ---------------------------------------------------------------------------
# Evaluation -> win probability
# ---------------------------------------------------------------------------

def cp_to_win_pct(cp: float) -> float:
    """
    Centipawns (White POV) -> White's win probability on a 0-100 scale.

    0cp maps to exactly 50.0; the curve is symmetric about it.
    """
    cp = max(-CP_CLAMP, min(CP_CLAMP, cp))
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-WIN_PCT_SCALE * cp)) - 1.0)


def win_pct_white(ev: Evaluation) -> float:
    """
    Any evaluation -> White's win probability (0-100).

    Mate convention follows python-chess: a White-POV `mate` of 0 means White
    has been checkmated (a loss), matching `chess.engine.Mate(0)`. Positive
    means White delivers mate. Mate distance does not change the probability;
    a forced mate is a forced mate.
    """
    if ev.mate is not None:
        return 100.0 if ev.mate > 0 else 0.0
    if ev.cp is None:
        return 50.0
    return cp_to_win_pct(ev.cp)


def win_pct_for(ev: Evaluation, color: Color) -> float:
    """White's win probability, flipped if we want it from Black's side."""
    white = win_pct_white(ev)
    return white if color == Color.WHITE else 100.0 - white


# ---------------------------------------------------------------------------
# Move quality
# ---------------------------------------------------------------------------

def win_loss_for_move(
    win_pct_before_white: float,
    win_pct_after_white: float,
    color: Color,
) -> float:
    """
    Win probability the MOVER threw away, in percentage points, never below 0.

    Both inputs are White POV. A move can appear to *gain* win probability
    when the deeper search after the move revises the earlier verdict; that is
    search noise rather than a real gain, so it clamps to zero.
    """
    if color == Color.WHITE:
        loss = win_pct_before_white - win_pct_after_white
    else:
        loss = win_pct_after_white - win_pct_before_white
    return max(0.0, loss)


def accuracy_pct(win_loss: float) -> float:
    """
    Lichess accuracy curve: win% lost on a move -> 0-100 accuracy.

    A perfect move scores 100. Used for per-player summary lines
    ("she played at 94 percent accuracy"), not for classification.
    """
    value = _ACC_A * math.exp(_ACC_B * max(0.0, win_loss)) + _ACC_C
    return max(0.0, min(100.0, value))


def classify_move(
    win_loss: float,
    *,
    is_best: bool = False,
    in_book: bool = False,
    win_pct_after_mover: Optional[float] = None,
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
) -> MoveQuality:
    """
    Label a move from the win probability it cost.

    A move the engine itself ranks first is never an error, whatever the
    numbers say. When the top move appears to lose win probability, the
    search has revised its own earlier assessment after seeing one move
    deeper -- that is the engine correcting itself, not the player erring.
    Without this rule the classifier calls Morphy's winning combination in
    the Opera Game an inaccuracy, which is the kind of output that destroys
    a listener's trust in everything else the commentary says.

    `in_book` only suppresses the *minor* labels. A move still nominally in a
    database but losing 30 points of win probability is a blunder regardless
    of how many people have played it before -- opening databases contain
    plenty of bad theory, and calling that "book" would hide exactly the
    moments worth talking about.

    BRILLIANT and GREAT are never returned here: they need sacrifice and
    only-move detection, which is a separate layer with board access.
    """
    if is_best:
        return MoveQuality.BOOK if in_book else MoveQuality.BEST

    if win_loss >= thresholds.blunder:
        severity = MoveQuality.BLUNDER
    elif win_loss >= thresholds.mistake:
        severity = MoveQuality.MISTAKE
    elif win_loss >= thresholds.inaccuracy:
        severity = MoveQuality.INACCURACY
    else:
        return MoveQuality.BOOK if in_book else MoveQuality.GOOD

    # Still winning comfortably? Then it cost something, but it did not cost
    # the game, and calling it a blunder would misdescribe the position.
    if (
        thresholds.decided_win_pct is not None
        and win_pct_after_mover is not None
        and win_pct_after_mover >= thresholds.decided_win_pct
    ):
        return MoveQuality.INACCURACY

    return severity


#: Labels that mean the mover made a real error, for filtering and counting.
ERROR_QUALITIES = frozenset({
    MoveQuality.INACCURACY,
    MoveQuality.MISTAKE,
    MoveQuality.BLUNDER,
})


# ---------------------------------------------------------------------------
# Plain-language description
# ---------------------------------------------------------------------------

#: (upper bound on White win%, description). Scanned in order.
_EVAL_BANDS = (
    (2.0, "black has a completely winning position"),
    (10.0, "black is winning"),
    (25.0, "black is clearly better"),
    (40.0, "black is slightly better"),
    (60.0, "the position is roughly balanced"),
    (75.0, "white is slightly better"),
    (90.0, "white is clearly better"),
    (98.0, "white is winning"),
    (100.01, "white has a completely winning position"),
)


def describe_eval(win_pct_white_value: float) -> str:
    """
    Win probability -> a plain-English phrase.

    The commentary layer is handed this string rather than a number, so the
    model never has to decide for itself what "+1.4" means. Fixed vocabulary
    here is deliberate: it is one less thing that can be hallucinated, and it
    keeps the same position described the same way across a whole game.
    """
    for upper, text in _EVAL_BANDS:
        if win_pct_white_value < upper:
            return text
    return _EVAL_BANDS[-1][1]
