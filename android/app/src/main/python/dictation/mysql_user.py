"""Per-account MySQL store: same tables with an ``owner`` column.

The local/offline store keeps using ``dictation.mysql.MySQLStore``.  Server mode
uses this class for each logged-in user, so every query is scoped by owner.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .mysql import (
    ANSWERS_UPSERT as BASE_ANSWERS_UPSERT,
    CHECK_UPSERT as BASE_CHECK_UPSERT,
    RESET_UPSERT as BASE_RESET_UPSERT,
    SESSION_UPSERT as BASE_SESSION_UPSERT,
    SETTINGS_UPSERT as BASE_SETTINGS_UPSERT,
    WORDBOOK_UPSERT as BASE_WORDBOOK_UPSERT,
    MySQLStore,
    _json,
    _record,
)

OWNER_TABLES = [
    ("dictation_sessions", "id"),
    ("dictation_checks", "session_id"),
    ("dictation_answers", "session_id"),
    ("dictation_settings", "id"),
    ("dictation_resets", "word"),
    ("dictation_wordbooks", "id"),
]

SESSION_UPSERT = """
INSERT INTO dictation_sessions
    (owner, id, created_at, source_name, seed, count, heard_count, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    created_at = VALUES(created_at),
    source_name = VALUES(source_name),
    seed = VALUES(seed),
    count = VALUES(count),
    heard_count = VALUES(heard_count),
    payload = VALUES(payload)
"""

CHECK_UPSERT = """
INSERT INTO dictation_checks
    (owner, session_id, applied_at, engine, correct, wrong, blank, uncertain, accuracy, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    applied_at = VALUES(applied_at),
    engine = VALUES(engine),
    correct = VALUES(correct),
    wrong = VALUES(wrong),
    blank = VALUES(blank),
    uncertain = VALUES(uncertain),
    accuracy = VALUES(accuracy),
    payload = VALUES(payload)
"""

ANSWERS_UPSERT = """
INSERT INTO dictation_answers (owner, session_id, saved_at, payload)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    saved_at = VALUES(saved_at),
    payload = VALUES(payload)
"""

SETTINGS_UPSERT = """
INSERT INTO dictation_settings (owner, id, payload)
VALUES (%s, 1, %s)
ON DUPLICATE KEY UPDATE payload = VALUES(payload)
"""

RESET_UPSERT = """
INSERT INTO dictation_resets (owner, word, reset_at)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE reset_at = VALUES(reset_at)
"""

WORDBOOK_UPSERT = """
INSERT INTO dictation_wordbooks (owner, id, name, created_at, word_count, payload)
VALUES (%s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    created_at = VALUES(created_at),
    word_count = VALUES(word_count),
    payload = VALUES(payload)
"""


class UserMySQLStore(MySQLStore):
    def __init__(self, data_dir: str | Path, owner: str):
        super().__init__(data_dir)
        self.owner = str(owner or "").strip() or "local"

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        data = super().status()
        data["owner"] = self.owner
        return data

    def _ensure_schema(self, conn) -> None:
        if getattr(self, "_owner_ready", False):
            return
        super()._ensure_schema(conn)
        with conn.cursor() as cur:
            for table, key in OWNER_TABLES:
                cur.execute(f"SHOW COLUMNS FROM {table} LIKE 'owner'")
                if cur.fetchone() is None:
                    cur.execute(
                        f"ALTER TABLE {table} ADD COLUMN owner VARCHAR(64) NOT NULL DEFAULT 'local' FIRST"
                    )
                    cur.execute(
                        f"ALTER TABLE {table} DROP PRIMARY KEY, ADD PRIMARY KEY (owner, {key})"
                    )
        conn.commit()
        self._owner_ready = True

    # ------------------------------------------------------------------
    def mirror_session(self, session: dict[str, Any]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(SESSION_UPSERT, (
                    self.owner,
                    str(session.get("id") or ""),
                    str(session.get("created_at") or ""),
                    str(session.get("source_name") or ""),
                    int(session.get("seed") or 0),
                    int(session.get("count") or 0),
                    int(session.get("heard_count") or 0),
                    _json(session),
                ))
            conn.commit()
        finally:
            conn.close()

    def mirror_check(self, check: dict[str, Any]) -> None:
        if not self.settings()["enabled"]:
            return
        summary = check.get("summary") or {}
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(CHECK_UPSERT, (
                    self.owner,
                    str(check.get("session_id") or ""),
                    str(check.get("applied_at") or ""),
                    str(check.get("engine") or ""),
                    int(summary.get("correct") or 0),
                    int(summary.get("wrong") or 0),
                    int(summary.get("blank") or 0),
                    int(summary.get("uncertain") or 0),
                    summary.get("accuracy"),
                    _json(check),
                ))
            conn.commit()
        finally:
            conn.close()

    def mirror_answers(self, session_id: str, answers: dict[str, str]) -> None:
        if not self.settings()["enabled"]:
            return
        payload = {"answers": {str(k): str(v) for k, v in answers.items()},
                   "saved_at": datetime.now().isoformat(timespec="seconds")}
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(ANSWERS_UPSERT, (
                    self.owner, session_id, payload["saved_at"], _json(payload),
                ))
            conn.commit()
        finally:
            conn.close()

    def mirror_settings(self, settings: dict[str, Any]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(SETTINGS_UPSERT, (self.owner, _json(settings)))
            conn.commit()
        finally:
            conn.close()

    def mirror_resets(self, resets: dict[str, str]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dictation_resets WHERE owner = %s", (self.owner,))
                if resets:
                    cur.executemany(
                        RESET_UPSERT,
                        [(self.owner, str(word), str(reset_at)) for word, reset_at in resets.items()],
                    )
            conn.commit()
        finally:
            conn.close()

    def mirror_wordbook(self, book: dict[str, Any]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(WORDBOOK_UPSERT, (
                    self.owner,
                    str(book.get("id") or ""),
                    str(book.get("name") or ""),
                    str(book.get("created_at") or ""),
                    len(book.get("words") or []),
                    _json(book),
                ))
            conn.commit()
        finally:
            conn.close()

    def delete_wordbook(self, book_id: str) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM dictation_wordbooks WHERE owner = %s AND id = %s",
                    (self.owner, book_id),
                )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def sync_all(self) -> dict[str, Any]:
        data = self._collect()
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dictation_answers WHERE owner = %s", (self.owner,))
                cur.execute("DELETE FROM dictation_checks WHERE owner = %s", (self.owner,))
                cur.execute("DELETE FROM dictation_wordbooks WHERE owner = %s", (self.owner,))
                cur.execute("DELETE FROM dictation_resets WHERE owner = %s", (self.owner,))
                cur.execute("DELETE FROM dictation_settings WHERE owner = %s", (self.owner,))
                cur.execute("DELETE FROM dictation_sessions WHERE owner = %s", (self.owner,))
                for session in data["sessions"]:
                    cur.execute(SESSION_UPSERT, (
                        self.owner,
                        str(session.get("id") or ""),
                        str(session.get("created_at") or ""),
                        str(session.get("source_name") or ""),
                        int(session.get("seed") or 0),
                        int(session.get("count") or 0),
                        int(session.get("heard_count") or 0),
                        _json(session),
                    ))
                for check in data["checks"]:
                    summary = check.get("summary") or {}
                    cur.execute(CHECK_UPSERT, (
                        self.owner,
                        str(check.get("session_id") or ""),
                        str(check.get("applied_at") or ""),
                        str(check.get("engine") or ""),
                        int(summary.get("correct") or 0),
                        int(summary.get("wrong") or 0),
                        int(summary.get("blank") or 0),
                        int(summary.get("uncertain") or 0),
                        summary.get("accuracy"),
                        _json(check),
                    ))
                for session_id, loaded in data["answers"]:
                    payload = {
                        "answers": {str(k): str(v) for k, v in (loaded.get("answers") or {}).items()},
                        "saved_at": loaded.get("saved_at") or "",
                    }
                    cur.execute(ANSWERS_UPSERT, (
                        self.owner, session_id, payload["saved_at"], _json(payload),
                    ))
                cur.execute(SETTINGS_UPSERT, (self.owner, _json(data["settings"])))
                for word, reset_at in data["resets"].items():
                    cur.execute(RESET_UPSERT, (self.owner, str(word), str(reset_at)))
                for book in data["wordbooks"]:
                    cur.execute(WORDBOOK_UPSERT, (
                        self.owner,
                        str(book.get("id") or ""),
                        str(book.get("name") or ""),
                        str(book.get("created_at") or ""),
                        len(book.get("words") or []),
                        _json(book),
                    ))
            conn.commit()
            counts = {
                "sessions": len(data["sessions"]),
                "checks": len(data["checks"]),
                "answers": len(data["answers"]),
                "resets": len(data["resets"]),
                "wordbooks": len(data["wordbooks"]),
            }
            _record(self.data_dir, counts=counts)
            return {"ok": True, "counts": counts, "status": self.status()}
        finally:
            conn.close()

    def restore_all(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM dictation_sessions WHERE owner = %s", (self.owner,))
                sessions = [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in cur.fetchall()]
                cur.execute("SELECT payload FROM dictation_checks WHERE owner = %s", (self.owner,))
                checks = [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in cur.fetchall()]
                cur.execute("SELECT session_id, payload FROM dictation_answers WHERE owner = %s", (self.owner,))
                answers = [(row[0], json.loads(row[1]) if isinstance(row[1], str) else row[1]) for row in cur.fetchall()]
                cur.execute("SELECT payload FROM dictation_settings WHERE owner = %s AND id = 1", (self.owner,))
                row = cur.fetchone()
                settings = (json.loads(row[0]) if isinstance(row[0], str) else row[0]) if row else None
                cur.execute("SELECT word, reset_at FROM dictation_resets WHERE owner = %s", (self.owner,))
                resets = {str(word): str(reset_at) for word, reset_at in cur.fetchall()}
                cur.execute("SELECT payload FROM dictation_wordbooks WHERE owner = %s", (self.owner,))
                books = [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in cur.fetchall()]
        finally:
            conn.close()

        from .store import ensure_dirs, save_answers, save_check, save_resets, save_session, save_settings, write_json
        from .wordbooks import wordbooks_dir
        ensure_dirs(self.data_dir)
        for session in sessions:
            save_session(self.data_dir, session)
        for check in checks:
            save_check(self.data_dir, check)
        for session_id, payload in answers:
            session = next((s for s in sessions if s.get("id") == session_id), None)
            if session is not None:
                save_answers(self.data_dir, session, (payload or {}).get("answers") or {})
        if settings:
            save_settings(self.data_dir, settings)
        save_resets(self.data_dir, resets)
        directory = wordbooks_dir(self.data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for book in books:
            book_id = str((book or {}).get("id") or "")
            if book_id:
                write_json(directory / f"{book_id}.json", book)
        counts = {
            "sessions": len(sessions),
            "checks": len(checks),
            "answers": len(answers),
            "resets": len(resets),
            "wordbooks": len(books),
        }
        return {"ok": True, "counts": counts, "status": self.status()}
