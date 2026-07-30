"""
Download the ungated ONNX models for local speaker diarization.

  Segmentation: sherpa-onnx-pyannote-segmentation-3-0   (MIT)
  Embedding:    3D-Speaker CAM++ en voxceleb 16k        (Apache-2.0)

Both are plain GitHub release assets from k2-fsa/sherpa-onnx: no Hugging Face
token, no account, no gated user conditions. Everything stays local.

The files land in the diarization model directory (default: models/diarization/,
which is gitignored). Override with DIARIZATION_MODELS_DIR.

  python scripts/get_diarization_models.py
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMB_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
)


def models_dir() -> Path:
    env = os.environ.get("DIARIZATION_MODELS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "models" / "diarization"


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url.rsplit('/', 1)[-1]} ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "local-transcriber"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def main() -> None:
    out = models_dir()
    out.mkdir(parents=True, exist_ok=True)
    seg = out / "segmentation.onnx"
    emb = out / "embedding.onnx"

    if emb.exists() and emb.stat().st_size > 0:
        print(f"  embedding.onnx already present ({emb})")
    else:
        _download(EMB_URL, emb)

    if seg.exists() and seg.stat().st_size > 0:
        print(f"  segmentation.onnx already present ({seg})")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tarball = Path(tmp) / "seg.tar.bz2"
            _download(SEG_URL, tarball)
            with tarfile.open(tarball, "r:bz2") as tf:
                member = next(m for m in tf.getmembers() if m.name.endswith("model.onnx"))
                src = tf.extractfile(member)
                if src is None:
                    raise RuntimeError("segmentation model.onnx not found in the archive")
                with open(seg, "wb") as fh:
                    shutil.copyfileobj(src, fh)

    print(f"\nDiarization models ready in {out}")
    print(f"  segmentation: {seg.stat().st_size // 1024} KB")
    print(f"  embedding:    {emb.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
