"""
Local Transcriber -- a tiny local web app around faster-whisper.

Run:  run.bat   (or: .venv\\Scripts\\python -m uvicorn app:app --port 8765)
Then open http://localhost:8765 , drop a video/audio file, get a transcript.
Everything stays on this machine. No size limits, no network, no paywall.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    Response,
    StreamingResponse,
    JSONResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import transcribe_core as core

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUT = BASE / "output"
UPLOADS.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)

app = FastAPI(title="Local Transcriber")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

# In-memory job table (single-user local app).
JOBS: dict[str, dict] = {}
_DONE = object()  # sentinel pushed onto a job queue when finished

# Bounded worker pool: at most MAX_CONCURRENT transcriptions run at once; the
# rest wait in _pending and start as slots free. Two concurrent CPU jobs share
# the same cores, so the default is deliberately small. Set MAX_CONCURRENT=1 for
# strictly sequential, or higher on a strong GPU box.
MAX_CONCURRENT = max(1, int(os.environ.get("MAX_CONCURRENT", "2")))
_pending: "queue.Queue[str]" = queue.Queue()

# File-type validation. We do not maintain an exhaustive allow-list (ffmpeg
# reads far more than we could enumerate); instead we fast-reject the obvious
# non-media types and let ffmpeg be the final judge for anything else.
NON_MEDIA_EXTS = {
    ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".gz", ".tar", ".exe", ".dll", ".msi", ".bat",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".ico",
    ".csv", ".json", ".html", ".htm", ".md", ".py", ".js", ".css",
}


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9 ._-]+", "_", stem).strip() or "transcript"
    return stem[:80]


def _run_job(job_id: str, media_path: Path, model: str, language: str | None) -> None:
    job = JOBS[job_id]
    q: queue.Queue = job["queue"]
    out_dir = OUTPUT / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for event in core.transcribe(str(media_path), model_name=model, language=language):
            if event["type"] == "done":
                segs = event["segments"]
                job["segments"] = segs
                job["duration"] = event["duration"]
                stem = _safe_stem(job["name"])
                (out_dir / f"{stem}.txt").write_text(core.to_txt(segs), encoding="utf-8")
                (out_dir / f"{stem}.srt").write_text(core.to_srt(segs), encoding="utf-8")
                (out_dir / f"{stem}.vtt").write_text(core.to_vtt(segs), encoding="utf-8")
                job["stem"] = stem
                job["status"] = "done"
            elif event["type"] == "error":
                job["status"] = "error"
            q.put(event)
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        q.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        q.put(_DONE)
        # tidy the (potentially large) source copy; transcripts live in output/
        try:
            media_path.unlink(missing_ok=True)
            media_path.parent.rmdir()  # remove now-empty uploads/<job_id>/
        except OSError:
            pass


def _worker_loop() -> None:
    """One pool worker: pull the next queued job and run it to completion.

    A slot is held for the whole transcription, so at most MAX_CONCURRENT jobs
    run at once and the rest stay 'queued' until a worker frees up.
    """
    while True:
        job_id = _pending.get()
        job = JOBS.get(job_id)
        if job is None:
            continue
        job["status"] = "running"
        # Nudge the stream so the tab flips from queued to running promptly,
        # even before the (possibly slow) model load emits its first status.
        job["queue"].put({"type": "status", "message": "Starting..."})
        _run_job(job_id, job["_media_path"], job["_model"], job["_lang"])


for _ in range(MAX_CONCURRENT):
    threading.Thread(target=_worker_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((BASE / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/device")
async def device() -> JSONResponse:
    """Resolve GPU vs CPU once and report it, so the page can show a badge at
    rest. Runs in a worker thread because the first call loads a tiny model and
    does a real CUDA self-test, which would otherwise block the event loop."""
    loop = asyncio.get_event_loop()
    _, _, note = await loop.run_in_executor(None, core.resolve_device)
    return JSONResponse({"device": note, "gpu": note.lower().startswith("gpu")})


@app.post("/api/jobs")
async def create_job(
    file: UploadFile,
    model: str = Form("small"),
    language: str = Form("auto"),
) -> JSONResponse:
    # Validate before we spend time/disk streaming a multi-GB upload.
    if not file.filename:
        raise HTTPException(400, "No file was selected.")
    ext = Path(file.filename).suffix.lower()
    if ext in NON_MEDIA_EXTS:
        raise HTTPException(
            400,
            f"'{ext}' is not a media file. Choose a video or audio file "
            f"(mp4, mov, mkv, webm, mp3, wav, m4a, and similar).",
        )

    safe_name = Path(file.filename).name or "input"  # strip any path components
    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOADS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    media_path = job_dir / safe_name

    # Stream the upload to disk in chunks (handles multi-GB files without OOM).
    size = 0
    with open(media_path, "wb") as fh:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            size += len(chunk)
    if size == 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, "That file is empty (0 bytes).")

    lang = None if language in ("auto", "", None) else language
    jq: queue.Queue = queue.Queue()
    JOBS[job_id] = {
        "queue": jq,
        "status": "queued",
        "name": safe_name,
        "segments": [],
        "duration": 0.0,
        "stem": _safe_stem(safe_name),
        # stashed so a pool worker can run it when a slot frees up
        "_media_path": media_path,
        "_model": model,
        "_lang": lang,
    }
    # Announce the wait immediately; a pool worker flips it to running.
    jq.put({"type": "queued"})
    _pending.put(job_id)
    return JSONResponse({"job_id": job_id, "size": size, "name": safe_name})


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    q: queue.Queue = job["queue"]

    async def gen():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is _DONE:
                yield "event: end\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/download/{fmt}")
async def download(job_id: str, fmt: str) -> FileResponse:
    if fmt not in ("txt", "srt", "vtt"):
        raise HTTPException(400, "Format must be txt, srt, or vtt.")
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "Transcript not ready.")
    path = OUTPUT / job_id / f"{job['stem']}.{fmt}"
    if not path.exists():
        raise HTTPException(404, "File missing.")
    return FileResponse(str(path), filename=path.name, media_type="text/plain")


# --- DOCX export -------------------------------------------------------------
# Text formats (txt/srt/vtt/md/json) are generated in the browser so they honor
# inline transcript edits. DOCX needs python-docx, so it is built here from the
# (possibly edited) segments the page posts back. Still fully local.
class _ExportSegment(BaseModel):
    start: float = 0.0
    end: float = 0.0
    text: str = ""


class _DocxRequest(BaseModel):
    segments: list[_ExportSegment]
    title: str = "Transcript"
    with_timestamps: bool = True


@app.post("/api/export/docx")
async def export_docx(req: _DocxRequest) -> Response:
    try:
        from docx import Document  # lazy: app still runs if docx is absent
    except ImportError:
        raise HTTPException(500, "python-docx is not installed on the server.")

    doc = Document()
    doc.add_heading(req.title or "Transcript", level=1)
    for seg in req.segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        para = doc.add_paragraph()
        if req.with_timestamps:
            stamp = para.add_run(f"[{core._mmss(seg.start)}] ")
            stamp.bold = True
        para.add_run(text)

    from io import BytesIO
    buf = BytesIO()
    doc.save(buf)
    filename = f"{_safe_stem(req.title or 'transcript')}.docx"
    return Response(
        content=buf.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/jobs/{job_id}/folder")
async def reveal_folder(job_id: str) -> JSONResponse:
    """Return the on-disk folder holding this job's transcripts."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    return JSONResponse({"folder": str(OUTPUT / job_id)})


