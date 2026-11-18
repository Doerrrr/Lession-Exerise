"""Optional MySQL mirror for the local JSON data.

The JSON files remain the primary local store; MySQL is an optional, synchronized
copy.  It can be enabled in Settings, tested, synced, and used to restore the
JSON files on a new machine.

The database schema is created automatically (CREATE TABLE IF NOT EXISTS) when
the connection is tested, settings are enabled, or a sync is requested.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .store import (
    load_answers,
    load_resets,
    load_session,
    load_settings,
    list_checks,
    list_sessions,
    read_json,
    save_answers,
    save_check,
    save_resets,
    save_session,
    save_settings,
    write_json,
)
from .wordbooks import load_books, wordbooks_dir

try:
    import pymysql  # type: ignore
    PYMSQL_AVAILABLE = True
    PYMSQL_VERSION = getattr(pymysql, "__version__", "?")
except Exception:  # pragma: no cover - optional dependency
    pymysql = None  # type: ignore
    PYMSQL_AVAILABLE = False
    PYMSQL_VERSION = ""

SETTINGS_FILE = "mysql.json"
DEFAULT_PORT = 3306
DEFAULT_DATABASE = "englishlisten"


class MySQLUnavailable(Exception):
    pass


def settings_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / SETTINGS_FILE


def load_mysql_settings(data_dir: str | Path) -> dict[str, Any]:
    raw = read_json(settings_path(data_dir), {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        port = int(raw.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return {
        "host": str(raw.get("host") or "127.0.0.1"),
        "port": port,
        "user": str(raw.get("user") or "root"),
        "password": str(raw.get("password") or ""),
        "database": str(raw.get("database") or DEFAULT_DATABASE),
        "enabled": bool(raw.get("enabled")),
        "owner": str(raw.get("owner") or "local"),
        "last_sync": str(raw.get("last_sync") or ""),
        "last_error": str(raw.get("last_error") or ""),
        "last_counts": raw.get("last_counts") or {},
    }


def _mask_password(password: str) -> str:
    return "****" if password else ""


def mysql_status(data_dir: str | Path) -> dict[str, Any]:
    settings = load_mysql_settings(data_dir)
    return {
        "available": PYMSQL_AVAILABLE,
        "driver": PYMSQL_VERSION,
        "enabled": settings["enabled"],
        "configured": bool(settings["password"]),
        "host": settings["host"],
        "port": settings["port"],
        "user": settings["user"],
        "database": settings["database"],
        "owner": settings["owner"],
        "masked_password": _mask_password(settings["password"]),
        "last_sync": settings["last_sync"],
        "last_error": settings["last_error"],
        "counts": settings["last_counts"],
    }


def save_mysql_settings(
    data_dir: str | Path,
    *,
    host: str | None = None,
    port: int | str | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
    enabled: bool | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    current = load_mysql_settings(data_dir)
    if host is not None and str(host).strip():
        current["host"] = str(host).strip()
    if port is not None and str(port).strip():
        try:
            current["port"] = max(1, min(65535, int(port)))
        except (TypeError, ValueError):
            raise ValueError("MySQL 端口必须是数字")
    if user is not None and str(user).strip():
        current["user"] = str(user).strip()
    if password is not None and str(password) != "":
        current["password"] = str(password)
    if database is not None and str(database).strip():
        current["database"] = str(database).strip()
    if enabled is not None:
        current["enabled"] = bool(enabled)
    if owner is not None and str(owner).strip():
        current["owner"] = str(owner).strip()[:64]
    payload = {
        "host": current["host"],
        "port": current["port"],
        "user": current["user"],
        "password": current["password"],
        "database": current["database"],
        "enabled": current["enabled"],
        "owner": current.get("owner", "local"),
        "last_sync": current.get("last_sync", ""),
        "last_error": current.get("last_error", ""),
        "last_counts": current.get("last_counts") or {},
    }
    write_json(settings_path(data_dir), payload)
    return mysql_status(data_dir)


def _record(data_dir: str | Path, *, error: str | None = None, counts: dict | None = None) -> None:
    current = load_mysql_settings(data_dir)
    if error is not None:
        current["last_error"] = error[:1000]
    if counts is not None:
        current["last_counts"] = counts
        current["last_sync"] = datetime.now().isoformat(timespec="seconds")
        current["last_error"] = ""
    payload = {
        "host": current["host"],
        "port": current["port"],
        "user": current["user"],
        "password": current["password"],
        "database": current["database"],
        "enabled": current["enabled"],
        "owner": current.get("owner", "local"),
        "last_sync": current.get("last_sync", ""),
        "last_error": current.get("last_error", ""),
        "last_counts": current.get("last_counts") or {},
    }
    write_json(settings_path(data_dir), payload)


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS dictation_sessions (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        created_at VARCHAR(40) NOT NULL DEFAULT '',
        source_name VARCHAR(255) NOT NULL DEFAULT '',
        seed BIGINT NOT NULL DEFAULT 0,
        count INT NOT NULL DEFAULT 0,
        heard_count INT NOT NULL DEFAULT 0,
        payload JSON NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_sessions_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dictation_checks (
        session_id VARCHAR(64) NOT NULL PRIMARY KEY,
        applied_at VARCHAR(40) NOT NULL DEFAULT '',
        engine VARCHAR(32) NOT NULL DEFAULT '',
        correct INT NOT NULL DEFAULT 0,
        wrong INT NOT NULL DEFAULT 0,
        blank INT NOT NULL DEFAULT 0,
        uncertain INT NOT NULL DEFAULT 0,
        accuracy DECIMAL(6,4) NULL,
        payload JSON NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dictation_answers (
        session_id VARCHAR(64) NOT NULL PRIMARY KEY,
        saved_at VARCHAR(40) NOT NULL DEFAULT '',
        payload JSON NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dictation_settings (
        id TINYINT NOT NULL PRIMARY KEY,
        payload JSON NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dictation_resets (
        word VARCHAR(191) NOT NULL PRIMARY KEY,
        reset_at VARCHAR(40) NOT NULL DEFAULT '',
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dictation_wordbooks (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        name VARCHAR(255) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL DEFAULT '',
        word_count INT NOT NULL DEFAULT 0,
        payload JSON NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dictation_users (
        username VARCHAR(64) NOT NULL PRIMARY KEY,
        password VARCHAR(255) NOT NULL,
        enabled TINYINT NOT NULL DEFAULT 1,
        created_at VARCHAR(40) NOT NULL DEFAULT ''
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS dictation_auth_sessions (
        token VARCHAR(128) NOT NULL PRIMARY KEY,
        username VARCHAR(64) NOT NULL,
        created_at VARCHAR(40) NOT NULL DEFAULT '',
        expires_at VARCHAR(40) NOT NULL DEFAULT '',
        KEY idx_auth_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


SESSION_UPSERT = """
INSERT INTO dictation_sessions
    (id, created_at, source_name, seed, count, heard_count, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s)
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
    (session_id, applied_at, engine, correct, wrong, blank, uncertain, accuracy, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
INSERT INTO dictation_answers (session_id, saved_at, payload)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE
    saved_at = VALUES(saved_at),
    payload = VALUES(payload)
"""

SETTINGS_UPSERT = """
INSERT INTO dictation_settings (id, payload)
VALUES (1, %s)
ON DUPLICATE KEY UPDATE payload = VALUES(payload)
"""

RESET_UPSERT = """
INSERT INTO dictation_resets (word, reset_at)
VALUES (%s, %s)
ON DUPLICATE KEY UPDATE reset_at = VALUES(reset_at)
"""

WORDBOOK_UPSERT = """
INSERT INTO dictation_wordbooks (id, name, created_at, word_count, payload)
VALUES (%s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    created_at = VALUES(created_at),
    word_count = VALUES(word_count),
    payload = VALUES(payload)
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MySQLStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    # ------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        return load_mysql_settings(self.data_dir)

    def status(self) -> dict[str, Any]:
        return mysql_status(self.data_dir)

    def save_settings(self, **kwargs: Any) -> dict[str, Any]:
        return save_mysql_settings(self.data_dir, **kwargs)

    def record_error(self, message: str) -> None:
        try:
            _record(self.data_dir, error=str(message))
        except OSError:
            pass

    def _connect(self):
        if not PYMSQL_AVAILABLE or pymysql is None:
            raise MySQLUnavailable("没有安装 PyMySQL；请运行 pip install pymysql cryptography")
        settings = self.settings()
        if not settings["password"]:
            raise MySQLUnavailable("还没有配置 MySQL 密码")
        try:
            return pymysql.connect(
                host=settings["host"],
                port=int(settings["port"]),
                user=settings["user"],
                password=settings["password"],
                database=settings["database"],
                charset="utf8mb4",
                autocommit=False,
                connect_timeout=5,
                read_timeout=15,
                write_timeout=15,
            )
        except Exception as exc:
            raise MySQLUnavailable(str(exc)) from exc

    def _ensure_schema(self, conn) -> None:
        with conn.cursor() as cur:
            for statement in SCHEMA:
                cur.execute(statement)
        conn.commit()

    # ------------------------------------------------------------------
    def test(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION(), DATABASE()")
                version, database = cur.fetchone()
                cur.execute("SHOW TABLES LIKE 'dictation_%'")
                tables = [row[0] for row in cur.fetchall()]
            _record(self.data_dir, error="")
            return {
                "ok": True,
                "version": version,
                "database": database,
                "tables": sorted(tables),
                "status": self.status(),
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def mirror_session(self, session: dict[str, Any]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(SESSION_UPSERT, (
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
            with conn.cursor() as cur:
                cur.execute(CHECK_UPSERT, (
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
            with conn.cursor() as cur:
                cur.execute(ANSWERS_UPSERT, (session_id, payload["saved_at"], _json(payload)))
            conn.commit()
        finally:
            conn.close()

    def mirror_settings(self, settings: dict[str, Any]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(SETTINGS_UPSERT, (_json(settings),))
            conn.commit()
        finally:
            conn.close()

    def mirror_resets(self, resets: dict[str, str]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dictation_resets")
                if resets:
                    cur.executemany(
                        RESET_UPSERT,
                        [(str(word), str(reset_at)) for word, reset_at in resets.items()],
                    )
            conn.commit()
        finally:
            conn.close()

    def mirror_wordbook(self, book: dict[str, Any]) -> None:
        if not self.settings()["enabled"]:
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(WORDBOOK_UPSERT, (
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
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dictation_wordbooks WHERE id = %s", (book_id,))
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def _collect(self) -> dict[str, Any]:
        sessions = []
        for item in list_sessions(self.data_dir):
            session = load_session(self.data_dir, item["id"])
            if session:
                sessions.append(session)
        checks = [c for c in list_checks(self.data_dir) if c.get("session_id")]
        answers = []
        for session in sessions:
            loaded = load_answers(self.data_dir, session["id"])
            if loaded is not None:
                answers.append((session["id"], loaded))
        return {
            "sessions": sessions,
            "checks": checks,
            "answers": answers,
            "settings": load_settings(self.data_dir),
            "resets": load_resets(self.data_dir),
            "wordbooks": load_books(self.data_dir),
        }

    def sync_all(self) -> dict[str, Any]:
        data = self._collect()
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dictation_answers")
                cur.execute("DELETE FROM dictation_checks")
                cur.execute("DELETE FROM dictation_wordbooks")
                cur.execute("DELETE FROM dictation_resets")
                cur.execute("DELETE FROM dictation_settings")
                cur.execute("DELETE FROM dictation_sessions")
                for session in data["sessions"]:
                    cur.execute(SESSION_UPSERT, (
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
                        session_id, payload["saved_at"], _json(payload),
                    ))
                cur.execute(SETTINGS_UPSERT, (_json(data["settings"]),))
                for word, reset_at in data["resets"].items():
                    cur.execute(RESET_UPSERT, (str(word), str(reset_at)))
                for book in data["wordbooks"]:
                    cur.execute(WORDBOOK_UPSERT, (
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

    # ------------------------------------------------------------------
    def restore_all(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM dictation_sessions")
                sessions = [_loads(row[0]) for row in cur.fetchall()]
                cur.execute("SELECT payload FROM dictation_checks")
                checks = [_loads(row[0]) for row in cur.fetchall()]
                cur.execute("SELECT session_id, payload FROM dictation_answers")
                answers = [(row[0], _loads(row[1])) for row in cur.fetchall()]
                cur.execute("SELECT payload FROM dictation_settings WHERE id = 1")
                row = cur.fetchone()
                settings = _loads(row[0]) if row else None
                cur.execute("SELECT word, reset_at FROM dictation_resets")
                resets = {str(word): str(reset_at) for word, reset_at in cur.fetchall()}
                cur.execute("SELECT payload FROM dictation_wordbooks")
                books = [_loads(row[0]) for row in cur.fetchall()]
        finally:
            conn.close()

        for session in sessions:
            save_session(self.data_dir, session)
        for check in checks:
            save_check(self.data_dir, check)
        for session_id, payload in answers:
            session = next((s for s in sessions if s.get("id") == session_id), None)
            if session is not None:
                save_answers(self.data_dir, session, payload.get("answers") or {})
        if settings:
            save_settings(self.data_dir, settings)
        save_resets(self.data_dir, resets)
        directory = wordbooks_dir(self.data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for book in books:
            book_id = str(book.get("id") or "")
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


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    return {}


# ---------------------------------------------------------------------------
# accounts / auth (server mode)
# ---------------------------------------------------------------------------

def _auth_connect(data_dir: str | Path):
    store = MySQLStore(data_dir)
    conn = store._connect()
    store._ensure_schema(conn)
    return conn


def authenticate(data_dir: str | Path, username: str, password: str) -> dict[str, Any] | None:
    """Plain-text password check (per project requirement)."""
    username = str(username or "").strip()
    if not username:
        return None
    conn = _auth_connect(data_dir)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, password, enabled, created_at FROM dictation_users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    db_user, db_password, enabled, created_at = row
    if not int(enabled or 0):
        return None
    if str(db_password or "") != str(password or ""):
        return None
    return {
        "username": str(db_user),
        "enabled": True,
        "created_at": str(created_at or ""),
    }


def create_auth_token(data_dir: str | Path, username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires = now + timedelta(days=30)
    conn = _auth_connect(data_dir)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM dictation_auth_sessions WHERE expires_at <> '' AND expires_at < %s",
                (now.isoformat(timespec="seconds"),),
            )
            cur.execute(
                "INSERT INTO dictation_auth_sessions (token, username, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s)",
                (token, str(username), now.isoformat(timespec="seconds"),
                 expires.isoformat(timespec="seconds")),
            )
        conn.commit()
    finally:
        conn.close()
    return token


def resolve_auth_token(data_dir: str | Path, token: str) -> str | None:
    token = str(token or "").strip()
    if not token:
        return None
    conn = _auth_connect(data_dir)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.username, s.expires_at, u.enabled "
                "FROM dictation_auth_sessions s LEFT JOIN dictation_users u ON u.username = s.username "
                "WHERE s.token = %s",
                (token,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    username, expires_at, enabled = row
    if enabled is None or not int(enabled or 0):
        return None
    if expires_at:
        try:
            if datetime.fromisoformat(str(expires_at)) < datetime.now():
                return None
        except ValueError:
            return None
    return str(username)


def delete_auth_token(data_dir: str | Path, token: str) -> None:
    token = str(token or "").strip()
    if not token:
        return
    conn = _auth_connect(data_dir)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dictation_auth_sessions WHERE token = %s", (token,))
        conn.commit()
    finally:
        conn.close()
