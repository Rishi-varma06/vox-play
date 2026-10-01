"""
Running the listener study: building sessions and collecting responses.

The protocol in docs/listener-study-protocol.md is the design; this is the
instrument. It exists because recruitment is the project's long pole, and
every minute of friction between "yes, I'll help" and usable data costs
participants.

Design constraints, all from how the study actually has to run
---------------------------------------------------------------
**No infrastructure.** A participant gets one HTML file. It works offline,
from a USB stick, from a Drive link, opened by double-clicking. There is no
server to keep up, nothing to deploy, and no account for anyone to create.

**Responses come back as a code.** The page produces a downloadable file and
a copy-paste string containing the same data. A participant returns whichever
is easier, and `decode_response` reads both. Asking people to install
something, or to be online at a particular moment, loses participants.

**Conditions are never named in the participant's hearing.** The page calls
them "Clip 1" through "Clip 4". The assignment lives in the session file and
the decoder maps it back afterwards.

**Each game is analysed once.** Conditions differ in beat selection and
script, not in engine analysis, so analysing per condition would cost four
times as much for identical numbers.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from . import beats as B
from .audio import narrate
from .engine import AnalysisConfig, analyse_game, get_engine
from .evaluation import COMMENTARY_THRESHOLDS
from .pgn_source import ParsedGame, load_lichess, load_pgn
from .player import _ply_nodes, build_player
from .script import ScriptStyle, generate_script

#: The arms, in a fixed order so Latin-square assignment is reproducible.
#: Named here as they are named in the protocol, never in the participant's
#: hearing -- the page shows "Clip 1".
CONDITIONS: Tuple[str, ...] = ("flat", "top_n", "system", "template")

#: Clips one participant rates. Four is about twenty-five minutes with the
#: rating blocks, which is as much as a volunteer will reliably give.
CLIPS_PER_PARTICIPANT = 4

#: The five rated items, in the protocol's wording. Keys are what land in the
#: CSV; the statements are what the participant reads.
ITEMS: Dict[str, str] = {
    "informativeness": "I understood what happened in this game.",
    "engagement": "I wanted to keep listening.",
    "accuracy": "What the commentary said seemed correct.",
    "pacing": "The commentary spent time on the right moments.",
    "naturalness": "It sounded like a person talking, not a report being read.",
}


class StudyError(RuntimeError):
    """Raised when a session cannot be built or a response cannot be read."""


class ClipAssignment(BaseModel):
    """One clip in one participant's session."""
    position: int
    game_id: str
    condition: str


class StudyResponse(BaseModel):
    """One participant's returned data, after decoding."""
    participant: str
    strength: str = ""
    watched_commentary: str = ""
    ratings: List[Dict[str, object]] = Field(default_factory=list)
    preferred_position: Optional[int] = None
    preference_reason: str = ""


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def assign(
    participant: int,
    games: Sequence[str],
    conditions: Sequence[str] = CONDITIONS,
    clips: int = CLIPS_PER_PARTICIPANT,
) -> List[ClipAssignment]:
    """
    Latin-square assignment of (game, condition) for one participant.

    Three things must hold at once, and the obvious implementation gets only
    two of them:

    1. Each participant hears every condition once, on a different game.
    2. Across participants, each condition appears equally often in each
       position -- otherwise order effects confound with condition.
    3. Across participants, each condition is paired equally often with each
       game -- otherwise "some games are just more interesting" confounds
       with condition.

    Rotating both indices by the participant number satisfies 1 and 2 and
    fails 3 completely: game and condition move in lockstep, so one condition
    is always heard on one game. That produces a dataset which looks entirely
    normal and in which the headline comparison is meaningless.

    The fix is the `shift` term, which advances the games by one extra step
    every time the condition cycle completes, so the pairing rotates
    independently. Over `len(conditions) * len(games)` participants every
    pair occurs equally often.
    """
    if not games:
        raise StudyError("no games supplied")
    if clips > len(games):
        raise StudyError(
            f"cannot give {clips} distinct games to a participant from "
            f"{len(games)} games"
        )

    shift = participant // len(conditions)
    return [
        ClipAssignment(
            position=i,
            game_id=games[(participant + i + shift) % len(games)],
            condition=conditions[(participant + i) % len(conditions)],
        )
        for i in range(clips)
    ]


