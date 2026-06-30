# Local Transcriber

Transcribe your own video/audio files **on this machine** with OpenAI's Whisper
(via `faster-whisper`). No cloud upload, no file-size cap, no paywall. Built to
replace tools that choke on large files or hide behind a paywall.

- Drag-and-drop a video or audio file of **any length** (a 30-minute talk is fine).
- Uses your **GPU** when one is available (a 30-min file can finish in a couple of
  minutes on a recent NVIDIA card); if no usable GPU is found it automatically
  falls back to the **CPU**.
- Live transcript streams in as it runs, with a progress bar and time estimate.
- A badge up top tells you whether it is running on **GPU** or **CPU**.
- Output as plain text (`.txt`), subtitles (`.srt`), or web captions (`.vtt`),
  saved to the `output\` folder and downloadable or copyable from the page.

## Use it

1. Double-click **`run.bat`**. A browser tab opens at <http://localhost:8765>.
   (Keep the little black window open while you work; close it to stop the app.)
2. Drop your file, pick a **Model** and **Language**, click **Transcribe**.
3. Watch the live transcript, then download `.txt` / `.srt` / `.vtt` or copy it.

The first time you use a given model it downloads once (Small ~0.5 GB,
Large v3 ~3 GB) and is cached for next time.

### Which model?

| Model | Speed | Accuracy | Notes |
|-------|-------|----------|-------|
| Tiny / Base | fastest | rough | quick drafts |
| **Small** | fast | good | sensible default |
| Medium | slower | better | |
| **Large v3** | slowest | best | best on a GPU; use for important transcripts |

## First-time setup

Run **`setup.bat`** once. It needs Python 3.11 (from python.org) and `ffmpeg` on
your PATH (or point `FFMPEG_PATH` at it). It creates `.venv` and installs the
pinned dependencies from `requirements.txt` (a ~1.5 GB download the first time).

## If it ever breaks

Delete the `.venv` folder and run **`setup.bat`** again to rebuild the
environment from the exact pinned versions in `requirements.txt`.

If a file is rejected or fails: non-media files (documents, images, archives)
are turned away with a message, and a corrupt file or one with no audio track
reports a clean error instead of crashing.

## Run it in Docker (optional)

A CPU container is provided so the app runs anywhere with Docker, no Python
setup needed. See **`TESTING.md`** and the comments in `Dockerfile` /
`docker-compose.yml`. Models are kept in a persistent cache volume, so they are
not re-downloaded on every run, and transcripts land in your local `output\`
folder. An optional `Dockerfile.gpu` builds a CUDA image for NVIDIA GPUs (needs
the NVIDIA Container Toolkit and `--gpus all`).

```
docker compose up --build      # build and start the CPU app on http://localhost:8765
```

## What's where

- `app.py` -- the local web server (FastAPI): upload, job queue, streaming, downloads.
- `transcribe_core.py` -- ffmpeg audio extraction + faster-whisper, GPU/CPU auto.
- `static\` -- the web page (HTML/CSS/JS).
- `requirements.txt` -- pinned Python dependencies.
- `output\` -- your saved transcripts, one folder per job.
- `uploads\` -- temporary; source files are deleted after each job.
- `Dockerfile`, `Dockerfile.gpu`, `docker-compose.yml` -- optional containers.

Everything runs locally. Nothing leaves your computer.