# --- AI insights (optional, via a local Ollama) ------------------------------
# Summary, chapters, and action items for a finished transcript. Entirely local
# and optional: if Ollama is not running, these endpoints report it and the rest
# of the app is unaffected.
class _SummarizeRequest(BaseModel):
    segments: list[_ExportSegment]
    model: str | None = None


@app.get("/api/ai/status")
async def ai_status() -> JSONResponse:
    import ai_summary  # lazy: keeps startup light, import is network-free

    loop = asyncio.get_event_loop()
    return JSONResponse(await loop.run_in_executor(None, ai_summary.status))


@app.post("/api/ai/summarize")
async def ai_summarize(req: _SummarizeRequest) -> JSONResponse:
    import ai_summary

    loop = asyncio.get_event_loop()
    st = await loop.run_in_executor(None, ai_summary.status)
    if not st["available"]:
        return JSONResponse(
            {
                "ok": False,
                "error": st.get("error", "Ollama is not reachable."),
                "hint": "Start Ollama (run 'ollama serve') and pull a model, e.g. 'ollama pull qwen3:8b'.",
                "base_url": st.get("base_url", ""),
            }
        )
    model = (req.model or st.get("default") or "").strip()
    if not model:
        return JSONResponse(
            {"ok": False, "error": "No Ollama chat model is available.", "hint": "Pull one, e.g. 'ollama pull qwen3:8b'."}
        )
    segs = [s.model_dump() for s in req.segments]
    result = await loop.run_in_executor(None, ai_summary.summarize, segs, model)
    return JSONResponse(result)
