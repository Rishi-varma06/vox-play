"""
Tactical motif detection from board geometry.

Deliberately not the LLM's job. A model asked "was that a fork?" will answer
confidently either way, and a listener who knows chess will catch it the one
time in ten it is wrong. These detectors are deterministic, cheap, and
testable against the Lichess puzzle database, so the commentary layer can be
handed a fact rather than asked for an opinion.

Motif names follow Lichess puzzle themes so that validation is a direct
comparison rather than a mapping exercise.

Everything here works on the position ALONE -- no engine. The one place that
is limiting is sacrifice detection: geometry can tell you material was given
up, but only a search can tell you whether it was sound. This module reports
the material fact; soundness is decided in the analysis layer, which has the
evaluation.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import chess

from .models import Motif, MotifHit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Piece values for exchange arithmetic. The king is given an absurd value so
#: it always sorts last as a capturer and always counts as the most valuable
#: thing on any line; it can never actually be captured in a legal position.
PIECE_VALUE: Dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}

PIECE_NAMES: Dict[chess.PieceType, str] = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}

_DIAGONAL = ((1, 1), (1, -1), (-1, 1), (-1, -1))
_ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))

_SLIDER_DIRECTIONS: Dict[chess.PieceType, Tuple[Tuple[int, int], ...]] = {
    chess.BISHOP: _DIAGONAL,
    chess.ROOK: _ORTHOGONAL,
    chess.QUEEN: _DIAGONAL + _ORTHOGONAL,
}

#: Material the opponent must stand to win for a move to read as a sacrifice.
#: Two pawns keeps ordinary exchanges and pawn grabs out of the results.
SACRIFICE_THRESHOLD = 2

#: A hanging piece has to be worth taking. Below this, "hanging" is noise.
HANGING_THRESHOLD = 1

#: Pins and skewers must involve a real piece, not a pawn. Pawn pins are
#: geometrically valid and almost never worth saying out loud -- unfiltered
#: they produced 17 "pins" in the 33-move Opera Game, most of them a queen
#: notionally pinning a pawn to another pawn, drowning the two pins that
#: actually mattered.
MIN_PINNED_VALUE = PIECE_VALUE[chess.KNIGHT]

#: A fork needs two targets worth having. "Forks the king and a pawn" is a
#: check that happens to hit a pawn, not a fork.
MIN_FORK_TARGET_VALUE = PIECE_VALUE[chess.KNIGHT]

#: Patterns that describe a STATE of the position rather than an event. They
#: persist for as long as the pieces stay put, so only the move that creates
#: one is news -- see detect_motifs.
STANDING_MOTIFS = frozenset({Motif.PIN, Motif.SKEWER, Motif.HANGING_PIECE})


def _name(square: int) -> str:
    return chess.square_name(square)


def _piece_name(board: chess.Board, square: int) -> str:
    piece = board.piece_at(square)
    return PIECE_NAMES.get(piece.piece_type, "piece") if piece else "piece"


# ---------------------------------------------------------------------------
# Static exchange evaluation
# ---------------------------------------------------------------------------

def _best_recapture_gain(board: chess.Board, target: int) -> int:
    """
    Material the side to move wins by capturing on `target`, best play.

    Recursive swap-off. Legality is delegated to python-chess, which means
    x-ray recaptures behind a battery are handled for free and a recapture
    that would expose the king is correctly excluded. Returns 0 when
    capturing loses material, because nobody is obliged to take.
    """
    victim = board.piece_at(target)
    if victim is None:
        return 0

    captures = [
        move for move in board.legal_moves
        if move.to_square == target and board.is_capture(move)
    ]
    if not captures:
        return 0

    # Always recapture with the least valuable attacker.
    def attacker_value(move: chess.Move) -> int:
        piece = board.piece_at(move.from_square)
        return PIECE_VALUE[piece.piece_type] if piece else 0

    move = min(captures, key=attacker_value)
    victim_value = PIECE_VALUE[victim.piece_type]

    board.push(move)
    try:
        gain = victim_value - _best_recapture_gain(board, target)
    finally:
        board.pop()

    return max(0, gain)


def static_exchange_eval(board: chess.Board, move: chess.Move) -> int:
    """
    Material the mover nets from the exchange sequence starting with `move`.

    Positive means the capture wins material; negative means it loses it.
    python-chess has no SEE of its own, and hanging-piece, sacrifice and
    trapped-piece detection all need one.
    """
    if board.is_en_passant(move):
        captured_value = PIECE_VALUE[chess.PAWN]
    else:
        victim = board.piece_at(move.to_square)
        captured_value = PIECE_VALUE[victim.piece_type] if victim else 0

    scratch = board.copy(stack=False)
    scratch.push(move)
    return captured_value - _best_recapture_gain(scratch, move.to_square)


def material_at_risk(board: chess.Board, square: int) -> int:
    """
    What the side to move wins by grabbing whatever sits on `square`.

    Zero when the piece is adequately defended or not attackable at all.
    """
    captures = [
        move for move in board.legal_moves
        if move.to_square == square and board.is_capture(move)
    ]
    if not captures:
        return 0
    # Clamped at zero: nobody is obliged to make a capture that loses
    # material, so a well-defended piece is worth nothing, not a negative.
    return max(0, max(static_exchange_eval(board, move) for move in captures))


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def _detect_fork(board: chess.Board, move: chess.Move, mover: chess.Color) -> Optional[MotifHit]:
    """
    One piece attacking two things worth having at once.

    A target counts when it is the king, when it is worth more than the
    attacker, or when it is undefended. Without that filter a queen eyeing
    two defended pawns would register as a fork.
    """
    attacker = board.piece_at(move.to_square)
    if attacker is None:
        return None
    attacker_value = PIECE_VALUE[attacker.piece_type]

    targets: List[int] = []
    for square in board.attacks(move.to_square):
        victim = board.piece_at(square)
        if victim is None or victim.color == mover:
            continue
        if victim.piece_type == chess.KING:
            targets.append(square)
            continue
        value = PIECE_VALUE[victim.piece_type]
        if value < MIN_FORK_TARGET_VALUE:
            continue  # attacking a pawn as well is not what makes a fork
        undefended = not board.attackers(not mover, square)
        if value > attacker_value or undefended:
            targets.append(square)

    if len(targets) < 2:
        return None

    named = ", ".join(f"{_piece_name(board, sq)} on {_name(sq)}" for sq in targets)
    return MotifHit(
        motif=Motif.FORK,
        squares=[_name(move.to_square)] + [_name(sq) for sq in targets],
        description=(
            f"the {_piece_name(board, move.to_square)} on {_name(move.to_square)} "
            f"forks the {named}"
        ),
    )


def _scan_line(
    board: chess.Board, origin: int, delta: Tuple[int, int], mover: chess.Color
) -> Optional[Tuple[int, int]]:
    """
    First two occupied squares along a ray, if both hold enemy pieces.

    Returns (front, back) squares. This is the shared geometry behind pins
    and skewers -- they are the same picture, and differ only in which of the
    two pieces is worth more.
    """
    file_step, rank_step = delta
    file_index = chess.square_file(origin)
    rank_index = chess.square_rank(origin)

    found: List[int] = []
    while len(found) < 2:
        file_index += file_step
        rank_index += rank_step
        if not (0 <= file_index <= 7 and 0 <= rank_index <= 7):
            return None
        square = chess.square(file_index, rank_index)
        piece = board.piece_at(square)
        if piece is None:
            continue
        if piece.color == mover:
            return None
        found.append(square)

    return found[0], found[1]


def _detect_pins_and_skewers(board: chess.Board, mover: chess.Color) -> List[MotifHit]:
    """
    Two enemy pieces lined up on one of our sliders' rays.

    Valuable piece behind means the front one is pinned; valuable piece in
    front means it is skewered. Equal values are neither, so they are skipped.
    """
    hits: List[MotifHit] = []
    for piece_type, directions in _SLIDER_DIRECTIONS.items():
        for origin in board.pieces(piece_type, mover):
            for delta in directions:
                line = _scan_line(board, origin, delta, mover)
                if line is None:
                    continue
                front, back = line
                front_piece = board.piece_at(front)
                back_piece = board.piece_at(back)
                if front_piece is None or back_piece is None:
                    continue

                front_value = PIECE_VALUE[front_piece.piece_type]
                back_value = PIECE_VALUE[back_piece.piece_type]
                squares = [_name(origin), _name(front), _name(back)]

                if back_value > front_value and front_value >= MIN_PINNED_VALUE:
                    absolute = back_piece.piece_type == chess.KING
                    hits.append(MotifHit(
                        motif=Motif.PIN,
                        squares=squares,
                        description=(
                            f"the {PIECE_NAMES[piece_type]} on {_name(origin)} pins the "
                            f"{_piece_name(board, front)} on {_name(front)} "
                            f"{'to the king' if absolute else f'to the {_piece_name(board, back)} on {_name(back)}'}"
                        ),
                    ))
                elif front_value > back_value and back_value >= MIN_PINNED_VALUE:
                    hits.append(MotifHit(
                        motif=Motif.SKEWER,
                        squares=squares,
                        description=(
                            f"the {PIECE_NAMES[piece_type]} on {_name(origin)} skewers the "
                            f"{_piece_name(board, front)} on {_name(front)}, winning the "
                            f"{_piece_name(board, back)} behind it"
                        ),
                    ))
    return hits


def _detect_discovered_attack(
    before: chess.Board, after: chess.Board, move: chess.Move, mover: chess.Color
) -> Optional[MotifHit]:
    """
    A piece that did not move suddenly attacking something.

    Only our own piece moved, and it moved off `from_square`, so any target a
    stationary slider has gained must have been revealed by that departure.
    Landing on a square can only ever block lines, never open them, so a
    gained target is sufficient evidence -- no line geometry needed.
    """
    for piece_type in _SLIDER_DIRECTIONS:
        for origin in after.pieces(piece_type, mover):
            if origin == move.to_square:
                continue  # the piece that moved is not a discovery

            gained = after.attacks(origin) - before.attacks(origin)
            for square in gained:
                victim = after.piece_at(square)
                if victim is None or victim.color == mover:
                    continue
                if victim.piece_type == chess.KING:
                    return MotifHit(
                        motif=Motif.DISCOVERED_ATTACK,
                        squares=[_name(origin), _name(move.from_square), _name(square)],
                        description=(
                            f"moving off {_name(move.from_square)} uncovers a check from the "
                            f"{PIECE_NAMES[piece_type]} on {_name(origin)}"
                        ),
                    )
                if PIECE_VALUE[victim.piece_type] >= PIECE_VALUE[chess.KNIGHT]:
                    return MotifHit(
                        motif=Motif.DISCOVERED_ATTACK,
                        squares=[_name(origin), _name(move.from_square), _name(square)],
                        description=(
                            f"moving off {_name(move.from_square)} uncovers an attack from the "
                            f"{PIECE_NAMES[piece_type]} on {_name(origin)} against the "
                            f"{_piece_name(after, square)} on {_name(square)}"
                        ),
                    )
    return None


def _detect_hanging(board: chess.Board, mover: chess.Color) -> List[MotifHit]:
    """
    Enemy pieces the mover can simply take and keep.

    material_at_risk needs the mover to be the side to move, so this works on
    a probe board with the turn flipped. That is illegal when it would leave
    the other king in check, hence the validity guard -- and it is built once
    rather than per square, because generating legal moves is the expensive
    part of the whole motif pass.
    """
    probe = board.copy(stack=False)
    probe.turn = mover
    if not probe.is_valid():
        return []

    hits: List[MotifHit] = []
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None or piece.color == mover or piece.piece_type == chess.KING:
            continue
        if not board.attackers(mover, square):
            continue
        gain = material_at_risk(probe, square)
        if gain > HANGING_THRESHOLD:
            hits.append(MotifHit(
                motif=Motif.HANGING_PIECE,
                squares=[_name(square)],
                description=(
                    f"the {_piece_name(board, square)} on {_name(square)} is hanging, "
                    f"worth {gain} pawns"
                ),
            ))
    return hits


def _detect_sacrifice(before: chess.Board, move: chess.Move) -> Optional[MotifHit]:
    """
    Material handed over voluntarily.

    Soundness is not decided here -- geometry cannot know whether a sacrifice
    works. The analysis layer combines this with the evaluation to tell a
    brilliancy from a blunder.
    """
    swing = static_exchange_eval(before, move)
    if swing > -SACRIFICE_THRESHOLD:
        return None
    piece = before.piece_at(move.from_square)
    name = PIECE_NAMES.get(piece.piece_type, "piece") if piece else "piece"
    return MotifHit(
        motif=Motif.SACRIFICE,
        squares=[_name(move.from_square), _name(move.to_square)],
        description=(
            f"the {name} goes to {_name(move.to_square)} where it can be taken, "
            f"giving up {abs(swing)} pawns of material"
        ),
    )


def _detect_mate_pattern(board: chess.Board) -> Optional[MotifHit]:
    """Back-rank and smothered mate, the two patterns worth naming."""
    if not board.is_checkmate():
        return None

    king_square = board.king(board.turn)
    if king_square is None:
        return None

    checkers = board.checkers()
    if len(checkers) != 1:
        return None
    checker = chess.square(chess.square_file(list(checkers)[0]), chess.square_rank(list(checkers)[0]))
    checker_piece = board.piece_at(checker)
    if checker_piece is None:
        return None

    back_rank = 0 if board.turn == chess.WHITE else 7
    on_back_rank = chess.square_rank(king_square) == back_rank

    if checker_piece.piece_type == chess.KNIGHT:
        # Smothered: every escape square blocked by the king's own men.
        neighbours = list(board.attacks(king_square))
        if neighbours and all(
            (board.piece_at(sq) is not None and board.piece_at(sq).color == board.turn)
            for sq in neighbours
        ):
            return MotifHit(
                motif=Motif.SMOTHERED_MATE,
                squares=[_name(king_square), _name(checker)],
                description=(
                    f"smothered mate -- the king on {_name(king_square)} is boxed in by "
                    f"its own pieces and the knight on {_name(checker)} delivers mate"
                ),
            )

    if on_back_rank and checker_piece.piece_type in (chess.ROOK, chess.QUEEN):
        if chess.square_rank(checker) == back_rank:
            return MotifHit(
                motif=Motif.BACK_RANK_MATE,
                squares=[_name(king_square), _name(checker)],
                description=(
                    f"back-rank mate -- the {PIECE_NAMES[checker_piece.piece_type]} on "
                    f"{_name(checker)} mates the king trapped on its own back rank"
                ),
            )
    return None


def _standing_motifs(board: chess.Board, mover: chess.Color) -> List[MotifHit]:
    """Every STANDING_MOTIFS pattern true of `board` from the mover's side."""
    return _detect_pins_and_skewers(board, mover) + _detect_hanging(board, mover)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def detect_motifs(before: chess.Board, move: chess.Move) -> List[MotifHit]:
    """
    Every pattern created by playing `move` in position `before`.

    Ordered roughly by how much a commentator would care: mate first, then
    the tactic that produced it, then the material consequences.
    """
    mover = before.turn
    after = before.copy(stack=False)
    after.push(move)

    hits: List[MotifHit] = []

    mate = _detect_mate_pattern(after)
    if mate:
        hits.append(mate)

    if len(after.checkers()) >= 2:
        king_square = after.king(after.turn)
        hits.append(MotifHit(
            motif=Motif.DOUBLE_CHECK,
            squares=[_name(sq) for sq in after.checkers()] +
                    ([_name(king_square)] if king_square is not None else []),
            description="double check -- only a king move can answer it",
        ))

    fork = _detect_fork(after, move, mover)
    if fork:
        hits.append(fork)

    discovered = _detect_discovered_attack(before, after, move, mover)
    if discovered:
        hits.append(discovered)

    sacrifice = _detect_sacrifice(before, move)
    if sacrifice:
        hits.append(sacrifice)

    if move.promotion is not None:
        hits.append(MotifHit(
            motif=Motif.PROMOTION,
            squares=[_name(move.to_square)],
            description=(
                f"the pawn reaches {_name(move.to_square)} and becomes a "
                f"{PIECE_NAMES.get(move.promotion, 'queen')}"
            ),
        ))
    elif before.is_en_passant(move):
        hits.append(MotifHit(
            motif=Motif.EN_PASSANT,
            squares=[_name(move.from_square), _name(move.to_square)],
            description=f"the pawn captures en passant on {_name(move.to_square)}",
        ))
    else:
        piece = before.piece_at(move.from_square)
        if piece is not None and piece.piece_type == chess.PAWN:
            rank = chess.square_rank(move.to_square)
            if (mover == chess.WHITE and rank >= 5) or (mover == chess.BLACK and rank <= 2):
                hits.append(MotifHit(
                    motif=Motif.ADVANCED_PAWN,
                    squares=[_name(move.to_square)],
                    description=f"the pawn pushes deep into enemy territory on {_name(move.to_square)}",
                ))

    # Standing patterns are reported only when this move creates them. The
    # bishop pinning a knight stays pinned for five more moves, and saying so
    # every time is how commentary starts sounding like a machine reading a
    # database. Subtracting what was already true leaves the news.
    already = {
        (hit.motif, tuple(hit.squares)) for hit in _standing_motifs(before, mover)
    }
    hits.extend(
        hit for hit in _standing_motifs(after, mover)
        if (hit.motif, tuple(hit.squares)) not in already
    )

    return hits
