"""
Demo driver for the local-transcriber Kubernetes service.

Synthesizes a few tiny speech clips with ffmpeg (no real user data), submits them
to the transcriber API, and polls until they are done, printing the worker pod
count as it goes so you can watch KEDA scale from zero. Port-forwards the API
itself, so `python scripts/demo.py` is one command.

Usage:
  python scripts/demo.py --release lt --jobs 5 --model tiny
  python scripts/demo.py --api http://localhost:8000   # skip port-forward
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import requests

CLIP_TEXTS = [
    "Hello from the first transcription job on Kubernetes.",
    "This worker was scaled up from zero by KEDA on queue depth.",
    "Each job runs Whisper and writes text, subtitles, and captions.",
    "The queue is Redis, the workers are autoscaled, nothing runs idle.",
    "When the queue drains, the workers scale back down to zero.",
    "Self-hosted speech to text, no cloud and no client data.",
]


def find_ffmpeg() -> str:
    override = os.environ.get("FFMPEG_PATH", "").strip()
    if override:
        if os.path.isfile(override):
            return override
        found = shutil.which("ffmpeg", path=override) if os.path.isdir(override) else None
        if found:
            return found
    found = shutil.which("ffmpeg")
    if not found:
        sys.exit("ffmpeg not found. Install it or set FFMPEG_PATH.")
    return found


def synth_clip(ffmpeg: str, text: str, path: str, seconds: int = 3) -> None:
    """Write ~`seconds` of speech (flite voice), falling back to a tone.

    Longer clips make each job take longer, which keeps the queue deep enough
    for KEDA to scale to several workers (otherwise one fast worker drains a
    short burst before the autoscaler reacts)."""
    flite = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"flite=text='{text}':voice=slt", "-t", str(seconds),
             "-ar", "16000", "-ac", "1", path]
    try:
        subprocess.run(flite, check=True, capture_output=True)
        if os.path.getsize(path) > 0:
            return
    except Exception:
        pass
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency=220:duration={seconds}", "-ar", "16000", "-ac", "1", path],
                   check=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def worker_pod_count(release: str, namespace: str) -> str:
    """Return 'running/total' worker pods, best effort."""
    try:
        out = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace,
             "-l", f"app.kubernetes.io/instance={release},app.kubernetes.io/component=worker",
             "--no-headers"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        if not out:
            return "0/0"
        lines = out.splitlines()
        running = sum(1 for ln in lines if " Running " in f" {ln} ")
        return f"{running}/{len(lines)}"
    except Exception:
        return "?"


def start_port_forward(release: str, namespace: str, local_port: int):
    svc = f"svc/{release}-local-transcriber-api"
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, svc, f"{local_port}:8000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def wait_healthy(api: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(api + "/healthz", timeout=3).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", default="lt")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--secs", type=int, default=3, help="length of each synth clip")
    ap.add_argument("--api", default="", help="API base URL; skips port-forward if set")
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    pf = None
    api = args.api
    if not api:
        port = free_port()
        api = f"http://127.0.0.1:{port}"
        print(f"[demo] port-forwarding {args.release}-local-transcriber-api -> {api}")
        pf = start_port_forward(args.release, args.namespace, port)

    try:
        if not wait_healthy(api):
            print("[demo] API did not become healthy. Is the chart installed and KEDA running?")
            return 1

        workdir = tempfile.mkdtemp(prefix="lt_demo_")
        n = max(1, args.jobs)
        print(f"[demo] submitting {n} job(s) with model={args.model}, {args.secs}s clips")
        ids = []
        for i in range(n):
            clip = os.path.join(workdir, f"clip{i + 1}.wav")
            synth_clip(ffmpeg, CLIP_TEXTS[i % len(CLIP_TEXTS)], clip, seconds=args.secs)
            with open(clip, "rb") as fh:
                resp = requests.post(api + "/jobs",
                                     files={"file": (f"clip{i + 1}.wav", fh, "audio/wav")},
                                     data={"model": args.model, "language": "en"}, timeout=30)
            resp.raise_for_status()
            jid = resp.json()["job_id"]
            ids.append(jid)
            print(f"[demo]   queued job {i + 1}: {jid}")

        print("[demo] polling (watch the worker pods scale up from zero)...")
        done = {}
        start = time.time()
        while len(done) < len(ids) and time.time() - start < 600:
            for jid in ids:
                if jid in done:
                    continue
                job = requests.get(f"{api}/jobs/{jid}", timeout=10).json()
                if job.get("status") in ("done", "error"):
                    done[jid] = job
            pods = worker_pod_count(args.release, args.namespace)
            print(f"[demo]   workers={pods}  done={len(done)}/{len(ids)}", flush=True)
            if len(done) < len(ids):
                time.sleep(3)

        print("\n[demo] results:")
        ok = 0
        for i, jid in enumerate(ids, 1):
            job = done.get(jid, {})
            status = job.get("status", "timeout")
            if status == "done":
                ok += 1
                text = (job.get("text") or "").replace("\n", " ")
                print(f"  job {i} [{jid}] done: {text[:70]!r}")
            else:
                print(f"  job {i} [{jid}] {status}: {job.get('error', '')[:80]}")
        print(f"\n[demo] {ok}/{len(ids)} jobs transcribed. "
              f"Workers will scale back to zero after the cooldown.")
        return 0 if ok == len(ids) else 1
    finally:
        if pf:
            pf.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
