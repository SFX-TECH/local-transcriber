"""
transcriber-worker: consumes the Redis queue and runs Whisper on each job.

Loop: block on the queue (BRPOP), mark the job running, transcribe the input from
shared storage with transcribe_core, write txt/srt/vtt/md/json artifacts back to
shared storage, and mark the job done (or error). KEDA scales the number of these
workers on queue length, down to zero when the queue is empty.

CPU mode (int8) by default. GPU mode is selected by the image and the device
self-test in transcribe_core (CUDA when available, automatic CPU fallback), so
this file is identical for both; only the Pod's image and resources differ.
"""

from __future__ import annotations

import os
import signal
import time

import transcribe_core as core
from service import queue as q

_STOP = False


def _handle_term(signum, frame):  # noqa: ANN001
    """On SIGTERM/SIGINT, stop after the current job so KEDA scale-down or a
    rolling update never kills a job mid-flight."""
    global _STOP
    _STOP = True
    print("[worker] stop signal received, will exit after the current job", flush=True)


signal.signal(signal.SIGTERM, _handle_term)
signal.signal(signal.SIGINT, _handle_term)


def process_job(r, job_id: str) -> str:
    """Run one job to completion. Returns 'done', 'error', or 'missing'.

    Factored out of the loop so tests can drive a single job with fakeredis.
    """
    job = q.get_job(r, job_id)
    if not job:
        print(f"[worker] job {job_id} not found, skipping", flush=True)
        return "missing"

    q.set_status(r, job_id, "running", started=time.time())
    input_path = job.get("input_path")
    model = job.get("model", "small")
    language = job.get("language", "auto")
    lang = None if language in ("auto", "", None) else language

    try:
        segments: list[dict] = []
        duration = 0.0
        detected = ""
        for ev in core.transcribe(input_path, model_name=model, language=lang):
            kind = ev.get("type")
            if kind == "language":
                detected = ev.get("language", "")
            elif kind == "segment":
                segments.append({"start": ev["start"], "end": ev["end"], "text": ev["text"]})
            elif kind == "done":
                segments = ev["segments"]
                duration = ev.get("duration", 0.0)
            elif kind == "error":
                raise RuntimeError(ev.get("message", "transcription error"))

        out_dir = q.output_dir(job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        title = os.path.splitext(os.path.basename(job.get("name", "transcript")))[0] or "transcript"
        contents = {
            "txt": core.to_txt(segments),
            "srt": core.to_srt(segments),
            "vtt": core.to_vtt(segments),
            "md": core.to_md(segments, title=title),
            "json": core.to_json(segments, duration=duration, title=title),
        }
        artifacts = {}
        for fmt, text in contents.items():
            path = out_dir / f"transcript.{fmt}"
            path.write_text(text, encoding="utf-8")
            artifacts[fmt] = str(path)

        q.set_result(r, job_id, segments, duration, detected, artifacts)
        print(f"[worker] job {job_id} done: {len(segments)} segment(s), "
              f"{duration:.1f}s audio", flush=True)
        return "done"
    except Exception as exc:  # noqa: BLE001
        q.set_status(r, job_id, "error", error=f"{type(exc).__name__}: {exc}")
        print(f"[worker] job {job_id} error: {exc}", flush=True)
        return "error"


def run(poll_timeout: int = 5) -> None:
    r = q.get_redis()
    print(f"[worker] started, waiting for jobs ({core.resolve_device()[2]})", flush=True)
    while not _STOP:
        job_id = q.dequeue(r, timeout=poll_timeout)
        if job_id is None:
            continue
        process_job(r, job_id)
    print("[worker] exiting cleanly", flush=True)


if __name__ == "__main__":
    run(poll_timeout=int(os.environ.get("WORKER_POLL_TIMEOUT", "5")))
