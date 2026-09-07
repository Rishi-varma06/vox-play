"""
Engine analysis -- the chess domain's ground truth.

Follows the same shape as voxplay/tts: an abstract provider, a real backend,
an offline mock, and a get_engine() factory. The mock exists for the same
reason MockTTSProvider does -- the full pipeline has to run on a machine with
no Stockfish and no network, or demo day becomes a hostage to installation.

Two decisions worth keeping
---------------------------
Budget by NODES, not depth or time. Time-limited analysis gives different
answers on different machines and different answers on the same machine under
load, which makes every number in the report unreproducible. A node budget is
deterministic for a given engine build.

Analyse positions, not moves. The position after move i is the position before
move i+1, so a game of N moves needs N+1 evaluations rather than 2N. That is
an exact halving of the most expensive stage in the pipeline, for free.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import chess
import chess.engine

from .models import EngineLine, Evaluation, MoveAnalysis, MoveEvent
from .evaluation import (
    ClassificationThresholds,
    DEFAULT_THRESHOLDS,
    accuracy_pct,
    classify_move,
    win_loss_for_move,
    win_pct_for,
    win_pct_white,
)
from .pgn_source import ParsedGame, PIECE_VALUES


class AnalysisError(RuntimeError):
    """Raised by AnalysisEngine implementations on unrecoverable failure."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalysisConfig:
    """
    Engine budget and search width.

    `nodes` is the reproducibility knob and the cost knob at once. 1M nodes is
    roughly a second per position on a modern core -- fast enough for a whole
    game in well under a minute once positions run in parallel, strong enough
    that classifications stop changing much above it. The depth/quality
    sensitivity sweep is a planned experiment; vary this field for it.

    `multipv` of 3 is the minimum that supports only-move detection, which
    needs the gap between the best and second-best move.
    """
    nodes: int = 1_000_000
    multipv: int = 3
    threads: int = 1
    hash_mb: int = 128

    @property
    def cache_key(self) -> str:
        return f"n{self.nodes}-pv{self.multipv}"


#: A move is an "only move" when the best line beats the second best by this
#: much win probability -- the position had a single resource and finding it
#: mattered. These are the most dramatic moments in any game.
ONLY_MOVE_GAP = 15.0


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class AnalysisEngine(ABC):
    """Abstract base for all position-evaluation backends."""

    #: Identifies the backend in cache keys, so mock and real results never mix.
    name: str = "abstract"

    @abstractmethod
    def analyse_position(self, board: chess.Board) -> List[EngineLine]:
        """
        Evaluate `board`, best line first.

        Returns an empty list for a terminal position -- callers should use
        terminal_evaluation() for those rather than asking the engine.
        """
        ...

    def close(self) -> None:
        """Release any subprocess. Safe to call more than once."""

    def __enter__(self) -> "AnalysisEngine":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Terminal positions
# ---------------------------------------------------------------------------

def terminal_evaluation(board: chess.Board) -> Optional[Evaluation]:
    """
    Evaluation for a finished position, or None if the game is still on.

    An engine cannot analyse a position with no legal moves, so these are
    resolved from the rules instead. Sign convention matches Evaluation:
    White POV, and `mate=0` means White has been mated.
    """
    if not board.is_game_over(claim_draw=False):
        return None
    if board.is_checkmate():
        # The side to move is the one that has been mated.
        return Evaluation(mate=0) if board.turn == chess.WHITE else Evaluation(mate=1)
    return Evaluation(cp=0)


def _pv_to_san(board: chess.Board, moves: Sequence[chess.Move], limit: int = 8) -> List[str]:
    """Render a principal variation as SAN, on a scratch copy of the board."""
    scratch = board.copy(stack=False)
    out: List[str] = []
    for move in moves[:limit]:
        if move not in scratch.legal_moves:
            break
        out.append(scratch.san(move))
        scratch.push(move)
    return out


# ---------------------------------------------------------------------------
# Stockfish
# ---------------------------------------------------------------------------

#: Checked in order when no explicit path is given.
_STOCKFISH_CANDIDATES = (
    r"C:\Program Files\Stockfish\stockfish.exe",
    r"C:\stockfish\stockfish.exe",
    "/usr/games/stockfish",
    "/usr/local/bin/stockfish",
    "/opt/homebrew/bin/stockfish",
)


