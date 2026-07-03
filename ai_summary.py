"""
Local AI insights via Ollama: a summary, chapters, and action items for a
transcript. Fully local and entirely optional. If Ollama is not running (or no
model is pulled), every call degrades gracefully and the app keeps working.

Nothing here touches the network at import time, so the backends can import it
unconditionally and tests run without an Ollama server.

Configuration (all optional):
  OLLAMA_BASE_URL   full base url, e.g. http://127.0.0.1:11434 (wins if set)
  OLLAMA_HOST       host[:port], e.g. host.docker.internal:11434
  OLLAMA_MODEL      preferred model name, e.g. qwen3:8b
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")

# Models that only produce embeddings (no chat/generate). Skipped when we pick a
# default and hidden from the UI's model picker.
_EMBED_HINTS = ("embed", "bge", "minilm", "nomic", "mxbai", "gte-")


def base_url() -> str:
    """Where Ollama lives. Env-overridable so the Docker backend can point at the
    host (host.docker.internal) while the local app uses localhost."""
    url = os.environ.get("OLLAMA_BASE_URL")
    if url:
        return url.rstrip("/")
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    if not host.startswith("http://") and not host.startswith("https://"):
        host = "http://" + host
    return host.rstrip("/")


def _get(path: str, timeout: float = 3.0) -> dict:
    req = urllib.request.Request(base_url() + path)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, payload: dict, timeout: float = 180.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url() + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_chat_model(name: str) -> bool:
    low = name.lower()
    return not any(h in low for h in _EMBED_HINTS)


def list_chat_models(tags: dict) -> list[str]:
    return [m["name"] for m in tags.get("models", []) if m.get("name") and _is_chat_model(m["name"])]


def status() -> dict:
    """Probe Ollama. Never raises: returns availability, the usable models, and
    the model we would use by default."""
    try:
        tags = _get("/api/tags", timeout=3.0)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {
            "available": False,
            "error": _friendly_error(exc),
            "models": [],
            "default": DEFAULT_MODEL,
            "base_url": base_url(),
        }
    models = list_chat_models(tags)
    default = DEFAULT_MODEL if DEFAULT_MODEL in models else (models[0] if models else "")
    return {
        "available": True,
        "models": models,
        "default": default,
        "base_url": base_url(),
    }


def _friendly_error(exc: Exception) -> str:
    text = str(getattr(exc, "reason", exc)) or exc.__class__.__name__
    if "refused" in text.lower() or "actively refused" in text.lower():
        return "Ollama is not running at " + base_url()
    return text


def _mmss(seconds: float) -> str:
    s = max(0, int(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def build_transcript(segments: list, max_chars: int = 12000) -> str:
    """Render segments as timestamped lines, bounded so the prompt fits context.
    Timestamps are included so the model can cite real times for chapters."""
    lines = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if text:
            lines.append(f"[{_mmss(s.get('start', 0))}] {text}")
    joined = "\n".join(lines)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n[...transcript truncated...]"
    return joined


_PROMPT = (
    "You analyze a transcript and return insights. Respond with ONLY a single "
    "JSON object (no prose, no markdown code fences) with exactly these keys:\n"
    '  "summary": a 2 to 4 sentence plain-language summary of the whole transcript.\n'
    '  "chapters": an array of 3 to 8 objects, each {"time": "M:SS", "title": '
    '"short chapter title"}, using timestamps that appear in the transcript, in order.\n'
    '  "actions": an array of 0 to 8 short action items or decisions as strings; '
    "use [] if there are none.\n"
    "Stay faithful to the transcript and do not invent facts.\n\n"
    "TRANSCRIPT:\n"
)


def _extract_json(raw: str) -> dict:
    """Parse the model's reply as JSON, tolerating stray text or a code fence
    around the object (some models add one despite instructions)."""
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except ValueError:
            pass
    return {}


def _normalize(data: dict, raw: str) -> dict:
    summary = str(data.get("summary") or "").strip()
    if not summary:
        # Fall back to the raw text so the user still gets something useful.
        summary = raw.strip()[:800]

    chapters = []
    for ch in data.get("chapters") or []:
        if isinstance(ch, dict):
            time = str(ch.get("time") or ch.get("timestamp") or "").strip()
            title = str(ch.get("title") or ch.get("name") or "").strip()
        else:
            time, title = "", str(ch).strip()
        if title:
            chapters.append({"time": time, "title": title})

    actions = []
    for a in data.get("actions") or []:
        if isinstance(a, dict):
            a = a.get("text") or a.get("action") or a.get("item") or ""
        a = str(a).strip()
        if a:
            actions.append(a)

    return {"summary": summary, "chapters": chapters, "actions": actions}


def summarize(segments: list, model: str, timeout: float = 300.0) -> dict:
    """Generate insights for a transcript. Returns {ok: True, ...} on success or
    {ok: False, error, hint} on any failure. Blocking; call in a thread."""
    transcript = build_transcript(segments)
    if not transcript.strip():
        return {"ok": False, "error": "The transcript is empty."}
    payload = {
        "model": model,
        "prompt": _PROMPT + transcript,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    try:
        resp = _post("/api/generate", payload, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        hint = ""
        if exc.code == 404 or "not found" in body.lower():
            hint = f"Model '{model}' is not pulled. Run: ollama pull {model}"
        return {"ok": False, "error": f"Ollama error {exc.code}: {body[:200]}", "hint": hint}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "error": _friendly_error(exc), "hint": "Start Ollama, then try again."}

    result = _normalize(_extract_json(resp.get("response", "")), resp.get("response", ""))
    result["ok"] = True
    result["model"] = model
    return result
