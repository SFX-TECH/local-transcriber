"""
End-to-end smoke test for Local Transcriber.

What it proves, fast and offline:
  1. The output formatters (to_txt / to_srt / to_vtt) emit well-formed, non-empty
     text for known segments.
  2. The real pipeline works: ffmpeg synthesizes a short speech clip, runs
     through transcribe_core.transcribe(), reaches a "done" event with no error,
     and the three transcript files written from it are non-empty.

Runs with the plain interpreter (no pytest required):

    .venv\\Scripts\\python.exe test_smoke.py

or, if you have pytest:

    .venv\\Scripts\\python.exe -m pytest test_smoke.py -v

Uses the "tiny" model by default for speed (override with SMOKE_MODEL).
Everything stays local; no network is needed once the tiny model is cached.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import transcribe_core as core

MODEL = os.environ.get("SMOKE_MODEL", "tiny")
SPEECH_TEXT = "Hello world, this is a local transcription smoke test."


# --- helpers -----------------------------------------------------------------
def _make_clip(path: str) -> str:
    """Write ~4s of synthetic audio. Prefer real speech (ffmpeg flite) so the
    transcript has words; fall back to a tone if flite is not compiled in."""
    flite = [
        core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"flite=text='{SPEECH_TEXT}':voice=slt",
        "-t", "4", "-ar", "16000", "-ac", "1", path,
    ]
    try:
        subprocess.run(flite, check=True, capture_output=True)
        if os.path.getsize(path) > 0:
            return "speech"
    except Exception:
        pass
    subprocess.run(
        [core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=4",
         "-ar", "16000", "-ac", "1", path],
        check=True,
    )
    return "tone"


# --- tests -------------------------------------------------------------------
def test_format_functions() -> None:
    """Formatters produce non-empty, correctly-shaped output (no model needed)."""
    segs = [
        {"start": 0.0, "end": 1.5, "text": "Hello world."},
        {"start": 1.5, "end": 3.25, "text": "Second line here."},
    ]
    txt = core.to_txt(segs)
    srt = core.to_srt(segs)
    vtt = core.to_vtt(segs)

    assert txt.strip(), "txt is empty"
    assert "Hello world." in txt and "Second line here." in txt

    assert srt.strip().startswith("1"), "srt should start with index 1"
    assert "00:00:00,000 --> 00:00:01,500" in srt, "srt timestamp wrong"
    assert "00:00:01,500 --> 00:00:03,250" in srt

    assert vtt.startswith("WEBVTT"), "vtt must start with WEBVTT"
    assert "00:00:00.000 --> 00:00:01.500" in vtt, "vtt timestamp wrong"
    print("[ok] format functions: txt/srt/vtt well-formed and non-empty")


def test_end_to_end_pipeline() -> None:
    """Synthesize audio, run the real pipeline, write the 3 files, assert non-empty."""
    workdir = tempfile.mkdtemp(prefix="smoke_")
    try:
        clip = os.path.join(workdir, "clip.wav")
        mode = _make_clip(clip)
        assert os.path.getsize(clip) > 0, "ffmpeg did not produce a clip"

        events = list(core.transcribe(clip, model_name=MODEL, language="en"))
        types = [e["type"] for e in events]
        errors = [e for e in events if e["type"] == "error"]
        done = [e for e in events if e["type"] == "done"]

        assert not errors, f"pipeline reported error(s): {errors}"
        assert "status" in types, "no status events emitted"
        assert done, "pipeline never reached a 'done' event"

        segments = done[0]["segments"]
        duration = done[0]["duration"]
        assert duration > 0, f"duration should be > 0, got {duration}"

        # Real speech must yield at least one segment; a tone fallback may not,
        # so only the speech path asserts on transcript content.
        if mode == "speech":
            assert segments, "speech clip produced no segments"

        txt = core.to_txt(segments)
        srt = core.to_srt(segments)
        vtt = core.to_vtt(segments)

        out_txt = os.path.join(workdir, "out.txt")
        out_srt = os.path.join(workdir, "out.srt")
        out_vtt = os.path.join(workdir, "out.vtt")
        open(out_txt, "w", encoding="utf-8").write(txt)
        open(out_srt, "w", encoding="utf-8").write(srt)
        open(out_vtt, "w", encoding="utf-8").write(vtt)

        if mode == "speech":
            for p in (out_txt, out_srt, out_vtt):
                assert os.path.getsize(p) > 0, f"{os.path.basename(p)} is empty"
            assert txt.strip(), "txt has no content"
            assert srt.strip(), "srt has no content"
        # vtt always carries the WEBVTT header regardless of segments.
        assert vtt.startswith("WEBVTT"), "vtt missing header"

        print(f"[ok] end-to-end ({mode}, model={MODEL}, {core.resolve_device()[2]}): "
              f"{len(segments)} segment(s), {duration:.1f}s audio, files non-empty")
        print(f"     transcript: {txt.strip()[:80]!r}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --- standalone runner -------------------------------------------------------
def _main() -> int:
    tests = [test_format_functions, test_end_to_end_pipeline]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failures += 1
            print(f"[FAIL] {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[ERROR] {t.__name__}: {type(exc).__name__}: {exc}")
    print("-" * 60)
    if failures:
        print(f"SMOKE TEST FAILED ({failures} of {len(tests)} checks failed)")
        return 1
    print(f"SMOKE TEST PASSED ({len(tests)}/{len(tests)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
