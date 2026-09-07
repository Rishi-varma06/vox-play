"""
Chess domain adapter for Voxplay.

Produces the same shape of structured event stream that the cricket domain
produces, but sourced from PGN + engine analysis instead of a human scorer.
This is the point of the chess pivot: cricket needs a person (or a vision
model) in the loop to generate events, while chess gets its event stream and
its ground-truth evaluation for free.

Package name is `chessgame`, not `chess`, so it does not shadow the
python-chess library on import.

Layers:
    pgn_source  -- PGN / Lichess / Chess.com  -> MoveEvent stream (no engine)
    engine      -- UCI engine wrapper (Stockfish) + offline mock
    evaluation  -- centipawns -> win probability -> move classification
"""

from .models import (
    ChessEvent,
    ChessEventType,
    Evaluation,
    GameEndEvent,
    GameMeta,
    GamePhase,
    GameStartEvent,
    GameState,
    MoveAnalysis,
    MoveEvent,
    MoveQuality,
)

__all__ = [
    "ChessEvent",
    "ChessEventType",
    "Evaluation",
    "GameEndEvent",
    "GameMeta",
    "GamePhase",
    "GameStartEvent",
    "GameState",
    "MoveAnalysis",
    "MoveEvent",
    "MoveQuality",
]
