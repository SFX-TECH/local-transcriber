# Testing

Two layers: a fast automated smoke test, and a manual full-length check with a
real recording. Everything runs locally; no network is needed once the `tiny`
model is cached.

## 1. Automated smoke test

Proves the formatters and the real ffmpeg to faster-whisper pipeline work,
end to end, in a few seconds. It synthesizes a short speech clip with ffmpeg
(no microphone needed), transcribes it with the `tiny` model, and asserts the
`.txt` / `.srt` / `.vtt` output is non-empty and well-formed.

Run it:

```bat
.venv\Scripts\python.exe test_smoke.py
```

Or, if you have pytest installed:

```bat
.venv\Scripts\python.exe -m pytest test_smoke.py -v
```

Expected output ends with:

```
SMOKE TEST PASSED (2/2 checks)
```

Exit code is `0` on success, `1` on any failure (handy for CI).

Notes:
- Override the model with `SMOKE_MODEL` (for example `set SMOKE_MODEL=small`).
- It uses the GPU when available, otherwise the CPU. Both are fine.
- If the ffmpeg build lacks the `flite` voice filter, the test falls back to a
  tone and still verifies the pipeline runs and the formatters work.

## 2. Manual full-length check (a real 30-minute file)

This is the real-world confidence check: a full recording through the web UI.

1. Start the app: double-click **`run.bat`** (or
   `.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8765`).
2. Open <http://localhost:8765>. Confirm the header badge reads **GPU (CUDA,
   float16)** (or **CPU (int8)** if no GPU). No file selected: the dropzone
   shows only the prompt, with no stray file chip.
3. Drag in a ~30-minute video or audio file (mp4, mov, mkv, mp3, m4a, wav, ...).
   The file chip shows its name and size; **Transcribe** enables.
4. Pick a model. **Small** is the sensible default; **Large v3** is best on the
   GPU for an important transcript.
5. Click **Transcribe** and watch:
   - Upload percentage, then "Extracting audio", then "Transcribing".
   - The progress bar advances and a "~m:ss left" estimate appears.
   - The transcript streams in live, each line stamped with its start time.
   - On the GPU a 30-minute file typically finishes in roughly 1 to 3 minutes.
6. When done, the status reads "Done in m:ss (N segments, mm:ss of audio)".
   Verify:
   - **Copy** copies the full transcript to the clipboard.
   - **.txt**, **.srt**, **.vtt** each download and open correctly.
   - The same three files exist under `output\<job-id>\` (path shown on the page).
   - `uploads\` does not keep the source file afterward (it is deleted per job).

### Error cases worth a quick poke

- Drop a non-media file (a `.txt` or `.pdf`): you should get a clear message,
  not a crash.
- Drop a renamed/corrupt file with no audio: it should fail with
  "Could not read audio from this file...", and the app stays usable.

If all of the above hold, the working path is healthy.
