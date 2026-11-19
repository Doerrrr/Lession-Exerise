"""Filesystem storage helpers: atomic JSON, sessions, checks, settings."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_SETTINGS,
    is_valid_session_id,
    now_iso,
)

SESSION_DIR = "sessions"
CHECK_DIR = "checks"
ARCHIVE_DIR = "archive"
WORDBOOK_DIR = "wordbooks"


def data_path(data_dir: str | Path) -> Path:
    return Path(data_dir).resolve()


def ensure_dirs(data_dir: str | Path) -> Path:
    root = data_path(data_dir)
    for name in (SESSION_DIR, CHECK_DIR, ARCHIVE_DIR, WORDBOOK_DIR):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 文件损坏：{p} ({exc})") from exc


def write_json(path: str | Path, data: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, p)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return p


def write_text(path: str | Path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, p)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return p


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def load_settings(data_dir: str | Path) -> dict[str, Any]:
    root = data_path(data_dir)
    raw = read_json(root / "settings.json", {}) or {}
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            settings[key] = value
    try:
        settings["threshold"] = max(1, min(20, int(settings.get("threshold", 5))))
    except (TypeError, ValueError):
        settings["threshold"] = 5
    return settings


def save_settings(data_dir: str | Path, settings: dict[str, Any]) -> dict[str, Any]:
    root = data_path(data_dir)
    merged = load_settings(root)
    merged.update(settings)
    try:
        merged["threshold"] = max(1, min(20, int(merged.get("threshold", 5))))
    except (TypeError, ValueError):
        merged["threshold"] = 5
    write_json(root / "settings.json", merged)
    return merged


def load_familiar_words(data_dir: str | Path) -> set[str]:
    words = load_settings(data_dir).get("familiar_words")
    if not isinstance(words, list):
        return set()
    return {word.strip().lower() for word in words if isinstance(word, str) and word.strip()}


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def session_path(data_dir: str | Path, session_id: str) -> Path:
    return data_path(data_dir) / SESSION_DIR / f"{session_id}.json"


def answers_json_path(data_dir: str | Path, session_id: str) -> Path:
    return data_path(data_dir) / SESSION_DIR / f"{session_id}-answers.json"


def answers_md_path(data_dir: str | Path, session_id: str) -> Path:
    return data_path(data_dir) / SESSION_DIR / f"{session_id}-answers.md"


def list_sessions(data_dir: str | Path) -> list[dict[str, Any]]:
    root = data_path(data_dir) / SESSION_DIR
    if not root.exists():
        return []
    sessions = []
    for path in root.glob("*.json"):
        if not is_valid_session_id(path.stem):
            continue
        try:
            session = read_json(path)
        except ValueError:
            continue
        if isinstance(session, dict) and session.get("id"):
            sessions.append(session)
    sessions.sort(key=lambda s: (s.get("created_at", ""), s.get("id", "")), reverse=True)
    return sessions


def load_session(data_dir: str | Path, session_id: str) -> dict[str, Any] | None:
    if not is_valid_session_id(session_id):
        return None
    return read_json(session_path(data_dir, session_id), None)


def save_session(data_dir: str | Path, session: dict[str, Any]) -> Path:
    ensure_dirs(data_dir)
    return write_json(session_path(data_dir, session["id"]), session)


def find_sessions_by_seed(data_dir: str | Path, seed: int) -> list[dict[str, Any]]:
    return [s for s in list_sessions(data_dir) if int(s.get("seed") or 0) == int(seed)]


def load_answers(data_dir: str | Path, session_id: str) -> dict[str, Any] | None:
    """Return {"answers": {n: text}, "saved_at": ...} if an answer file exists."""
    root = data_path(data_dir)
    json_path = answers_json_path(root, session_id)
    if json_path.exists():
        try:
            data = read_json(json_path, {}) or {}
            answers = {str(k): str(v) for k, v in (data.get("answers") or {}).items()}
            return {"answers": answers, "saved_at": data.get("saved_at"), "source": "json"}
        except ValueError:
            pass
    md_path = answers_md_path(root, session_id)
    if md_path.exists():
        from .sheet import parse_sheet
        parsed = parse_sheet(md_path.read_text(encoding="utf-8"))
        return {
            "answers": {str(k): v for k, v in parsed.answers.items()},
            "saved_at": None,
            "source": "md",
        }
    return None


def save_answers(
    data_dir: str | Path,
    session: dict[str, Any],
    answers: dict[str, str],
) -> dict[str, str]:
    ensure_dirs(data_dir)
    session_id = session["id"]
    payload = {
        "session_id": session_id,
        "saved_at": now_iso(),
        "answers": {str(k): str(v) for k, v in sorted(answers.items(), key=lambda kv: int(kv[0]))},
    }
    write_json(answers_json_path(data_dir, session_id), payload)
    from .sheet import build_sheet
    md = build_sheet(session, answers=payload["answers"])
    write_text(answers_md_path(data_dir, session_id), md)
    return {
        "json": str(answers_json_path(data_dir, session_id)),
        "markdown": str(answers_md_path(data_dir, session_id)),
    }


def session_status(session: dict[str, Any], *, has_answers: bool, has_check: bool) -> str:
    if has_check:
        return "checked"
    if has_answers:
        return "answered"
    if int(session.get("heard_count") or 0) > 0:
        return "heard"
    return "new"


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def check_path(data_dir: str | Path, session_id: str) -> Path:
    return data_path(data_dir) / CHECK_DIR / f"{session_id}.json"


def load_check(data_dir: str | Path, session_id: str) -> dict[str, Any] | None:
    if not is_valid_session_id(session_id):
        return None
    return read_json(check_path(data_dir, session_id), None)


def list_checks(data_dir: str | Path) -> list[dict[str, Any]]:
    root = data_path(data_dir) / CHECK_DIR
    if not root.exists():
        return []
    checks = []
    for path in root.glob("*.json"):
        if not is_valid_session_id(path.stem):
            continue
        try:
            check = read_json(path)
        except ValueError:
            continue
        if isinstance(check, dict) and check.get("session_id"):
            checks.append(check)
    checks.sort(key=lambda c: c.get("applied_at", ""))
    return checks


def save_check(data_dir: str | Path, check: dict[str, Any]) -> Path:
    ensure_dirs(data_dir)
    return write_json(check_path(data_dir, check["session_id"]), check)


# ---------------------------------------------------------------------------
# resets / archive
# ---------------------------------------------------------------------------

def load_resets(data_dir: str | Path) -> dict[str, str]:
    raw = read_json(data_path(data_dir) / "resets.json", {}) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}


def save_resets(data_dir: str | Path, resets: dict[str, str]) -> Path:
    return write_json(data_path(data_dir) / "resets.json", resets)


def reset_word(data_dir: str | Path, word: str) -> dict[str, str]:
    resets = load_resets(data_dir)
    resets[word.strip().lower()] = now_iso()
    save_resets(data_dir, resets)
    return resets


def archive_learning(data_dir: str | Path) -> Path | None:
    """Move checks/ and resets.json into archive/; keep sessions/history."""
    root = ensure_dirs(data_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / ARCHIVE_DIR / f"learning-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    checks = root / CHECK_DIR
    if checks.exists() and any(checks.iterdir()):
        shutil.move(str(checks), str(dest / CHECK_DIR))
    checks.mkdir(parents=True, exist_ok=True)
    resets = root / "resets.json"
    if resets.exists():
        shutil.move(str(resets), str(dest / "resets.json"))
    return dest if any(dest.iterdir()) else None