def coverage(participants: int, games: Sequence[str],
             conditions: Sequence[str] = CONDITIONS) -> Dict[str, int]:
    """How many times each condition is heard across the whole study."""
    counts: Dict[str, int] = {c: 0 for c in conditions}
    for p in range(participants):
        for clip in assign(p, games, conditions):
            counts[clip.condition] += 1
    return counts


# ---------------------------------------------------------------------------
# Building clips
# ---------------------------------------------------------------------------

def sheet_for(game: ParsedGame, condition: str) -> B.BeatSheet:
    """The beat sheet for one arm of the study."""
    if condition == "flat":
        return B.select_every_move(game)
    if condition == "top_n":
        return B.select_top_n(game, 8)
    if condition in ("system", "template"):
        return B.select_beats(
            game, pacing=B.PacingConfig(budget_seconds=B.budget_for(game))
        )
    raise StudyError(f"unknown condition '{condition}'")


def generator_for(condition: str) -> str:
    """
    Which script generator an arm uses.

    Only `template` differs. It shares the system's pacing exactly and
    differs solely in that nothing wrote the prose, which is what isolates
    the language model's contribution from the facts being correct.
    """
    return "template" if condition == "template" else "auto"


def build_clip(
    game: ParsedGame,
    condition: str,
    out_dir: Path,
    style: ScriptStyle = ScriptStyle(),
    tts: str = "offline",
) -> Dict[str, object]:
    """Produce one condition's audio and player data for an analysed game."""
    sheet = sheet_for(game, condition)
    script = generate_script(game, sheet, generator=generator_for(condition),
                             style=style)
    audio = narrate(script, out_dir / condition, provider=tts)
    return {"game": game, "sheet": sheet, "script": script, "audio": audio}


