"""
HTTP API for the chess commentary pipeline.

The CLI runs the whole pipeline in one shot, which is right for a terminal
and wrong for a person: analysis takes about thirty seconds, and the script
that comes out may need a word changed before anyone wants to hear it spoken.

So the work is split in two, with the human in the middle:

    POST /api/chess/games                 upload -> analyse -> script  (slow)
    POST /api/chess/games/{id}/verify     re-check edited text         (instant)
    POST /api/chess/games/{id}/narrate    text -> speech               (fast)

Narration is never implicit. A caller reads the script, edits it if they
want, and only then asks for audio. That ordering is the point: speech is
the expensive, hard-to-skim artefact, and committing to it before the words
are right wastes the listener's time more than it wastes ours.

Everything long-running happens on a worker thread and is polled through
GET /api/chess/games/{id}. Engine work is capped by a semaphore because each
job owns a Stockfish process, and letting a browser open ten of them is a
good way to make a demo machine unresponsive.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field as PydField

router = APIRouter(prefix="/api/chess", tags=["chess"])

#: Where job artefacts land. One directory per job, so two uploads of the
#: same game cannot overwrite each other's audio.
DATA_ROOT = Path("output/web")

#: Concurrent engine jobs. Each one owns a Stockfish process pinned to a
#: thread; more than a couple on a laptop makes every job slower rather than
#: finishing any of them sooner.
_ENGINE_SLOTS = threading.Semaphore(2)

#: Upload ceiling. A single game's PGN is a few kilobytes; anything over this
#: is a database dump, and we only ever read the first game out of it.
MAX_PGN_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

QUEUED = "queued"
RUNNING = "running"
READY = "ready"
NARRATING = "narrating"
NARRATED = "narrated"
ERROR = "error"


@dataclass
class Job:
    """One game moving through the pipeline. Mutated only under `lock`."""

    job_id: str
    status: str = QUEUED
    stage: str = "queued"
    pct: float = 0.0
    log: List[str] = field(default_factory=list)
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    # Pipeline artefacts, filled in as the stages complete.
    game: Any = None
    sheet: Any = None
    script: Any = None
    report: Any = None
    audio: Any = None
    player_path: Optional[str] = None
    cost_usd: Optional[float] = None
    model: Optional[str] = None
    pacing: str = "paced"
    baselines: Dict[str, Any] = field(default_factory=dict)
    timings: Dict[str, float] = field(default_factory=dict)

    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def dir(self) -> Path:
        return DATA_ROOT / self.job_id

    def note(
        self,
        message: str,
        stage: Optional[str] = None,
        pct: Optional[float] = None,
    ) -> None:
        with self.lock:
            self.log.append(message)
            if stage is not None:
                self.stage = stage
            if pct is not None:
                self.pct = round(pct, 1)

    def fail(self, message: str) -> None:
        with self.lock:
            self.status = ERROR
            self.error = message
            self.stage = "failed"
            self.log.append("ERROR: " + message)


_JOBS: Dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _new_job() -> Job:
    job = Job(job_id=uuid.uuid4().hex[:12])
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
    job.dir.mkdir(parents=True, exist_ok=True)
    return job


def _get_job(job_id: str) -> Job:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "No such game. It may have expired on a restart.")
    return job


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _move_rows(game) -> List[Dict[str, Any]]:
    rows = []
    for move in game.moves:
        analysis = move.analysis
        rows.append({
            "ply": move.ply,
            "move_number": move.move_number,
            "san": move.san,
            "uci": move.uci,
            "color": move.color.value,
            "fen_after": move.fen_after,
            "quality": analysis.quality.value if analysis else None,
            "win_pct_after": analysis.win_pct_after if analysis else None,
            "accuracy": analysis.accuracy if analysis else None,
            "is_best": analysis.is_best if analysis else None,
            "best_move_san": analysis.best_move_san if analysis else None,
            "motifs": [
                {"motif": hit.motif.value, "description": hit.description}
                for hit in (analysis.motifs if analysis else [])
            ],
        })
    return rows


def _accuracy_rows(game) -> List[Dict[str, Any]]:
    from voxplay.chessgame.evaluation import ERROR_QUALITIES
    from voxplay.chessgame.models import Color
    from voxplay.chessgame.speech import ascii_fold

    rows = []
    pairs = ((Color.WHITE, game.meta.white), (Color.BLACK, game.meta.black))
    for colour, name in pairs:
        played = [m for m in game.moves if m.color == colour and m.analysis]
        if not played:
            continue
        errors = [m for m in played if m.analysis.quality in ERROR_QUALITIES]
        rows.append({
            "color": colour.value,
            "name": ascii_fold(name or colour.value),
            "accuracy": round(sum(m.analysis.accuracy for m in played) / len(played), 1),
            "errors": len(errors),
            "error_moves": [m.san for m in errors[:8]],
        })
    return rows


def _findings_payload(report) -> List[Dict[str, Any]]:
    if report is None:
        return []
    return [
        {
            "check": f.check,
            "beat_index": f.beat_index,
            "beat_type": f.beat_type.value,
            "severity": f.severity,
            "message": f.message,
        }
        for f in report.findings
    ]


def _group_by_beat(findings: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(finding["beat_index"], []).append(finding)
    return grouped


def _script_payload(script, report) -> List[Dict[str, Any]]:
    if script is None:
        return []
    by_beat = _group_by_beat(_findings_payload(report))
    return [
        {
            "index": index,
            "beat_type": line.beat_type.value,
            "plies": list(line.plies),
            "text": line.text,
            "words": line.words,
            "est_seconds": round(line.est_seconds, 1),
            "findings": by_beat.get(index, []),
        }
        for index, line in enumerate(script.lines)
    ]


def _audio_payload(job: "Job") -> Optional[Dict[str, Any]]:
    audio = job.audio
    if audio is None:
        return None
    return {
        "voice": audio.voice,
        "total_seconds": round(audio.total_ms / 1000, 1),
        "estimated_seconds": round(audio.estimated_ms / 1000, 1),
        "drift_ratio": round(audio.drift_ratio, 2),
        "track_url": "/api/chess/games/" + job.job_id + "/audio",
        "player_url": (
            "/api/chess/games/" + job.job_id + "/player" if job.player_path else None
        ),
        "cues": [
            {
                "index": cue.index,
                "beat_type": cue.beat_type.value,
                "plies": list(cue.plies),
                "start_seconds": round(cue.start_ms / 1000, 2),
                "duration_seconds": round(cue.duration_ms / 1000, 2),
            }
            for cue in audio.cues
        ],
    }


def snapshot(job: Job) -> Dict[str, Any]:
    """Everything the browser needs, safe to call at any point in the run."""
    with job.lock:
        payload: Dict[str, Any] = {
            "job_id": job.job_id,
            "status": job.status,
            "stage": job.stage,
            "pct": job.pct,
            "log": list(job.log),
            "error": job.error,
            "timings": dict(job.timings),
            "cost_usd": job.cost_usd,
            "model": job.model,
            "baselines": dict(job.baselines),
        }

        if job.game is not None:
            from voxplay.chessgame.speech import ascii_fold

            meta = job.game.meta
            payload["meta"] = {
                "white": ascii_fold(meta.white or "?"),
                "black": ascii_fold(meta.black or "?"),
                "result": meta.result.value,
                "eco": meta.eco,
                "opening": ascii_fold(meta.opening or ""),
                "date": meta.date,
                "event": ascii_fold(meta.event or ""),
                "total_plies": job.game.total_plies,
            }
            payload["players"] = _accuracy_rows(job.game)
            payload["moves"] = _move_rows(job.game)
            payload["motif_count"] = sum(
                len(m.analysis.motifs) for m in job.game.moves if m.analysis
            )
            opening = getattr(job.game, "opening_report", None)
            payload["opening_report"] = (
                {
                    "eco": opening.eco,
                    "name": opening.name,
                    "describe": opening.describe(),
                }
                if opening is not None
                else None
            )

        if job.sheet is not None and job.game is not None:
            arc = job.sheet.arc
            by_ply = {m.ply: m for m in job.game.moves}
            turning = by_ply.get(arc.turning_point_ply)
            payload["arc"] = {
                "archetype": arc.archetype.value,
                "turning_point_san": turning.san if turning else None,
                "turning_point_ply": arc.turning_point_ply,
                "lead_changes": arc.lead_changes,
            }
            payload["pacing"] = {
                "beats": len(job.sheet.beats),
                "seconds": round(job.sheet.total_seconds, 1),
                "mode": job.pacing,
                "strategy": job.sheet.strategy,
            }

        if job.script is not None:
            payload["script"] = _script_payload(job.script, job.report)
            payload["script_meta"] = {
                "generator": job.script.generator,
                "total_words": job.script.total_words,
                "total_seconds": round(job.script.total_seconds, 1),
            }

        if job.report is not None:
            payload["verification"] = {
                "clean": job.report.clean,
                "errors": len(job.report.errors),
                "warnings": len(job.report.findings) - len(job.report.errors),
                "errors_per_hundred_beats": round(
                    job.report.errors_per_hundred_beats, 1
                ),
            }

        payload["audio"] = _audio_payload(job)
        return payload


# ---------------------------------------------------------------------------
# The pipeline, on a worker thread
# ---------------------------------------------------------------------------

#: How much of the game gets talked about. "paced" is the system this
#: project is actually about; the other two were written as baselines to
#: measure it against, and are offered here because a listener who wants
#: every move is asking for the baseline on purpose rather than by mistake.
PACING_MODES = ("paced", "every_move", "top_n")


@dataclass
class RunOptions:
    engine: str = "auto"
    nodes: int = 1_000_000
    threads: int = 1
    generator: str = "auto"
    persona: str = "analytical"
    audience: int = 1200
    budget: Optional[float] = None
    openings: bool = True
    pacing: str = "paced"


def _run_pipeline(job: Job, source: Dict[str, str], options: RunOptions) -> None:
    """Analyse, pace and script. Audio is deliberately not part of this."""
    from voxplay.chessgame import beats as B
    from voxplay.chessgame.engine import AnalysisConfig, analyse_game, get_engine
    from voxplay.chessgame.evaluation import COMMENTARY_THRESHOLDS
    from voxplay.chessgame.pgn_source import load_lichess, parse_pgn_text
    from voxplay.chessgame.script import ScriptError, ScriptStyle, get_generator
    from voxplay.chessgame.speech import ascii_fold
    from voxplay.chessgame.verify import verify_script

    # -- load ------------------------------------------------------------
    job.note("Reading the game", stage="loading", pct=2)
    if source.get("lichess_url"):
        game = load_lichess(source["lichess_url"])
    else:
        game = parse_pgn_text(source["pgn_text"])

    meta = game.meta
    job.note(
        ascii_fold(meta.white or "?") + " vs " + ascii_fold(meta.black or "?")
        + "  " + meta.result.value
        + "  (" + str(game.total_plies) + " half-moves)"
    )
    with job.lock:
        job.game = game

    if game.total_plies == 0:
        raise ValueError("that PGN has no moves in it")

    # -- analyse ---------------------------------------------------------
    positions = game.total_plies + 1
    job.note(
        "Analysing " + str(positions) + " positions at "
        + format(options.nodes, ",") + " nodes each",
        stage="analysing",
        pct=5,
    )
    config = AnalysisConfig(nodes=options.nodes, multipv=3, threads=options.threads)
    started = time.time()

    def on_progress(done: int, total: int) -> None:
        # 5% to 70% of the bar belongs to the engine; it is most of the wait.
        job.pct = round(5 + 65 * done / max(total, 1), 1)
        job.stage = "analysing position " + str(done) + " of " + str(total)

    with _ENGINE_SLOTS:
        with get_engine(options.engine, config) as engine:
            game = analyse_game(
                game,
                engine,
                thresholds=COMMENTARY_THRESHOLDS,
                progress=on_progress,
            )
            engine_name = engine.name
    elapsed = time.time() - started
    with job.lock:
        job.game = game
        job.timings["analysis_seconds"] = round(elapsed, 1)
    job.note(
        "Engine: " + engine_name + ", done in " + format(elapsed, ".1f") + "s",
        pct=70,
    )

    for row in _accuracy_rows(game):
        job.note(
            row["name"][:26] + "  accuracy " + format(row["accuracy"], ".1f")
            + "%  errors " + str(row["errors"])
        )

    # -- openings --------------------------------------------------------
    if options.openings:
        job.note("Consulting the opening book", stage="openings", pct=72)
        try:
            from voxplay.chessgame.openings import load_book, probe_opening

            book = load_book()
            game.opening_report = probe_opening(game, book)
            job.note(game.opening_report.describe())
        except Exception as exc:  # noqa: BLE001 - the book is optional
            job.note("Opening book unavailable (" + str(exc) + "); continuing")

    motifs = sum(len(m.analysis.motifs) for m in game.moves if m.analysis)
    job.note("Tactical motifs detected: " + str(motifs), pct=75)

    # -- pace ------------------------------------------------------------
    job.note("Choosing what to talk about", stage="pacing", pct=78)
    budget = options.budget or B.budget_for(game)
    paced = B.select_beats(game, pacing=B.PacingConfig(budget_seconds=budget))
    flat = B.select_every_move(game)
    topn = B.select_top_n(game, 8)

    # All three are computed either way: they cost nothing next to the engine
    # pass, and the comparison is reported whichever one the listener picked.
    sheet = {"paced": paced, "every_move": flat, "top_n": topn}[options.pacing]

    with job.lock:
        job.sheet = sheet
        job.pacing = options.pacing
        job.baselines = {
            "pacing": options.pacing,
            "selected_seconds": round(sheet.total_seconds),
            "selected_beats": len(sheet.beats),
            "paced_seconds": round(paced.total_seconds),
            "paced_beats": len(paced.beats),
            "every_move_seconds": round(flat.total_seconds),
            "every_move_beats": len(flat.beats),
            "top8_seconds": round(topn.total_seconds),
            "budget_seconds": round(budget),
        }

    if options.pacing == "every_move":
        job.note(
            "Full match: " + str(len(sheet.beats)) + " beats, "
            + format(sheet.total_seconds, ".0f") + "s ("
            + format(sheet.total_seconds / 60, ".1f") + " min) -- covering every "
            "half-move. The paced version of this game would run "
            + format(paced.total_seconds, ".0f") + "s.",
            pct=82,
        )
    else:
        job.note(
            str(len(sheet.beats)) + " beats, " + format(sheet.total_seconds, ".0f")
            + "s (budget " + format(budget, ".0f") + "s; every-move baseline would be "
            + format(flat.total_seconds, ".0f") + "s)",
            pct=82,
        )

    # -- script ----------------------------------------------------------
    job.note("Writing the commentary", stage="scripting", pct=85)
    style = ScriptStyle(persona=options.persona, audience_elo=options.audience)
    started = time.time()
    generator = get_generator(options.generator)
    try:
        script = generator.generate(game, sheet, style)
    except ScriptError as exc:
        # "auto" means whatever actually works. An explicit choice is a
        # request, and silently substituting a different generator would
        # misreport which one produced the text on screen.
        if options.generator != "auto":
            raise
        job.note("Claude unavailable (" + str(exc) + "); falling back to templates")
        generator = get_generator("template")
        script = generator.generate(game, sheet, style)

    usage = getattr(generator, "usage", None)
    with job.lock:
        job.script = script
        job.timings["script_seconds"] = round(time.time() - started, 1)
        job.model = getattr(generator, "model", None)
        if usage is not None and job.model:
            job.cost_usd = usage.cost(job.model)
    job.note(
        script.generator + ": " + str(script.total_words) + " words, "
        + format(script.total_seconds, ".0f") + "s spoken",
        pct=93,
    )

    # -- verify ----------------------------------------------------------
    job.note("Checking every claim against the facts", stage="verifying", pct=95)
    report = verify_script(script, game, sheet, spoiler_free=style.spoiler_free)
    with job.lock:
        job.report = report
    verdict = "clean" if report.clean else str(len(report.errors)) + " errors"
    job.note(
        "Verification: " + verdict + " ("
        + format(report.errors_per_hundred_beats, ".1f") + " errors per 100 beats, "
        + str(len(report.findings) - len(report.errors)) + " warnings)"
    )

    with job.lock:
        job.status = READY
        job.stage = "ready for review"
        job.pct = 100.0
    job.note("Script ready. Review the text, then turn it into audio.")


def _worker(job: Job, source: Dict[str, str], options: RunOptions) -> None:
    try:
        _run_pipeline(job, source, options)
    except Exception as exc:  # noqa: BLE001 - the browser needs the reason
        traceback.print_exc()
        job.fail(type(exc).__name__ + ": " + str(exc))


# ---------------------------------------------------------------------------
# Narration, also on a worker thread
# ---------------------------------------------------------------------------

def _edited_script(job: Job, texts: Optional[List[str]]):
    """
    Apply the reviewer's edits to the generated script.

    Returns a copy rather than mutating, so a failed narration leaves the job
    showing the text it actually verified.
    """
    from voxplay.chessgame.beats import WORDS_PER_SECOND
    from voxplay.chessgame.speech import normalise_for_speech

    script = job.script.model_copy(deep=True)
    if not texts:
        return script
    if len(texts) != len(script.lines):
        raise ValueError(
            "expected " + str(len(script.lines)) + " lines of text, got "
            + str(len(texts))
        )
    for line, text in zip(script.lines, texts):
        clean = normalise_for_speech(text)
        if not clean.strip():
            raise ValueError("a beat cannot be empty")
        line.text = clean
        line.words = len(clean.split())
        line.est_seconds = round(line.words / WORDS_PER_SECOND, 1)
    script.total_words = sum(line.words for line in script.lines)
    script.total_seconds = round(sum(line.est_seconds for line in script.lines), 1)
    return script


def _run_narration(job: Job, script, provider: str, build_player_page: bool) -> None:
    from voxplay.chessgame.audio import narrate, write_timeline
    from voxplay.chessgame.verify import verify_script

    job.note("Synthesising speech with the '" + provider + "' voice", stage="narrating")
    started = time.time()
    audio = narrate(script, job.dir, provider=provider)
    write_timeline(audio)
    elapsed = time.time() - started

    # The text that is about to be spoken is the text that gets verified, not
    # the text the model first produced. Editing is allowed; editing past the
    # gate is not.
    report = verify_script(script, job.game, job.sheet)

    with job.lock:
        job.script = script
        job.report = report
        job.audio = audio
        job.timings["audio_seconds"] = round(elapsed, 1)

    job.note(
        str(len(audio.cues)) + " clips in " + format(elapsed, ".1f")
        + "s; track runs " + format(audio.total_ms / 1000, ".0f") + "s (estimate "
        + format(audio.estimated_ms / 1000, ".0f") + "s, drift "
        + format(audio.drift_ratio, ".2f") + "x)"
    )

    if build_player_page:
        try:
            from voxplay.chessgame.player import build_player

            page = build_player(job.game, job.sheet, audio, job.dir / "player.html")
            with job.lock:
                job.player_path = str(page)
            size_mb = page.stat().st_size / (1024 * 1024)
            job.note(
                "Standalone player built (" + format(size_mb, ".1f")
                + " MB, self-contained)"
            )
        except Exception as exc:  # noqa: BLE001 - the audio still stands
            job.note("Player page unavailable (" + str(exc) + "); audio unaffected")

    with job.lock:
        job.status = NARRATED
        job.stage = "narrated"
        job.pct = 100.0


def _narration_worker(job: Job, script, provider: str, player: bool) -> None:
    try:
        _run_narration(job, script, provider, player)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        message = type(exc).__name__ + ": " + str(exc)
        with job.lock:
            job.status = READY  # the script survives a failed narration
            job.stage = "narration failed"
            job.error = message
            job.log.append("ERROR: " + message)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/games")
async def create_game(
    file: Optional[UploadFile] = File(None),
    pgn_text: Optional[str] = Form(None),
    lichess_url: Optional[str] = Form(None),
    engine: str = Form("auto"),
    nodes: int = Form(1_000_000),
    generator: str = Form("auto"),
    persona: str = Form("analytical"),
    audience: int = Form(1200),
    budget: Optional[float] = Form(None),
    openings: bool = Form(True),
    pacing: str = Form("paced"),
):
    """Accept a game and start analysing it. Returns immediately."""
    source: Dict[str, str] = {}
    if file is not None and file.filename:
        raw = await file.read()
        if len(raw) > MAX_PGN_BYTES:
            raise HTTPException(
                413, "that file is larger than " + str(MAX_PGN_BYTES // 1024) + " KB"
            )
        try:
            source["pgn_text"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            source["pgn_text"] = raw.decode("latin-1")
    elif pgn_text and pgn_text.strip():
        source["pgn_text"] = pgn_text
    elif lichess_url and lichess_url.strip():
        source["lichess_url"] = lichess_url.strip()
    else:
        raise HTTPException(400, "provide a PGN file, PGN text, or a Lichess link")

    if nodes < 1000 or nodes > 20_000_000:
        raise HTTPException(400, "nodes must be between 1,000 and 20,000,000")
    if pacing not in PACING_MODES:
        raise HTTPException(
            400, "pacing must be one of " + ", ".join(PACING_MODES)
        )

    options = RunOptions(
        engine=engine,
        nodes=nodes,
        generator=generator,
        persona=persona,
        audience=audience,
        budget=budget,
        openings=openings,
        pacing=pacing,
    )
    job = _new_job()
    with job.lock:
        job.status = RUNNING
    threading.Thread(
        target=_worker,
        args=(job, source, options),
        daemon=True,
        name="voxplay-chess-" + job.job_id,
    ).start()
    return {"job_id": job.job_id}


@router.get("/games")
async def list_games():
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [
        {
            "job_id": j.job_id,
            "status": j.status,
            "created_at": j.created_at,
            "white": (j.game.meta.white if j.game else None),
            "black": (j.game.meta.black if j.game else None),
        }
        for j in jobs
    ]


@router.get("/games/{job_id}")
async def get_game(job_id: str):
    return snapshot(_get_job(job_id))


class VerifyRequest(BaseModel):
    lines: List[str] = PydField(default_factory=list)


@router.post("/games/{job_id}/verify")
async def verify_edits(job_id: str, req: VerifyRequest):
    """
    Re-run the gate over edited text without synthesising anything.

    This is the same `verify_script` the pipeline runs, not a lighter
    browser-side approximation -- a gate that is stricter when nobody is
    looking would be worth very little.
    """
    from voxplay.chessgame.verify import verify_script

    job = _get_job(job_id)
    if job.script is None:
        raise HTTPException(409, "there is no script to check yet")
    try:
        script = _edited_script(job, req.lines)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    report = verify_script(script, job.game, job.sheet)
    by_beat = _group_by_beat(_findings_payload(report))
    return {
        "clean": report.clean,
        "errors": len(report.errors),
        "warnings": len(report.findings) - len(report.errors),
        "errors_per_hundred_beats": round(report.errors_per_hundred_beats, 1),
        "total_words": script.total_words,
        "total_seconds": round(script.total_seconds, 1),
        "beats": [
            {
                "index": index,
                "words": line.words,
                "est_seconds": round(line.est_seconds, 1),
                "findings": by_beat.get(index, []),
            }
            for index, line in enumerate(script.lines)
        ],
    }


#: Voices the studio offers. 'elevenlabs' is the only one that spends
#: money, so it is never the default -- it is chosen per narration, on the
#: screen where the script has already been read and approved.
VOICES = ("elevenlabs", "gtts", "offline", "mock")


class NarrateRequest(BaseModel):
    lines: Optional[List[str]] = None
    tts: str = "offline"
    player: bool = True


@router.post("/games/{job_id}/narrate")
async def narrate_game(job_id: str, req: NarrateRequest):
    """Turn the (possibly edited) script into speech."""
    job = _get_job(job_id)
    if req.tts not in VOICES:
        raise HTTPException(400, "voice must be one of " + ", ".join(VOICES))
    with job.lock:
        if job.script is None:
            raise HTTPException(409, "analyse a game first")
        if job.status == NARRATING:
            raise HTTPException(409, "already narrating")
        try:
            script = _edited_script(job, req.lines)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        job.status = NARRATING
        job.stage = "narrating"
        job.error = None
        job.audio = None
        job.player_path = None

    threading.Thread(
        target=_narration_worker,
        args=(job, script, req.tts, req.player),
        daemon=True,
        name="voxplay-narrate-" + job.job_id,
    ).start()
    return {"job_id": job.job_id, "status": NARRATING}


@router.get("/games/{job_id}/audio")
async def get_audio(job_id: str):
    job = _get_job(job_id)
    if job.audio is None:
        raise HTTPException(404, "no audio has been generated for this game")
    track = Path(job.audio.track_path)
    if not track.exists():
        raise HTTPException(404, "the audio track is missing from disk")
    return FileResponse(
        str(track), media_type="audio/wav", filename=track.stem + ".wav"
    )


@router.get("/games/{job_id}/player")
async def get_player(job_id: str):
    job = _get_job(job_id)
    if not job.player_path or not Path(job.player_path).exists():
        raise HTTPException(404, "no standalone player has been built")
    return FileResponse(
        job.player_path, media_type="text/html", filename="voxplay_player.html"
    )


@router.delete("/games/{job_id}")
async def delete_game(job_id: str):
    _get_job(job_id)
    with _JOBS_LOCK:
        _JOBS.pop(job_id, None)
    return {"deleted": job_id}
