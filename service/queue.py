"""
Redis-backed job queue and job state for the Kubernetes transcription service.

Two small Redis structures do all the work:

  - a LIST  (QUEUE_KEY) used as a FIFO work queue of job ids.
              LPUSH to enqueue, BRPOP to consume. KEDA scales workers on its
              length (LLEN).
  - a HASH  per job (JOB_PREFIX + id) holding status and metadata.

Every function takes an explicit redis client, so the whole module is trivially
testable with fakeredis and has no global connection. Nothing here imports
faster-whisper, which keeps the API process lightweight.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

QUEUE_KEY = "transcribe:queue"
JOB_PREFIX = "transcribe:job:"
EVENTS_PREFIX = "transcribe:events:"  # per-job live progress log (a Redis list)
DEVICE_KEY = "transcribe:device"      # device note a worker reports on startup
EVENTS_TTL = 3600                     # keep a job's event log for an hour

# Artifacts the worker writes and the API serves. DOCX is intentionally left to
# the interactive app; the service keeps the artifact set lean and text-only.
ARTIFACT_FORMATS = ("txt", "srt", "vtt", "md", "json")


def get_redis():
    """Build a redis client from the environment.

    Honors REDIS_URL first (redis://host:port/db), else REDIS_HOST / REDIS_PORT /
    REDIS_DB. decode_responses=True so we deal in str, not bytes.
    """
    import redis

    url = os.environ.get("REDIS_URL")
    if url:
        return redis.Redis.from_url(url, decode_responses=True)
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        db=int(os.environ.get("REDIS_DB", "0")),
        decode_responses=True,
    )


# --- shared storage layout ---------------------------------------------------
def shared_dir() -> Path:
    return Path(os.environ.get("SHARED_DIR", "/data"))


def input_dir(job_id: str) -> Path:
    return shared_dir() / "inputs" / job_id


def output_dir(job_id: str) -> Path:
    return shared_dir() / "outputs" / job_id


def job_key(job_id: str) -> str:
    return JOB_PREFIX + job_id


# --- queue + state -----------------------------------------------------------
def create_job(r, job_id: str, name: str, model: str, language: str,
               input_path) -> None:
    r.hset(job_key(job_id), mapping={
        "id": job_id,
        "status": "queued",
        "name": name,
        "model": model,
        "language": language,
        "input_path": str(input_path),
        "created": str(time.time()),
        "error": "",
    })


def enqueue(r, job_id: str) -> None:
    r.lpush(QUEUE_KEY, job_id)


def queue_length(r) -> int:
    return int(r.llen(QUEUE_KEY))


def dequeue(r, timeout: int = 5) -> Optional[str]:
    """Blocking pop of the next job id (FIFO with LPUSH). None when no job.

    A blocking BRPOP normally returns None on timeout, but it can also surface
    the wait as a redis TimeoutError, for example when SIGTERM interrupts the
    socket read while KEDA is scaling the worker down. Treat that as "no job this
    cycle" so the loop simply re-checks its stop flag and shuts down cleanly with
    exit 0, instead of crashing with an unhandled exception (exit 1).
    """
    import redis

    try:
        res = r.brpop(QUEUE_KEY, timeout=timeout)
    except redis.exceptions.TimeoutError:
        return None
    return res[1] if res else None


def set_status(r, job_id: str, status: str, **extra) -> None:
    mapping = {"status": status}
    mapping.update({k: str(v) for k, v in extra.items()})
    r.hset(job_key(job_id), mapping=mapping)


def set_result(r, job_id: str, segments, duration: float,
               language: str, artifacts: dict) -> None:
    r.hset(job_key(job_id), mapping={
        "status": "done",
        "segments": json.dumps(segments, ensure_ascii=False),
        "duration": str(round(float(duration or 0.0), 2)),
        "detected_language": language or "",
        "artifacts": json.dumps(artifacts),
        "finished": str(time.time()),
    })


def get_job(r, job_id: str) -> Optional[dict]:
    data = r.hgetall(job_key(job_id))
    return data or None


# --- live progress log -------------------------------------------------------
# A worker appends each transcribe event to a per-job Redis list; the API relays
# that list over SSE. Using a list (not pub/sub) makes the stream race free: a
# client that connects late still reads every event from the start.
def events_key(job_id: str) -> str:
    return EVENTS_PREFIX + job_id


def push_event(r, job_id: str, event: dict) -> None:
    key = events_key(job_id)
    r.rpush(key, json.dumps(event))
    r.expire(key, EVENTS_TTL)


def read_events(r, job_id: str, start: int = 0) -> list:
    """Return the job's events from index `start` onward, as dicts."""
    raw = r.lrange(events_key(job_id), start, -1)
    return [json.loads(x) for x in raw]


def set_device_note(r, note: str) -> None:
    r.set(DEVICE_KEY, note)


def get_device_note(r) -> str:
    return r.get(DEVICE_KEY) or ""
