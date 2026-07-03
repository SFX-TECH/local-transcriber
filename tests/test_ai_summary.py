"""
Unit tests for ai_summary: the optional local-AI (Ollama) insights layer.

No Ollama server is needed. Network calls (_get / _post) are monkeypatched, so
these tests exercise the prompt building, JSON extraction, normalization, model
filtering, and the graceful-degradation paths deterministically.
"""

import urllib.error

import ai_summary


# --- transcript building -----------------------------------------------------
def test_build_transcript_has_timestamps():
    segs = [
        {"start": 4, "end": 7, "text": "Hello world."},
        {"start": 65, "end": 70, "text": "Past a minute."},
    ]
    out = ai_summary.build_transcript(segs)
    assert "[0:04] Hello world." in out
    assert "[1:05] Past a minute." in out


def test_build_transcript_skips_empty_and_truncates():
    segs = [{"start": 0, "end": 1, "text": ""}, {"start": 1, "end": 2, "text": "x" * 50}]
    out = ai_summary.build_transcript(segs, max_chars=20)
    assert "truncated" in out
    # the empty segment contributed no line
    assert out.count("[0:0") <= 1


# --- JSON extraction ---------------------------------------------------------
def test_extract_json_plain():
    assert ai_summary._extract_json('{"summary": "ok"}') == {"summary": "ok"}


def test_extract_json_from_code_fence():
    raw = "```json\n{\"summary\": \"ok\", \"chapters\": []}\n```"
    assert ai_summary._extract_json(raw)["summary"] == "ok"


def test_extract_json_with_leading_prose():
    raw = "Sure, here you go: {\"summary\": \"hi\"} hope that helps"
    assert ai_summary._extract_json(raw) == {"summary": "hi"}


def test_extract_json_garbage_returns_empty():
    assert ai_summary._extract_json("not json at all") == {}


# --- normalization -----------------------------------------------------------
def test_normalize_full():
    data = {
        "summary": "  A talk.  ",
        "chapters": [
            {"time": "0:00", "title": "Intro"},
            {"timestamp": "1:20", "name": "Middle"},
            {"title": ""},  # dropped (no title)
            "loose string chapter",
        ],
        "actions": ["Do a thing", {"text": "Nested action"}, "", {"item": "Another"}],
    }
    out = ai_summary._normalize(data, raw="")
    assert out["summary"] == "A talk."
    assert {"time": "0:00", "title": "Intro"} in out["chapters"]
    assert {"time": "1:20", "title": "Middle"} in out["chapters"]
    assert {"time": "", "title": "loose string chapter"} in out["chapters"]
    assert len(out["chapters"]) == 3
    assert out["actions"] == ["Do a thing", "Nested action", "Another"]


def test_normalize_falls_back_to_raw_summary():
    out = ai_summary._normalize({}, raw="model said something")
    assert out["summary"] == "model said something"
    assert out["chapters"] == [] and out["actions"] == []


# --- model listing -----------------------------------------------------------
def test_list_chat_models_filters_embeddings():
    tags = {"models": [
        {"name": "qwen3:8b"},
        {"name": "nomic-embed-text:latest"},
        {"name": "gpt-oss:20b"},
        {"name": "mxbai-embed-large"},
    ]}
    assert ai_summary.list_chat_models(tags) == ["qwen3:8b", "gpt-oss:20b"]


# --- status (availability) ---------------------------------------------------
def test_status_unavailable_when_ollama_down(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(ai_summary, "_get", boom)
    st = ai_summary.status()
    assert st["available"] is False
    assert st["models"] == []
    assert st["error"]


def test_status_available_picks_default(monkeypatch):
    monkeypatch.setattr(ai_summary, "DEFAULT_MODEL", "gpt-oss:20b")
    monkeypatch.setattr(
        ai_summary, "_get",
        lambda *a, **k: {"models": [{"name": "qwen3:8b"}, {"name": "gpt-oss:20b"}]},
    )
    st = ai_summary.status()
    assert st["available"] is True
    assert st["default"] == "gpt-oss:20b"


def test_status_default_falls_back_to_first_when_preferred_absent(monkeypatch):
    monkeypatch.setattr(ai_summary, "DEFAULT_MODEL", "not-pulled")
    monkeypatch.setattr(ai_summary, "_get", lambda *a, **k: {"models": [{"name": "qwen3:8b"}]})
    assert ai_summary.status()["default"] == "qwen3:8b"


# --- summarize (end-to-end with a stubbed model) -----------------------------
def test_summarize_parses_model_response(monkeypatch):
    captured = {}

    def fake_post(path, payload, timeout=180.0):
        captured["path"] = path
        captured["model"] = payload["model"]
        return {"response": '{"summary": "It works.", "chapters": '
                            '[{"time": "0:00", "title": "Start"}], "actions": ["Ship it"]}'}

    monkeypatch.setattr(ai_summary, "_post", fake_post)
    res = ai_summary.summarize([{"start": 0, "end": 1, "text": "Hello."}], model="qwen3:8b")
    assert res["ok"] is True
    assert res["model"] == "qwen3:8b"
    assert res["summary"] == "It works."
    assert res["chapters"] == [{"time": "0:00", "title": "Start"}]
    assert res["actions"] == ["Ship it"]
    assert captured["path"] == "/api/generate"


def test_summarize_empty_segments_returns_not_ok():
    res = ai_summary.summarize([{"start": 0, "end": 1, "text": "   "}], model="qwen3:8b")
    assert res["ok"] is False


def test_summarize_reports_missing_model(monkeypatch):
    def not_found(path, payload, timeout=180.0):
        raise urllib.error.HTTPError(path, 404, "not found", {}, None)

    monkeypatch.setattr(ai_summary, "_post", not_found)
    res = ai_summary.summarize([{"start": 0, "end": 1, "text": "Hi."}], model="ghost:1b")
    assert res["ok"] is False
    assert "ollama pull ghost:1b" in res["hint"].lower()
