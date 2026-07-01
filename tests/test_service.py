"""
Unit tests for the queue-based service: the Redis queue contract and the worker
processing one job end to end. Uses fakeredis so no real Redis is needed, and the
tiny Whisper model on a short synthesized clip so it stays offline and fast.

Run:  pip install -r requirements-cpu.txt -r requirements-dev.txt
      pytest tests/ -v
"""

from __future__ import annotations

import json
import os
import subprocess

import fakeredis
import pytest

import transcribe_core as core
from service import queue as q
from service import worker


@pytest.fixture
def r():
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def shared(tmp_path, monkeypatch):
    # queue.shared_dir() reads SHARED_DIR at call time, so this redirects all
    # input/output paths under a throwaway temp dir.
    monkeypatch.setenv("SHARED_DIR", str(tmp_path))
    return tmp_path


def _synth(path: str) -> None:
    """~3s of speech (flite), tone fallback if the ffmpeg build lacks flite."""
    flite = [core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "flite=text='Worker unit test clip.':voice=slt", "-t", "3",
             "-ar", "16000", "-ac", "1", path]
    try:
        subprocess.run(flite, check=True, capture_output=True)
        if os.path.getsize(path) > 0:
            return
    except Exception:
        pass
    subprocess.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=220:duration=3", "-ar", "16000", "-ac", "1", path],
                   check=True)


def test_queue_roundtrip(r):
    """enqueue / dequeue / status transitions on the Redis structures."""
    q.create_job(r, "j1", "a.wav", "tiny", "en", "/x/a.wav")
    assert q.get_job(r, "j1")["status"] == "queued"

    q.enqueue(r, "j1")
    assert q.queue_length(r) == 1

    assert q.dequeue(r, timeout=1) == "j1"
    assert q.queue_length(r) == 0
    assert q.dequeue(r, timeout=1) is None  # empty queue

    q.set_status(r, "j1", "running", started=123)
    assert q.get_job(r, "j1")["status"] == "running"


def test_worker_missing_job(r):
    """A job id with no hash is skipped, not crashed on."""
    assert worker.process_job(r, "nope") == "missing"


def test_worker_process_job(r, shared):
    """Full path: enqueue, worker consumes, transcribes, writes 5 artifacts."""
    job_id = "job-unit-1"
    in_dir = q.input_dir(job_id)
    in_dir.mkdir(parents=True, exist_ok=True)
    clip = in_dir / "clip.wav"
    _synth(str(clip))

    q.create_job(r, job_id, "clip.wav", "tiny", "en", str(clip))
    q.enqueue(r, job_id)

    popped = q.dequeue(r, timeout=2)
    assert popped == job_id

    assert worker.process_job(r, popped) == "done"

    job = q.get_job(r, job_id)
    assert job["status"] == "done"
    assert float(job["duration"]) > 0
    segments = json.loads(job["segments"])

    out_dir = q.output_dir(job_id)
    for fmt in q.ARTIFACT_FORMATS:
        p = out_dir / f"transcript.{fmt}"
        assert p.exists(), f"{fmt} artifact missing"

    # JSON artifact is structurally valid and agrees with the stored segments.
    data = json.loads((out_dir / "transcript.json").read_text(encoding="utf-8"))
    assert data["segment_count"] == len(segments)
    # VTT always carries its header even with no speech.
    assert (out_dir / "transcript.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    # When the clip yields speech, the text artifacts have content.
    if segments:
        assert (out_dir / "transcript.txt").stat().st_size > 0
        assert (out_dir / "transcript.srt").stat().st_size > 0