def build_study(
    sources: Sequence[str],
    out_dir: Path | str,
    participants: int = 30,
    nodes: int = 400_000,
    engine: str = "auto",
    tts: str = "offline",
    conditions: Sequence[str] = CONDITIONS,
    progress: bool = True,
) -> Dict[str, object]:
    """
    Analyse every game once, render every condition, write every session.

    `sources` are PGN paths or Lichess links. Returns a manifest describing
    what was built, which doubles as the appendix the protocol asks for.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = AnalysisConfig(nodes=nodes, multipv=3, threads=1)

    analysed: Dict[str, ParsedGame] = {}
    clips: Dict[Tuple[str, str], Dict[str, object]] = {}

    with get_engine(engine, config) as backend:
        for source in sources:
            if progress:
                print(f"  analysing {source} ...")
            game = (load_pgn(source) if Path(source).exists()
                    else load_lichess(source))
            game = analyse_game(game, backend, thresholds=COMMENTARY_THRESHOLDS)
            analysed[game.meta.game_id] = game

            for condition in conditions:
                if progress:
                    print(f"      rendering {condition}")
                clips[(game.meta.game_id, condition)] = build_clip(
                    game, condition, out_dir / "clips" / game.meta.game_id, tts=tts
                )

    game_ids = list(analysed)
    manifest = {
        "games": [
            {
                "game_id": gid,
                "white": analysed[gid].meta.white,
                "black": analysed[gid].meta.black,
                "plies": analysed[gid].total_plies,
                "archetype": B.extract_arc(analysed[gid]).archetype.value,
            }
            for gid in game_ids
        ],
        "conditions": list(conditions),
        "participants": participants,
        "coverage": coverage(participants, game_ids, conditions),
        "sessions": [],
    }

    for participant in range(participants):
        plan = assign(participant, game_ids, conditions)
        page = out_dir / f"session_{participant + 1:03d}.html"
        write_session(participant + 1, plan, clips, analysed, page)
        manifest["sessions"].append({
            "participant": participant + 1,
            "file": page.name,
            "clips": [c.model_dump() for c in plan],
        })
        if progress and (participant + 1) % 10 == 0:
            print(f"  wrote {participant + 1}/{participants} sessions")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# Session pages
# ---------------------------------------------------------------------------

TEMPLATE = Path(__file__).parent / "study_template.html"


def write_session(
    participant: int,
    plan: Sequence[ClipAssignment],
    clips: Dict[Tuple[str, str], Dict[str, object]],
    games: Dict[str, ParsedGame],
    output: Path,
) -> Path:
    """
    Render one participant's session: four clips, blinded, in their order.

    The condition names travel in the payload so the decoder can map
    responses back, but nothing in the page ever displays them.
    """
    if not TEMPLATE.exists():
        raise StudyError(f"session template missing at {TEMPLATE}")

    payload_clips = []
    for clip in plan:
        built = clips.get((clip.game_id, clip.condition))
        if built is None:
            raise StudyError(f"no clip built for {clip.game_id}/{clip.condition}")
        audio = built["audio"]
        track = Path(audio.track_path)
        # The board travels with every clip so each condition is heard in
        # identical framing. Rating "pacing" without seeing the position it
        # is pacing against would be asking the wrong question.
        payload_clips.append({
            "position": clip.position,
            "gameId": clip.game_id,
            "condition": clip.condition,   # never displayed
            "audio": "data:audio/wav;base64,"
                     + base64.b64encode(track.read_bytes()).decode("ascii"),
            "durationMs": audio.total_ms,
            "plies": _ply_nodes(games[clip.game_id]),
            "cues": [
                {"text": cue.text, "startMs": cue.start_ms,
                 "durationMs": cue.duration_ms, "plies": list(cue.plies)}
                for cue in audio.cues
            ],
        })

    payload = {
        "participant": f"P{participant:03d}",
        "items": ITEMS,
        "clips": payload_clips,
    }
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")

    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__PARTICIPANT__", f"P{participant:03d}")
    html = html.replace("__PAYLOAD__", encoded)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


# ---------------------------------------------------------------------------
# Collecting responses
# ---------------------------------------------------------------------------

def decode_response(blob: str) -> StudyResponse:
    """
    Read a returned response, whether it arrived as a file or a pasted code.

    Both carry the same JSON; the code form is base64 so it survives being
    pasted into a chat message or an email body without reformatting.
    """
    text = blob.strip()
    if not text:
        raise StudyError("empty response")
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=False).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - surfaced as StudyError
            raise StudyError(f"could not decode response code: {exc}") from exc
    try:
        return StudyResponse(**json.loads(text))
    except Exception as exc:  # noqa: BLE001 - surfaced as StudyError
        raise StudyError(f"response is not valid study data: {exc}") from exc


def responses_to_rows(responses: Sequence[StudyResponse]) -> List[Dict[str, object]]:
    """
    Flatten to one row per rated clip -- the shape the analysis needs.

    Within-subjects tests pair on participant, so the participant id travels
    on every row rather than being implied by file order.
    """
    rows: List[Dict[str, object]] = []
    for response in responses:
        for rating in response.ratings:
            row: Dict[str, object] = {
                "participant": response.participant,
                "strength": response.strength,
                "watched_commentary": response.watched_commentary,
                "position": rating.get("position"),
                "game_id": rating.get("gameId"),
                "condition": rating.get("condition"),
                "preferred": int(
                    response.preferred_position == rating.get("position")
                ),
                "comment": rating.get("comment", ""),
            }
            for key in ITEMS:
                row[key] = rating.get(key)
            rows.append(row)
    return rows


def write_csv(rows: Sequence[Dict[str, object]], path: Path | str) -> Path:
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["participant", "strength", "watched_commentary", "position",
               "game_id", "condition", *ITEMS, "preferred", "comment"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def collect(folder: Path | str, out_csv: Path | str) -> Tuple[int, int, Path]:
    """
    Read every returned response in a folder into one CSV.

    Returns (responses read, rows written, path). Unreadable files are
    counted and skipped rather than aborting the batch -- losing a whole
    collection run to one corrupted paste would be absurd.
    """
    folder = Path(folder)
    responses: List[StudyResponse] = []
    failures = 0
    for path in sorted(folder.glob("*")):
        if path.is_dir() or path.suffix.lower() not in (".json", ".txt", ""):
            continue
        try:
            responses.append(decode_response(path.read_text(encoding="utf-8")))
        except StudyError:
            failures += 1
    rows = responses_to_rows(responses)
    written = write_csv(rows, out_csv)
    return len(responses), failures, written
