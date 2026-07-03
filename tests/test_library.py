"""
Unit tests for library: the persistent transcript store + global search.

Each test runs against a throwaway SQLite file (LIBRARY_DB points at tmp_path),
so nothing touches a real library and tests are fully isolated.
"""

import importlib

import pytest

import library


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRARY_DB", str(tmp_path / "library.db"))
    # db_path() reads the env on every call, so no reload is needed; return module.
    return library


def _segs(*texts):
    return [{"start": i, "end": i + 1, "text": t} for i, t in enumerate(texts)]


def test_add_and_get(lib):
    lib.add("a1", "Interview.mp3", _segs("Hello there", "General Kenobi"),
            duration=12.5, language="en", model="small", source="app", created_at=100.0)
    item = lib.get("a1")
    assert item is not None
    assert item["name"] == "Interview.mp3"
    assert item["duration"] == 12.5
    assert item["language"] == "en"
    assert len(item["segments"]) == 2
    assert item["segments"][1]["text"] == "General Kenobi"


def test_get_missing_returns_none(lib):
    assert lib.get("nope") is None


def test_list_newest_first(lib):
    lib.add("old", "Old", _segs("a"), created_at=100.0)
    lib.add("new", "New", _segs("b"), created_at=200.0)
    ids = [e["id"] for e in lib.list_entries()]
    assert ids == ["new", "old"]


def test_count(lib):
    assert lib.count() == 0
    lib.add("a", "A", _segs("x"), created_at=1.0)
    lib.add("b", "B", _segs("y"), created_at=2.0)
    assert lib.count() == 2


def test_upsert_replaces_same_id(lib):
    lib.add("dup", "First", _segs("one"), created_at=1.0)
    lib.add("dup", "Second", _segs("two"), created_at=2.0)
    assert lib.count() == 1
    assert lib.get("dup")["name"] == "Second"


def test_search_matches_body_with_snippet(lib):
    lib.add("m", "Meeting", _segs("We will ship the redesign this week"), created_at=1.0)
    lib.add("o", "Other", _segs("Nothing relevant here"), created_at=2.0)
    hits = lib.list_entries(query="redesign")
    assert [e["id"] for e in hits] == ["m"]
    assert "redesign" in hits[0]["snippet"].lower()


def test_search_matches_name(lib):
    lib.add("m", "Standup notes", _segs("hello"), created_at=1.0)
    hits = lib.list_entries(query="standup")
    assert len(hits) == 1 and hits[0]["id"] == "m"


def test_search_no_match_is_empty(lib):
    lib.add("m", "Meeting", _segs("hello world"), created_at=1.0)
    assert lib.list_entries(query="zzzzz") == []


def test_delete(lib):
    lib.add("m", "Meeting", _segs("hello"), created_at=1.0)
    assert lib.delete("m") is True
    assert lib.get("m") is None
    assert lib.delete("m") is False


def test_text_is_built_from_segments(lib):
    lib.add("m", "Meeting", _segs("line one", "", "line three"), created_at=1.0)
    entry = lib.list_entries()[0]
    # empty segment text is skipped in the snippet body
    assert "line one" in entry["snippet"]
    assert "line three" in entry["snippet"]
    # segment count still reflects all stored segments
    assert entry["segment_count"] == 3
