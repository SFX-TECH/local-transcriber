"""
Local transcription core: ffmpeg audio extraction + faster-whisper inference.

Everything runs on this machine. No file-size limits, no network, no paywall.
The device is auto-resolved once (CUDA float16 if the GPU works, else CPU int8),
with a real self-test so a brand-new GPU that CTranslate2 can't drive falls back
to the CPU instead of crashing. Models are cached after first load.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, asdict
from typing import Iterator, Optional

import numpy as np
from faster_whisper import WhisperModel

# --- ffmpeg / ffprobe discovery ----------------------------------------------
# Resolution order for each tool, so the app stays portable with no
# machine-specific paths baked in:
#   1. An explicit override env var (FFMPEG_PATH / FFPROBE_PATH), pointing at
#      either the executable itself or a directory that contains it.
#   2. The system PATH (shutil.which).
def _tool(name: str) -> str:
    override = os.environ.get(f"{name.upper()}_PATH", "").strip()
    if override:
        if os.path.isdir(override):
            found = shutil.which(name, path=override)
        else:
            found = override if os.path.isfile(override) else shutil.which(override)
        if found:
            return found
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(
        f"{name} not found. Install ffmpeg and make sure it is on your PATH, or "
        f"set the {name.upper()}_PATH environment variable to its location "
        f"(https://ffmpeg.org/download.html)."
    )


FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")

# --- device resolution (once) -------------------------------------------------
MODELS = ["tiny", "base", "small", "medium", "large-v3"]

_device_lock = threading.Lock()
_DEVICE: Optional[str] = None
_COMPUTE: Optional[str] = None
_DEVICE_NOTE: str = ""
_model_cache: dict[str, WhisperModel] = {}


def resolve_device() -> tuple[str, str, str]:
    """Return (device, compute_type, human_note); CUDA if it really works, else CPU."""
    global _DEVICE, _COMPUTE, _DEVICE_NOTE
    with _device_lock:
        if _DEVICE is not None:
            return _DEVICE, _COMPUTE, _DEVICE_NOTE
        try:
            probe = WhisperModel("tiny", device="cuda", compute_type="float16")
            # Real kernel launch on 1s of silence: this is what trips an
            # unsupported GPU / missing cuDNN, not the constructor.
            segs, _ = probe.transcribe(
                np.zeros(16000, dtype=np.float32), language="en", beam_size=1
            )
            list(segs)
            del probe
            _DEVICE, _COMPUTE = "cuda", "float16"
            _DEVICE_NOTE = "GPU (CUDA, float16)"
        except Exception as exc:  # noqa: BLE001
            _DEVICE, _COMPUTE = "cpu", "int8"
            short = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            _DEVICE_NOTE = f"CPU (int8) -- GPU unavailable: {short}"
        return _DEVICE, _COMPUTE, _DEVICE_NOTE


def _get_model(model_name: str) -> WhisperModel:
    device, compute, _ = resolve_device()
    key = f"{model_name}:{device}"
    with _device_lock:
        m = _model_cache.get(key)
        if m is None:
            m = WhisperModel(
                model_name,
                device=device,
                compute_type=compute,
                cpu_threads=min(16, os.cpu_count() or 8) if device == "cpu" else 0,
            )
            _model_cache[key] = m
        return m


# --- audio ---------------------------------------------------------------------
def media_duration(path: str) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(out.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def extract_audio(input_path: str, wav_path: str) -> None:
    """Decode any video/audio container to 16kHz mono PCM wav (what Whisper wants).

    Raises a clean, human-readable RuntimeError when ffmpeg cannot read the file
    (corrupt, not a media file, or no audio track) so the UI shows something
    useful instead of a raw subprocess traceback.
    """
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-i", input_path, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", wav_path],
        capture_output=True, text=True, check=False,
    )
    ok = proc.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 0
    if not ok:
        err_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
        detail = err_lines[-1].strip() if err_lines else "ffmpeg produced no audio output."
        raise RuntimeError(
            "Could not read audio from this file. It may be corrupt, not a media "
            "file, or have no audio track. ffmpeg said: " + detail
        )


# --- transcription -------------------------------------------------------------
@dataclass
class Segment:
    start: float
    end: float
    text: str


def transcribe(
    input_path: str,
    model_name: str = "small",
    language: Optional[str] = None,  # None = auto-detect
) -> Iterator[dict]:
    """
    Yields progress events for streaming to the UI:
      {"type": "status",   "message": str, "device": str}
      {"type": "language", "language": str, "probability": float}
      {"type": "segment",  "start": float, "end": float, "text": str, "progress": 0..1}
      {"type": "done",     "segments": [Segment...], "duration": float}
      {"type": "error",    "message": str}
    """
    if model_name not in MODELS:
        model_name = "small"

    device, _, note = resolve_device()
    yield {"type": "status", "message": f"Using {note}. Loading model '{model_name}'...",
           "device": note}

    tmpdir = tempfile.mkdtemp(prefix="transcribe_")
    wav = os.path.join(tmpdir, "audio.wav")
    try:
        yield {"type": "status", "message": "Extracting audio with ffmpeg...",
               "device": note}
        extract_audio(input_path, wav)
        total = media_duration(wav) or media_duration(input_path) or 0.0

        model = _get_model(model_name)
        yield {"type": "status", "message": "Transcribing...", "device": note}

        segments_iter, info = model.transcribe(
            wav,
            language=language,
            beam_size=5,
            vad_filter=True,  # skip long silences -> faster, cleaner
            vad_parameters={"min_silence_duration_ms": 500},
        )
        if total <= 0:
            total = float(getattr(info, "duration", 0.0)) or 0.0
        yield {"type": "language", "language": info.language,
               "probability": round(float(info.language_probability), 3)}

        collected: list[Segment] = []
        for seg in segments_iter:
            s = Segment(start=round(seg.start, 3), end=round(seg.end, 3),
                        text=seg.text.strip())
            collected.append(s)
            prog = max(0.0, min(1.0, (seg.end / total) if total else 0.0))
            yield {"type": "segment", "start": s.start, "end": s.end,
                   "text": s.text, "progress": round(prog, 4)}

        yield {"type": "done",
               "segments": [asdict(s) for s in collected],
               "duration": round(total, 2)}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- output formats ------------------------------------------------------------
def _ts(seconds: float, sep: str = ",") -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_txt(segments: list[dict]) -> str:
    return "\n".join(s["text"].strip() for s in segments).strip() + "\n"


def to_srt(segments: list[dict]) -> str:
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_ts(s['start'])} --> {_ts(s['end'])}")
        lines.append(s["text"].strip())
        lines.append("")
    return "\n".join(lines)


def to_vtt(segments: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for s in segments:
        lines.append(f"{_ts(s['start'], '.')} --> {_ts(s['end'], '.')}")
        lines.append(s["text"].strip())
        lines.append("")
    return "\n".join(lines)
