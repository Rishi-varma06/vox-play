"""
Self-contained HTML player: board, audio, and timeline in one file.

A web player rather than an mp4, for three reasons. It needs no ffmpeg, which
this project deliberately avoids depending on. It is scrubbable, so a demo can
jump straight to the turning point instead of waiting for it. And it is one
file you can double-click or email, which an mp4 plus a timeline is not.

The board follows the audio through the timeline produced by audio.narrate():
each cue knows which half-moves it describes, and within a cue the board walks
those plies evenly, so a beat covering eight half-moves actually moves instead
of freezing on the last one.

Audio is inlined as a data URI by default. It roughly doubles the file size,
but a single file that always works beats two files that break the moment one
is moved.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import chess

from .audio import CommentaryAudio
from .beats import BeatSheet
from .pgn_source import ParsedGame
from .script import _players
from .speech import ascii_fold, speak_san

TEMPLATE = Path(__file__).parent / "player_template.html"


class PlayerError(RuntimeError):
    """Raised when a player page cannot be built."""


def _check_square(fen: str) -> Optional[str]:
    """The square of a king that is currently in check, if any."""
    board = chess.Board(fen)
    if not board.is_check():
        return None
    king = board.king(board.turn)
    return chess.square_name(king) if king is not None else None


def _ply_nodes(game: ParsedGame) -> List[Dict[str, Any]]:
    """
    One entry per position, indexed by ply.

    Entry 0 is the starting position, so the player can index by ply number
    directly rather than juggling an offset.
    """
    start = game.events[0]
    nodes: List[Dict[str, Any]] = [{
        "ply": 0,
        "moveNumber": 1,
        "color": "white",
        "san": None,
        "fen": start.state.fen,
        "winPct": start.state.win_pct_white,
        "material": 0,
    }]

    for move in game.moves:
        analysis = move.analysis
        node: Dict[str, Any] = {
            "ply": move.ply,
            "moveNumber": move.move_number,
            "color": move.color.value,
            "san": move.san,
            "fen": move.fen_after,
            "from": move.from_square,
            "to": move.to_square,
            "winPct": move.state.win_pct_white,
            "material": move.state.material_balance,
            "checkSquare": _check_square(move.fen_after) if move.is_check else None,
        }
        if analysis:
            node["quality"] = analysis.quality.value
            if analysis.best_move_san and not analysis.is_best:
                node["best"] = analysis.best_move_san
            squares: List[str] = []
            for hit in analysis.motifs:
                squares.extend(hit.squares)
            # Keep order, drop repeats -- several motifs often share a square.
            node["motifSquares"] = list(dict.fromkeys(squares))
        nodes.append(node)

    return nodes


def _subtitle(game: ParsedGame, sheet: BeatSheet, audio: CommentaryAudio) -> str:
    meta = game.meta
    bits = [f"<b>{_players(game)}</b>"]
    if meta.opening:
        bits.append(ascii_fold(meta.opening))
    bits.append(f"{game.total_plies} half-moves")
    bits.append(f"{audio.total_ms // 1000}s of commentary")
    if sheet.arc.turning_point_ply:
        by_ply = {m.ply: m for m in game.moves}
        turning = by_ply.get(sheet.arc.turning_point_ply)
        if turning:
            bits.append(f"turning point {turning.san}")
    return " &middot; ".join(bits)


def build_player(
    game: ParsedGame,
    sheet: BeatSheet,
    audio: CommentaryAudio,
    output: Path | str,
    inline_audio: bool = True,
) -> Path:
    """Render a standalone player page for one narrated game."""
    if not TEMPLATE.exists():
        raise PlayerError(f"player template missing at {TEMPLATE}")

    track = Path(audio.track_path)
    if not track.exists():
        raise PlayerError(f"audio track not found: {track}")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if inline_audio:
        encoded = base64.b64encode(track.read_bytes()).decode("ascii")
        audio_src = f"data:audio/wav;base64,{encoded}"
    else:
        try:
            audio_src = track.relative_to(output.parent).as_posix()
        except ValueError:
            audio_src = track.as_uri()

    payload = {
        "subtitle": _subtitle(game, sheet, audio),
        "archetype": sheet.arc.archetype.value.replace("_", " "),
        "totalMs": audio.total_ms,
        "plies": _ply_nodes(game),
        "cues": [
            {
                "index": cue.index,
                "type": cue.beat_type.value,
                "text": cue.text,
                "plies": list(cue.plies),
                "startMs": cue.start_ms,
                "durationMs": cue.duration_ms,
            }
            for cue in audio.cues
        ],
    }

    meta = game.meta
    headline = f"{ascii_fold(meta.white or 'White')} vs {ascii_fold(meta.black or 'Black')}"

    # "</script>" inside the JSON would close the tag early; escaping "<" is
    # the standard way to embed JSON in a script element safely.
    encoded_payload = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")

    html = TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("__TITLE__", f"Voxplay - {headline}"),
        ("__HEADLINE__", headline),
        ("__AUDIO_SRC__", audio_src),
        ("__PAYLOAD__", encoded_payload),
    ):
        html = html.replace(token, value)

    output.write_text(html, encoding="utf-8")
    return output
