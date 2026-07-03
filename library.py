"""
Transcript library: a small local store of every finished transcript, with
global search across all of them. Pure stdlib (sqlite3), fully local.

Both backends record here when a job finishes:
  - the single-process app writes to a library.db next to its output/, and
  - the queue worker writes to library.db on the shared volume (SHARED_DIR),
    which the API reads. SQLite's file locking handles the two processes.

Location resolution (first that applies):
  LIBRARY_DB   an explicit path to the database file
  SHARED_DIR   -> $SHARED_DIR/library.db   (the queue deployment)
  otherwise    -> <repo_root>/library/library.db
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent


def db_path() -> Path:
    explicit = os.environ.get("LIBRARY_DB")
    if explicit:
        return Path(explicit)
    shared = os.environ.get("SHARED_DIR")
    if shared:
        return Path(shared) / "library.db"
    return _REPO / "library" / "library.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transcripts (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            created_at  REAL NOT NULL,
            duration    REAL DEFAULT 0,
            language    TEXT DEFAULT '',
            model       TEXT DEFAULT '',
            source      TEXT DEFAULT '',
            text        TEXT NOT NULL,
            segments    TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_transcripts_created ON transcripts(created_at DESC)")
    return conn


def add(
    id: str,
    name: str,
    segments: list,
    duration: float = 0.0,
    language: str = "",
    model: str = "",
    source: str = "",
    created_at: float | None = None,
) -> None:
    """Insert or replace a transcript. Safe to call more than once per job."""
    segs = [
        {"start": float(s.get("start", 0) or 0), "end": float(s.get("end", 0) or 0), "text": (s.get("text") or "").strip()}
        for s in (segments or [])
    ]
    text = "\n".join(s["text"] for s in segs if s["text"])
    if created_at is None:
        created_at = time.time()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO transcripts "
            "(id, name, created_at, duration, language, model, source, text, segments) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, name or "transcript", created_at, float(duration or 0), language or "",
             model or "", source or "", text, json.dumps(segs)),
        )
        conn.commit()
    finally:
        conn.close()


def _snippet(text: str, query: str, radius: int = 70) -> str:
    """A short excerpt of text. If query is given, center it on the first match."""
    text = text.replace("\n", " ").strip()
    if not query:
        return text[: radius * 2] + ("..." if len(text) > radius * 2 else "")
    low = text.lower()
    idx = low.find(query.lower())
    if idx == -1:
        return text[: radius * 2] + ("..." if len(text) > radius * 2 else "")
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end] + suffix


def list_entries(query: str = "", limit: int = 300, offset: int = 0) -> list[dict]:
    """Newest-first list of transcripts. With a query, only those whose name or
    body contains it, each with a snippet centered on the match."""
    query = (query or "").strip()
    conn = _connect()
    try:
        if query:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM transcripts WHERE name LIKE ? OR text LIKE ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM transcripts ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        try:
            seg_count = len(json.loads(row["segments"]))
        except (ValueError, TypeError):
            seg_count = 0
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "duration": row["duration"],
                "language": row["language"],
                "model": row["model"],
                "segment_count": seg_count,
                "snippet": _snippet(row["text"] or "", query),
            }
        )
    return out


def get(id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM transcripts WHERE id = ?", (id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        segments = json.loads(row["segments"])
    except (ValueError, TypeError):
        segments = []
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "duration": row["duration"],
        "language": row["language"],
        "model": row["model"],
        "source": row["source"],
        "segments": segments,
    }


def delete(id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM transcripts WHERE id = ?", (id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def count() -> int:
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
    finally:
        conn.close()