def find_stockfish(explicit: Optional[str] = None) -> Optional[str]:
    """Locate a Stockfish binary: explicit path, env var, PATH, then guesses."""
    for candidate in (explicit, os.environ.get("VOXPLAY_STOCKFISH_PATH")):
        if candidate and Path(candidate).exists():
            return candidate

    on_path = shutil.which("stockfish")
    if on_path:
        return on_path

    for candidate in _STOCKFISH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


class StockfishEngine(AnalysisEngine):
    """
    Real UCI analysis via Stockfish.

    Results are memoised per FEN. Opening positions repeat heavily across a
    corpus, so even an in-process cache pays for itself the moment you analyse
    a second game from the same opening.
    """

    name = "stockfish"

    def __init__(
        self,
        config: AnalysisConfig = AnalysisConfig(),
        path: Optional[str] = None,
    ) -> None:
        binary = find_stockfish(path)
        if binary is None:
            raise AnalysisError(
                "Stockfish not found. Install it and either put it on PATH or "
                "set VOXPLAY_STOCKFISH_PATH to the binary. "
                "Use engine='mock' to run the pipeline without it."
            )
        self.config = config
        self.path = binary
        self._cache: Dict[str, List[EngineLine]] = {}
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(binary)
            self._engine.configure({
                "Threads": config.threads,
                "Hash": config.hash_mb,
            })
        except Exception as exc:  # noqa: BLE001 - surfaced as AnalysisError
            raise AnalysisError(f"could not start Stockfish at {binary}: {exc}") from exc

    def analyse_position(self, board: chess.Board) -> List[EngineLine]:
        if board.is_game_over(claim_draw=False):
            return []

        key = board.fen()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            infos = self._engine.analyse(
                board,
                chess.engine.Limit(nodes=self.config.nodes),
                multipv=self.config.multipv,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as AnalysisError
            raise AnalysisError(f"engine analysis failed: {exc}") from exc

        lines: List[EngineLine] = []
        for rank, info in enumerate(infos, start=1):
            pv = info.get("pv") or []
            if not pv:
                continue
            white_score = info["score"].white()
            lines.append(
                EngineLine(
                    rank=rank,
                    move_san=board.san(pv[0]),
                    move_uci=pv[0].uci(),
                    evaluation=Evaluation(
                        cp=white_score.score(),
                        mate=white_score.mate(),
                    ),
                    pv_san=_pv_to_san(board, pv),
                )
            )

        self._cache[key] = lines
        return lines

    def close(self) -> None:
        engine = getattr(self, "_engine", None)
        if engine is not None:
            try:
                engine.quit()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
            self._engine = None


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------

class MockAnalysisEngine(AnalysisEngine):
    """
    Deterministic offline stand-in for Stockfish.

    Evaluations are material balance plus a FEN-seeded jitter. That is not
    chess understanding, but it is stable across runs and correlated with the
    position, which is all the downstream layers need in order to be tested:
    the numbers move when the board moves, and they move the same way twice.

    Never use it for anything reported as a result.
    """

    name = "mock"

    def __init__(self, config: AnalysisConfig = AnalysisConfig()) -> None:
        self.config = config

    @staticmethod
    def _jitter(fen: str, salt: str = "") -> float:
        """Stable pseudo-random value in [-1, 1] derived from the position."""
        digest = hashlib.sha1(f"{fen}{salt}".encode("utf-8")).digest()
        return (int.from_bytes(digest[:4], "big") / 0xFFFFFFFF) * 2.0 - 1.0

    def analyse_position(self, board: chess.Board) -> List[EngineLine]:
        if board.is_game_over(claim_draw=False):
            return []

        fen = board.fen()
        balance = sum(
            len(board.pieces(pt, chess.WHITE)) * v - len(board.pieces(pt, chess.BLACK)) * v
            for pt, v in PIECE_VALUES.items()
        )

        legal = list(board.legal_moves)
        # Deterministic ordering so repeated runs agree.
        legal.sort(key=lambda m: m.uci())
        legal.sort(key=lambda m: self._jitter(fen, m.uci()), reverse=True)

        lines: List[EngineLine] = []
        for rank, move in enumerate(legal[: self.config.multipv], start=1):
            # Best line strongest, each subsequent line a little worse.
            cp = int(balance * 100 + self._jitter(fen, move.uci()) * 60) - (rank - 1) * 25
            lines.append(
                EngineLine(
                    rank=rank,
                    move_san=board.san(move),
                    move_uci=move.uci(),
                    evaluation=Evaluation(cp=cp),
                    pv_san=_pv_to_san(board, [move]),
                )
            )
        return lines


def get_engine(
    name: str = "stockfish",
    config: AnalysisConfig = AnalysisConfig(),
    path: Optional[str] = None,
) -> AnalysisEngine:
    """
    Factory mirroring voxplay.tts.get_provider().

    'auto' falls back to the mock when no Stockfish binary is present, so the
    demo path always runs; ask for 'stockfish' explicitly when a missing
    engine should be a hard error.
    """
    key = (name or "").lower()
    if key == "mock":
        return MockAnalysisEngine(config)
    if key == "stockfish":
        return StockfishEngine(config, path=path)
    if key == "auto":
        if find_stockfish(path) is None:
            return MockAnalysisEngine(config)
        return StockfishEngine(config, path=path)
    raise AnalysisError(f"unknown engine '{name}' (expected stockfish, mock or auto)")


# ---------------------------------------------------------------------------
# Whole-game analysis
# ---------------------------------------------------------------------------

#: Piece values keyed by the spoken names MoveEvent carries.
_VALUE_BY_NAME = {"pawn": 1, "knight": 3, "bishop": 3, "rook": 5, "queen": 9}


def _material_swing(event: MoveEvent) -> int:
    """
    Material the mover won on this move, in pawns, from the mover's side.

    Always non-negative: a sacrifice is not visible here, it shows up as the
    opponent's reply having a large positive swing. Detecting the sacrifice
    itself needs the engine's continuation, which is the motif layer's job.
    """
    gained = _VALUE_BY_NAME.get(event.captured_piece or "", 0)
    if event.is_promotion:
        gained += _VALUE_BY_NAME.get(event.promotion_piece or "queen", 9) - 1
    return gained


def analyse_game(
    parsed: ParsedGame,
    engine: AnalysisEngine,
    thresholds: ClassificationThresholds = DEFAULT_THRESHOLDS,
) -> ParsedGame:
    """
    Attach engine verdicts to every move in a parsed game, in place.

    Evaluates each distinct position once and reuses it as both the "after"
    of one move and the "before" of the next.
    """
    moves = parsed.moves
    if not moves:
        return parsed

    # Position i is the one before move i; the final entry is the end position.
    boards = [chess.Board(moves[0].fen_before)]
    boards.extend(chess.Board(move.fen_after) for move in moves)

    evaluations: List[Evaluation] = []
    line_sets: List[List[EngineLine]] = []
    for board in boards:
        terminal = terminal_evaluation(board)
        if terminal is not None:
            evaluations.append(terminal)
            line_sets.append([])
            continue
        lines = engine.analyse_position(board)
        line_sets.append(lines)
        evaluations.append(lines[0].evaluation if lines else Evaluation(cp=0))

    for index, move in enumerate(moves):
        eval_before = evaluations[index]
        eval_after = evaluations[index + 1]
        win_before = win_pct_white(eval_before)
        win_after = win_pct_white(eval_after)
        win_loss = win_loss_for_move(win_before, win_after, move.color)

        lines = line_sets[index]
        best = lines[0] if lines else None
        is_best = bool(best and best.move_uci == move.uci)

        top_gap = None
        if len(lines) >= 2:
            top_gap = abs(
                win_pct_for(lines[0].evaluation, move.color)
                - win_pct_for(lines[1].evaluation, move.color)
            )

        move.analysis = MoveAnalysis(
            eval_before=eval_before,
            eval_after=eval_after,
            win_pct_before=round(win_before, 2),
            win_pct_after=round(win_after, 2),
            win_loss=round(win_loss, 2),
            accuracy=round(accuracy_pct(win_loss), 2),
            quality=classify_move(win_loss, is_best=is_best, thresholds=thresholds),
            is_best=is_best,
            is_only_move=bool(top_gap is not None and top_gap >= ONLY_MOVE_GAP),
            top_gap=round(top_gap, 2) if top_gap is not None else None,
            best_move_san=best.move_san if best else None,
            best_line_san=list(best.pv_san) if best else [],
            material_swing=_material_swing(move),
        )
        move.state.win_pct_white = round(win_after, 2)

    # The start event describes the position before the first move.
    parsed.events[0].state.win_pct_white = round(win_pct_white(evaluations[0]), 2)
    parsed.events[-1].state.win_pct_white = round(win_pct_white(evaluations[-1]), 2)
    return parsed
