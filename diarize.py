"""
Local speaker diarization ("who spoke each line") via sherpa-onnx.

Fully local and optional: pure ONNX (no torch, no Hugging Face token, no account).
If the sherpa-onnx package or the model files are missing, diarization reports
itself unavailable and the rest of the app is unaffected.

Models (ungated GitHub release assets, fetched by scripts/get_diarization_models.py):
  segmentation.onnx   pyannote-segmentation-3-0 (MIT)
  embedding.onnx      3D-Speaker CAM++ en voxceleb (Apache-2.0)

Configuration:
  DIARIZATION_MODELS_DIR   directory holding segmentation.onnx + embedding.onnx
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

_REPO = Path(__file__).resolve().parent


def models_dir() -> Path:
    env = os.environ.get("DIARIZATION_MODELS_DIR")
    return Path(env) if env else _REPO / "models" / "diarization"


def model_paths() -> tuple[Path, Path]:
    d = models_dir()
    return d / "segmentation.onnx", d / "embedding.onnx"


def available() -> bool:
    """True only if the library imports and both model files are present."""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    seg, emb = model_paths()
    return seg.is_file() and emb.is_file()


def _read_wav_16k_mono(path: str):
    """Read a 16 kHz mono PCM wav into a float32 numpy array in [-1, 1].
    transcribe_core already writes exactly this format, so no resampling."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data


def _build(num_threads: int, num_speakers: int):
    import sherpa_onnx as so

    seg, emb = model_paths()
    clustering = (
        so.FastClusteringConfig(num_clusters=int(num_speakers))
        if num_speakers and num_speakers > 0
        else so.FastClusteringConfig(num_clusters=-1, threshold=0.5)
    )
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg)),
            num_threads=num_threads,
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(emb), num_threads=num_threads),
        clustering=clustering,
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    return so.OfflineSpeakerDiarization(config)


def diarize_wav(wav_path: str, num_speakers: int = 0, num_threads: int = 4) -> list[dict]:
    """Diarize a 16 kHz mono wav into speaker turns [{start, end, speaker}].
    num_speakers > 0 forces that many speakers; 0 auto-detects. Blocking; call in
    a worker thread."""
    diar = _build(num_threads, num_speakers)
    samples = _read_wav_16k_mono(wav_path)
    result = diar.process(samples).sort_by_start_time()
    return [
        {"start": round(float(s.start), 3), "end": round(float(s.end), 3), "speaker": int(s.speaker)}
        for s in result
    ]


def label_for(speaker_index: int) -> str:
    return f"Speaker {int(speaker_index) + 1}"


def speaker_count(turns: list[dict]) -> int:
    return len({t["speaker"] for t in turns}) if turns else 0


def _best_speaker(start: float, end: float, turns: list[dict]):
    """The speaker whose turn overlaps [start, end] most; if none overlaps (a
    short segment in a gap), the nearest turn by midpoint."""
    best, best_overlap = None, 0.0
    for t in turns:
        overlap = min(end, t["end"]) - max(start, t["start"])
        if overlap > best_overlap:
            best_overlap, best = overlap, t["speaker"]
    if best is None:
        mid = (start + end) / 2.0
        best = min(turns, key=lambda t: abs(((t["start"] + t["end"]) / 2.0) - mid))["speaker"]
    return best


def assign_speakers(segments: list[dict], turns: list[dict]) -> list[dict]:
    """Attach a speaker index to each transcript segment (and its words) by the
    diarization turn it overlaps most. Pure logic, unit-testable with no model.
    Mutates and returns the same segment dicts. No-op if there are no turns."""
    if not turns:
        return segments
    for seg in segments:
        seg["speaker"] = _best_speaker(seg.get("start", 0.0), seg.get("end", 0.0), turns)
        for w in seg.get("words") or []:
            w["speaker"] = _best_speaker(w.get("start", 0.0), w.get("end", 0.0), turns)
    return segments
