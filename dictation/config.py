"""Shared configuration: sources, defaults, ids and timestamps."""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any

VERSION = "0.1.0"

DEFAULT_THRESHOLD = 5
DEFAULT_SETTINGS: dict[str, Any] = {
    "threshold": DEFAULT_THRESHOLD,
    "default_source": "new_cet4",
    "default_mode": "auto",
    "default_interval": 6,
    "default_repeat": 1,
}

# A source combines a vocabulary level with a book (new / wrong / all).
# The order here is the order shown in the UI.
SOURCES: list[dict[str, str]] = [
    {"id": "new_cet4", "name": "四级·新词本", "level": "cet4", "book": "new",
     "desc": "四级词库里还没连续答对 5 次的词"},
    {"id": "new_cet6", "name": "六级·新词本", "level": "cet6", "book": "new",
     "desc": "六级词库里还没连续答对 5 次的词（含四级重合词）"},
    {"id": "new_cet6_new", "name": "六级新增·新词本", "level": "cet6_new", "book": "new",
     "desc": "只含六级新增、不含四级重合的词"},
    {"id": "new_all", "name": "全部·新词本", "level": "all", "book": "new",
     "desc": "四级 + 六级全部未掌握词"},
    {"id": "wrong_all", "name": "错题本", "level": "all", "book": "wrong",
     "desc": "答错过、且还没掌握的词（属于新词本的一部分）"},
    {"id": "all_cet4", "name": "四级·全部词库", "level": "cet4", "book": "all",
     "desc": "包含已掌握词，用于总复习"},
    {"id": "all_cet6", "name": "六级·全部词库", "level": "cet6", "book": "all",
     "desc": "包含已掌握词，用于总复习"},
    {"id": "all_all", "name": "全部词库", "level": "all", "book": "all",
     "desc": "所有四级 + 六级词，包含已掌握词"},
]
SOURCE_BY_ID: dict[str, dict[str, str]] = {s["id"]: s for s in SOURCES}
DEFAULT_SOURCE = "new_cet4"

LEVEL_NAMES = {
    "all": "全部",
    "cet4": "四级",
    "cet6": "六级",
    "cet6_new": "六级新增",
}

SESSION_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
WORD_KEY_RE = re.compile(r"^[a-z][a-z'’\-]*$")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def random_seed() -> int:
    return secrets.randbelow(900_000) + 100_000


def new_session_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def is_valid_session_id(value: str) -> bool:
    return bool(SESSION_ID_RE.match(value or ""))


def normalize_word_key(word: str) -> str:
    return (word or "").strip().lower()
