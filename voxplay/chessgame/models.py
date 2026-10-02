"""
Chess event schema -- the contract the chess domain produces.

Mirrors the design of voxplay/events/models.py: a discriminated union on
`event_type`, with a state snapshot attached to every event describing the
position AFTER that event.

Two fields carry the whole downstream pipeline:

    GameState.win_pct_white -- position value on a 0-100 scale. Every
        classification, salience score and narrative decision runs off this,
        never off raw centipawns (see evaluation.py for why).

    MoveAnalysis.win_loss -- how much win probability the mover threw away.
        This is the chess equivalent of "what just happened" and is the single
        most important number for pacing.

Convergence note: cricket events (voxplay/events/models.py) and chess events
are deliberately separate unions for now. They converge later behind a narrow
protocol that the commentary engine consumes; nothing here should grow a
cricket-shaped field to speed that up.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ChessEventType(str, Enum):
    GAME_START = "game_start"
    MOVE = "move"
    GAME_END = "game_end"


class Color(str, Enum):
    WHITE = "white"
    BLACK = "black"


class GamePhase(str, Enum):
    OPENING = "opening"
    MIDDLEGAME = "middlegame"
    ENDGAME = "endgame"


class MoveQuality(str, Enum):
    """
    Move classification, ordered best to worst.

    BOOK is not a quality judgement -- it marks a move still inside opening
    theory, where engine deltas are noise and commentary should talk about
    plans rather than accuracy.

    BRILLIANT and GREAT require sacrifice / only-move detection that lives in
    a later layer; the eval-delta classifier never returns them.
    """
    BRILLIANT = "brilliant"
    GREAT = "great"
    BEST = "best"
    GOOD = "good"
    BOOK = "book"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


class Motif(str, Enum):
    """
    Tactical patterns detected from board geometry, not from the engine.

    Names match Lichess puzzle-database themes wherever one exists, so the
    detectors can be scored for precision and recall against that dataset
    (CC0, millions of human-tagged puzzles) instead of being asserted to work.
    """
    FORK = "fork"
    PIN = "pin"
    SKEWER = "skewer"
    DISCOVERED_ATTACK = "discoveredAttack"
    DOUBLE_CHECK = "doubleCheck"
    HANGING_PIECE = "hangingPiece"
    SACRIFICE = "sacrifice"
    BACK_RANK_MATE = "backRankMate"
    SMOTHERED_MATE = "smotheredMate"
    PROMOTION = "promotion"
    EN_PASSANT = "enPassant"
    ADVANCED_PAWN = "advancedPawn"


class MotifHit(BaseModel):
    """
    One detected pattern, with the squares it involves.

    `squares` drives board highlighting in the renderer. `description` is a
    factual sentence handed to the commentary layer -- the model narrates it
    rather than deducing it, which is what stops it inventing tactics.
    """
    motif: Motif
    squares: List[str] = Field(default_factory=list)
    description: str


class GameResult(str, Enum):
    WHITE_WINS = "1-0"
    BLACK_WINS = "0-1"
    DRAW = "1/2-1/2"
    UNKNOWN = "*"


# ---------------------------------------------------------------------------
# Sub-objects
# ---------------------------------------------------------------------------

class Evaluation(BaseModel):
    """
    An engine evaluation, always normalised to WHITE's point of view.

    Exactly one of `cp` / `mate` is meaningful. Positive favours White in both
    cases (`mate=3` means White mates in 3, `mate=-2` means Black mates in 2).
    Storing one POV everywhere removes a whole class of sign-flip bugs; use
    evaluation.win_pct_for() to get a mover-relative number.
    """
    cp: Optional[int] = None
    mate: Optional[int] = None

    @property
    def is_mate(self) -> bool:
        return self.mate is not None


class EngineLine(BaseModel):
    """One MultiPV line from the engine."""
    rank: int                              # 1 = engine's best
    move_san: str
    move_uci: str
    evaluation: Evaluation
    pv_san: List[str] = Field(default_factory=list)


class MoveAnalysis(BaseModel):
    """
    Engine verdict on a single played move.

    Populated by the analysis layer; absent on a freshly-parsed PGN. All
    win-percentage fields are 0-100.
    """
    eval_before: Evaluation
    eval_after: Evaluation
    win_pct_before: float                  # White POV, position before the move
    win_pct_after: float                   # White POV, position after the move
    win_loss: float                        # MOVER POV, win% thrown away (>= 0)
    accuracy: float                        # 0-100, Lichess accuracy formula
    quality: MoveQuality

    is_best: bool = False                  # played move was engine top-1
    is_only_move: bool = False             # top-1 far better than top-2
    top_gap: Optional[float] = None        # win% between top-1 and top-2
    best_move_san: Optional[str] = None
    best_line_san: List[str] = Field(default_factory=list)

    # Cheap-to-compute flags used by later layers (salience, pacing).
    material_swing: int = 0                # material delta in pawns, mover POV
    motifs: List[MotifHit] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class GameState(BaseModel):
    """
    Snapshot of the position AFTER the event.

    Everything here is derivable from the FEN, but is materialised so the
    commentary layer never has to reason about a board -- it only reads
    numbers and labels it was given. That separation is what keeps the LLM
    from inventing chess facts.
    """
    fen: str
    ply: int = 0                           # half-moves played so far
    move_number: int = 1                   # full move number
    side_to_move: Color = Color.WHITE

    white_material: int = 39               # standard values, kings excluded
    black_material: int = 39
    material_balance: int = 0              # white - black, in pawns

    phase: GamePhase = GamePhase.OPENING

    # Engine-derived; None until the analysis layer runs.
    win_pct_white: Optional[float] = None

    is_check: bool = False
    is_checkmate: bool = False
    is_stalemate: bool = False

    white_clock_s: Optional[float] = None
    black_clock_s: Optional[float] = None


class OpeningReport(BaseModel):
    """Where a game sat in theory, and where it left."""
    eco: Optional[str] = None
    name: Optional[str] = None
    #: Half-moves that stayed inside a named position.
    book_plies: int = 0
    #: The first half-move whose resulting position is not in the book.
    exit_ply: Optional[int] = None
    exit_san: Optional[str] = None
    #: Whose move left theory.
    exit_color: Optional[str] = None

    @property
    def exit_move_number(self) -> Optional[int]:
        """Full-move number of the departure, for saying it aloud."""
        if self.exit_ply is None:
            return None
        return (self.exit_ply + 1) // 2

    def describe(self) -> str:
        """A sentence the commentary layer can use as a fact."""
        if self.name is None:
            return "this opening is not in the book"
        if self.exit_ply is None:
            return f"the whole opening followed theory: {self.name}"
        return (
            f"they followed {self.name} and left theory on move "
            f"{self.exit_move_number} with {self.exit_san}"
        )


# ---------------------------------------------------------------------------
# Game metadata
# ---------------------------------------------------------------------------

class GameMeta(BaseModel):
    """PGN headers, normalised. Everything optional -- PGNs vary wildly."""
    game_id: str
    white: Optional[str] = None
    black: Optional[str] = None
    white_elo: Optional[int] = None
    black_elo: Optional[int] = None
    result: GameResult = GameResult.UNKNOWN
    eco: Optional[str] = None
    opening: Optional[str] = None
    event: Optional[str] = None
    site: Optional[str] = None
    date: Optional[str] = None
    time_control: Optional[str] = None
    termination: Optional[str] = None
    source: Optional[str] = None           # "pgn" | "lichess" | "chesscom"


# ---------------------------------------------------------------------------
# Event models
# ---------------------------------------------------------------------------

class _BaseChessEvent(BaseModel):
    """Fields common to every chess event."""
    game_id: str
    timestamp: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    video_offset: Optional[float] = None   # seconds from start; for AV sync


class GameStartEvent(_BaseChessEvent):
    event_type: Literal[ChessEventType.GAME_START] = ChessEventType.GAME_START
    meta: GameMeta
    state: GameState


class MoveEvent(_BaseChessEvent):
    event_type: Literal[ChessEventType.MOVE] = ChessEventType.MOVE

    ply: int                               # 1-based half-move index
    move_number: int
    color: Color
    san: str
    uci: str

    from_square: str
    to_square: str
    piece: str                             # "pawn", "knight", ... (spoken form)
    captured_piece: Optional[str] = None

    is_capture: bool = False
    is_check: bool = False
    is_checkmate: bool = False
    is_castle: bool = False
    is_promotion: bool = False
    promotion_piece: Optional[str] = None
    is_en_passant: bool = False

    fen_before: str
    fen_after: str

    state: GameState
    analysis: Optional[MoveAnalysis] = None

    clock_s: Optional[float] = None        # mover's clock after the move
    think_time_s: Optional[float] = None   # derived from consecutive clocks


class GameEndEvent(_BaseChessEvent):
    event_type: Literal[ChessEventType.GAME_END] = ChessEventType.GAME_END
    result: GameResult
    termination: Optional[str] = None
    total_plies: int = 0
    state: GameState


# ---------------------------------------------------------------------------
# Discriminated union -- the single type the chess pipeline handles
# ---------------------------------------------------------------------------

ChessEvent = Annotated[
    Union[
        GameStartEvent,
        MoveEvent,
        GameEndEvent,
    ],
    Field(discriminator="event_type"),
]
