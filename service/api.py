"""
transcriber-api: a thin FastAPI front door for the queue-based service.

  POST /jobs                     accept an upload, write it to shared storage,
                                 push a job id onto the Redis queue, return the id.
  GET  /jobs/{id}                return status; when done, the transcript text,
                                 detected language, duration, and artifact links.
  GET  /jobs/{id}/artifacts/{fmt}  serve one of the txt/srt/vtt/md/json files.
  GET  /healthz                  liveness/readiness (also checks Redis).
  GET  /                         a small page to submit a file and poll.

This process NEVER runs inference. The KEDA-scaled workers do that. It only
talks to Redis and shared storage, so it stays small and starts fast.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from service import queue as q

BASE = Path(__file__).resolve().parent

app = FastAPI(title="Local Transcriber API")

_STATIC = BASE / "static"
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


@app.post("/jobs")
async def create_job(
    file: UploadFile,
    model: str = Form("small"),
    language: str = Form("auto"),
) -> JSONResponse:
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
    return JSONResponse({"job_id": job_id, "status": "queued", "size": size})


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
