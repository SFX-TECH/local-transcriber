# Local Transcriber

> Transcribe any-length video or audio on your own machine with OpenAI's Whisper. No cloud, no file-size cap, no paywall.

![Platform](https://img.shields.io/badge/platform-local%20%C2%B7%20Windows-0a66c2)
![AI](https://img.shields.io/badge/AI-Whisper%20%C2%B7%20offline-7a5cff)
![Privacy](https://img.shields.io/badge/privacy-100%25%20local-2ea44f)
![License](https://img.shields.io/badge/license-proprietary-8a8a8a)

**Source is private by design** — public showcase only.

<!-- drop a screenshot of the live-transcript UI here: assets/hero.png -->

---

## The problem
Cloud transcription tools cap file size, hide good models behind a paywall, and make you upload your private recordings to someone else's server. For long talks, client calls, or anything sensitive, that's the wrong trade.

## What it does
- **Drag and drop** a video or audio file of **any length** (a 30-minute talk is fine).
- Runs **OpenAI Whisper locally** (via `faster-whisper`) on your GPU — a 30-minute file finishes in ~1-3 minutes — and falls back to CPU automatically if needed.
- Export as **plain text (.txt), subtitles (.srt), or web captions (.vtt)**.
- Source files are **deleted after each job**. Nothing ever leaves your computer.

## How it's built
```mermaid
flowchart LR
    UP["Drop a video/audio file<br/>(any length)"] --> FF["ffmpeg: extract audio"]
    FF --> W["faster-whisper (Whisper)<br/>GPU, auto CPU fallback"]
    W --> LIVE["Live transcript in the browser"]
    LIVE --> OUT["Download .txt · .srt · .vtt"]
    UP -. deleted after job .-> X["(source removed)"]
```

## Tech
| Layer | Stack |
|---|---|
| Server | Python + FastAPI (local web server) |
| Transcription | faster-whisper (Whisper) + ffmpeg, GPU/CPU auto-select |
| UI | Local web page (HTML/CSS/JS) at localhost |

## Status
Working local tool. Multiple Whisper model tiers (Tiny → Large v3); models cached after first download; runs entirely offline once set up.

---

Built by **Jesse Jolly** · [SFX Tech Innovation](https://sfxtechinnovation.com) · [LinkedIn](https://linkedin.com/in/jessegjolly)

*Source code is private and proprietary. This repository showcases the product and its architecture only.*
