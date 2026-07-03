"""
transcriber-api: a thin FastAPI front door for the queue-based service.

It serves the same premium web UI as the single-process app and speaks the same
/api/* contract, so one frontend drives either backend unchanged. Here, uploads
become queued jobs that the KEDA-scaled workers transcribe, and this process
relays each worker's live progress to the browser over SSE.

  GET  /                           the web UI (shared with the single-process app).
  GET  /api/device                 the device a worker reported (GPU or CPU badge).
  POST /api/jobs                   accept an upload, enqueue it, return the job id.
  GET  /api/jobs/{id}/events       Server-Sent Events: live status and segments.
  GET  /api/jobs/{id}/folder       where a job's transcripts are written.
  POST /api/export/docx            build a Word document from (edited) segments.
  POST /jobs, GET /jobs/{id}, GET /jobs/{id}/artifacts/{fmt}
                                   the original JSON API (kept for scripts/demo).
  GET  /healthz                    liveness/readiness (also checks Redis).

This process NEVER runs inference. The workers do. It only talks to Redis and
shared storage, so it stays small and starts fast.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from service import queue as q

BASE = Path(__file__).resolve().parent
# The premium web UI lives at the repo root, shared with the single-process app.
_STATIC = BASE.parent / "static"

app = FastAPI(title="Local Transcriber API")
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

# Lazily create the redis client so importing this module (e.g. in tests) never
# requires a live Redis.
_redis = None


def r():
    global _redis
    if _redis is None:
        _redis = q.get_redis()
    return _redis


def _mmss(seconds: float) -> str:
    s = max(0, int(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9 ._-]+", "_", Path(name).stem).strip() or "transcript"
    return stem[:80]


@app.get("/healthz")
async def healthz() -> JSONResponse:
    try:
        r().ping()
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    return JSONResponse({"ok": ok}, status_code=200 if ok else 503)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    page = _STATIC / "index.html"
    if page.is_file():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Local Transcriber API</h1><p>POST a file to /jobs.</p>")


# --- upload ------------------------------------------------------------------
async def _store_upload(file: UploadFile, model: str, language: str):
    """Stream an upload to shared storage, create the job, and enqueue it."""
    if not file.filename:
        raise HTTPException(400, "No file provided.")

    job_id = uuid.uuid4().hex[:12]
    safe_name = Path(file.filename).name or "input"
    in_dir = q.input_dir(job_id)
    in_dir.mkdir(parents=True, exist_ok=True)
    dest = in_dir / safe_name

    size = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            size += len(chunk)
    if size == 0:
        raise HTTPException(400, "Empty upload.")

    lang = "auto" if language in ("auto", "", None) else language
    q.create_job(r(), job_id, safe_name, model, lang, dest)
    q.enqueue(r(), job_id)
    return job_id, safe_name, size


@app.post("/jobs")
async def create_job(
    file: UploadFile,
    model: str = Form("small"),
    language: str = Form("auto"),
) -> JSONResponse:
    """Original JSON API, kept for scripts/demo."""
    job_id, _name, size = await _store_upload(file, model, language)
    return JSONResponse({"job_id": job_id, "status": "queued", "size": size})


@app.post("/api/jobs")
async def api_create_job(
    file: UploadFile,
    model: str = Form("small"),
    language: str = Form("auto"),
) -> JSONResponse:
    """Upload endpoint the web UI uses (mirrors the single-process app)."""
    job_id, name, size = await _store_upload(file, model, language)
    q.push_event(r(), job_id, {"type": "queued"})
    return JSONResponse({"job_id": job_id, "name": name, "size": size})


@app.get("/api/device")
async def api_device() -> JSONResponse:
    note = q.get_device_note(r()) or "Queue worker (device shown once a worker runs)"
    return JSONResponse({"device": note, "gpu": note.lower().startswith("gpu")})


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    job = q.get_job(r(), job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")

    out = {
        "job_id": job_id,
        "status": job.get("status"),
        "name": job.get("name"),
        "model": job.get("model"),
    }
    if job.get("status") == "done":
        segments = json.loads(job.get("segments", "[]"))
        out["duration"] = float(job.get("duration", 0) or 0)
        out["language"] = job.get("detected_language", "")
        out["segments"] = segments
        out["text"] = "\n".join(s["text"] for s in segments).strip()
        out["artifacts"] = {
            fmt: f"/jobs/{job_id}/artifacts/{fmt}" for fmt in q.ARTIFACT_FORMATS
        }
    elif job.get("status") == "error":
        out["error"] = job.get("error", "")
    return JSONResponse(out)


@app.get("/jobs/{job_id}/artifacts/{fmt}")
async def get_artifact(job_id: str, fmt: str) -> FileResponse:
    if fmt not in q.ARTIFACT_FORMATS:
        raise HTTPException(400, "Format must be one of: " + ", ".join(q.ARTIFACT_FORMATS))
    path = q.output_dir(job_id) / f"transcript.{fmt}"
    if not path.exists():
        raise HTTPException(404, "Artifact not ready.")
    media = "application/json" if fmt == "json" else "text/plain"
    return FileResponse(str(path), filename=f"{job_id}.{fmt}", media_type=media)


# --- live progress relay -----------------------------------------------------
def _final_from_hash(job: dict) -> dict:
    """Synthesize a terminal SSE event from the stored job hash, for clients that
    connect after the live event log has already expired."""
    if job.get("status") == "error":
        return {"type": "error", "message": job.get("error", "Transcription failed.")}
    segs = json.loads(job.get("segments", "[]"))
    return {
        "type": "done",
        "segments": segs,
        "duration": float(job.get("duration", 0) or 0),
        "detected": job.get("detected_language", ""),
    }


@app.get("/api/jobs/{job_id}/events")
async def api_events(job_id: str) -> StreamingResponse:
    """Relay a job's live event log (populated by the worker) as SSE."""
    async def gen():
        loop = asyncio.get_event_loop()
        cursor = 0
        idle = 0
        while True:
            events = await loop.run_in_executor(None, q.read_events, r(), job_id, cursor)
            if events:
                idle = 0
                cursor += len(events)
                terminal = False
                for ev in events:
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev.get("type") in ("done", "error"):
                        terminal = True
                if terminal:
                    break
            else:
                idle += 1
                # No log yet, or it expired. If the hash is already terminal,
                # replay a final event from it and stop.
                if idle >= 10 and cursor == 0:
                    job = await loop.run_in_executor(None, q.get_job, r(), job_id)
                    if job and job.get("status") in ("done", "error"):
                        yield f"data: {json.dumps(_final_from_hash(job))}\n\n"
                        break
                if idle > 4000:  # ~20 min safety net against a vanished worker
                    break
            await asyncio.sleep(0.3)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/folder")
async def api_folder(job_id: str) -> JSONResponse:
    return JSONResponse({"folder": str(q.output_dir(job_id))})


# --- DOCX export -------------------------------------------------------------
# Text formats are built in the browser so they honor inline edits; DOCX needs
# python-docx, so it is built here from the (possibly edited) segments the page
# posts back. Still fully local.
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
        from docx import Document  # lazy: the API still runs if docx is absent
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
            stamp = para.add_run(f"[{_mmss(seg.start)}] ")
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


# --- AI insights (optional, via a local Ollama) ------------------------------
# Summary, chapters, and action items for a finished transcript. This runs in the
# API process (never the workers) and is entirely optional: if Ollama is not
# reachable, the endpoints say so and the rest of the app is unaffected.
class _SummarizeRequest(BaseModel):
    segments: list[_ExportSegment]
    model: str | None = None


@app.get("/api/ai/status")
async def ai_status() -> JSONResponse:
    import ai_summary  # lazy: import is network-free; probe runs off the loop

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
