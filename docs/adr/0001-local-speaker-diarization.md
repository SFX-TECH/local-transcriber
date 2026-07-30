# ADR 0001: Local speaker diarization via sherpa-onnx

Status: accepted
Date: 2026-07-30

## Context

We wanted "who spoke each line" (speaker diarization) attached to our existing
whisper segments and word timings, staying 100% local and free, without bloating
the base install. We evaluated the community options read-only before choosing.

## Options considered

| Candidate | License | Verdict | Reason |
|---|---|---|---|
| sherpa-onnx | Apache-2.0 | chosen | Pure ONNX, no torch, CPU-native, and the models are ungated GitHub release assets, so no Hugging Face token or accepted user conditions. |
| WhisperX | BSD-2 | rejected | Diarization is pyannote under the hood, which requires a Hugging Face account, an accepted gated user agreement, and a read token. Breaks the "100% local, zero-setup" promise. Also pulls torch. |
| whisper-diarization | BSD-2 | rejected | No token (NeMo, CC-BY-4.0), but drags in `nemo_toolkit[asr]` plus three git-installed deps and is GPU-oriented. Heaviest install, worst for a slim CPU image. |
| pyannote-audio (direct) | MIT code | rejected | Same Hugging Face gating as WhisperX; no advantage. |

## Decision

Use `sherpa-onnx` with two ungated models:

- Segmentation: `sherpa-onnx-pyannote-segmentation-3-0` (MIT), the pyannote
  segmentation net redistributed as an ONNX release asset (so we get the model
  without the Hugging Face gate).
- Embedding: `3D-Speaker CAM++ en voxceleb 16k` (Apache-2.0).

We avoid the `reverb-diarization` model variants: Rev's Reverb weights are
non-commercial-licensed and the source repo is gated.

## How it is wired

- `diarize.py` runs the offline pipeline (segmentation, embedding, clustering) to
  get `{start, end, speaker}` turns, then assigns a speaker to each transcript
  segment and word by time-overlap. Pure, unit-tested assignment logic.
- Diarization runs inside `transcribe_core.transcribe(..., diarize=True)` while the
  16 kHz mono wav still exists, so no second audio pass and no retained source.
- Opt-in per job via an "Identify who spoke" toggle that appears only when the
  library and models are present. Graceful no-op when unavailable.
- `scripts/get_diarization_models.py` fetches the models into the gitignored
  model cache; the Dockerfile bakes them (non-fatal) so it works out of the box.

## Security review (sandbox scan, before install)

- `sherpa-onnx-core` ships prebuilt wheels for all platforms and no sdist, so
  `pip install` never compiles or runs a build script on the host.
- No install-time hooks, and the Python sources make no runtime network calls
  (only docstring URLs); it loads local ONNX files.
- The compiled native wheel was not decompiled (impractical for any prebuilt
  native package); trust posture matches `ctranslate2` / `onnxruntime`, which we
  already ship.

## Consequences

- Verified CPU-native: 2 speakers detected in about 2 seconds on a 22 second clip,
  and confirmed end to end in the UI (Speaker 1 / Speaker 2 chips). Only the GPU
  speed path remains documented-not-tested.
- This replaced the earlier paused plan (pyannote in an optional add-on image with
  a Hugging Face token). No token, no torch, and it lives in the base image.
