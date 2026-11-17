"""Custom wordbook import: fixed format, validation and local storage.

Canonical format (see docs/WORDBOOK_FORMAT.md):

{
  "format": "dictation-wordbook",
  "version": 1,
  "name": "我的新词本",
  "description": "可选",
  "words": [
    {"word": "abandon", "translation": "放弃；抛弃",
     "phonetic": "ə'bændən", "frequency": 1000}
  ]
}

CSV is also accepted as a convenience:
    word,translation,phonetic,frequency
"""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import normalize_word_key, now_iso
from .store import read_json, write_json

FORMAT = "dictation-wordbook"
VERSION = 1
MAX_WORDS = 10_000
MAX_CONTENT = 2_000_000
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-]*$")
BOOK_ID_RE = re.compile(r"^wb-[0-9]{14}-[0-9a-f]{4}$")
DIR_NAME = "wordbooks"


class WordbookError(Exception):
    """Raised for invalid import content."""


def wordbooks_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / DIR_NAME


def _clean_name(raw: Any, fallback: str = "") -> str:
    name = str(raw or "").strip() or str(fallback or "").strip()
    name = re.sub(r"\.(json|csv|txt)$", "", name, flags=re.IGNORECASE).strip()
    name = name[:60].strip()
    if not name:
        raise WordbookError("词本名称不能为空（在 JSON 里写 name，或使用表单里的名称）")
    return name


def _to_frequency(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise WordbookError(f"frequency 必须是整数：{value!r}")
    if number < 0:
        raise WordbookError(f"frequency 不能为负数：{value!r}")
    return number


def _parse_words(raw_words: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_words, list) or not raw_words:
        raise WordbookError("words 必须是非空数组")
    if len(raw_words) > MAX_WORDS:
        raise WordbookError(f"词本最多 {MAX_WORDS} 个词，当前 {len(raw_words)} 个")
    merged: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_words, start=1):
        if not isinstance(raw, dict):
            raise WordbookError(f"第 {index} 个词条不是对象")
        word = str(raw.get("word") or "").strip()
        translation = str(raw.get("translation") or "").strip()
        if not word:
            raise WordbookError(f"第 {index} 个词条缺少 word")
        if not WORD_RE.match(word):
            raise WordbookError(
                f"第 {index} 个词 {word!r} 格式不对：只支持单个英文单词（可含 - 和 '）"
            )
        if not translation:
            raise WordbookError(f"第 {index} 个词 {word!r} 缺少 translation")
        phonetic = str(raw.get("phonetic") or "").strip()
        frequency = _to_frequency(raw.get("frequency", raw.get("frq", 0)))
        key = normalize_word_key(word)
        merged[key] = {
            "word": word,
            "phonetic": phonetic,
            "translation": translation,
            "frq": frequency,
            "bnc": 0,
            "tags": ["custom"],
        }
    if not merged:
        raise WordbookError("词本没有可用的单词")
    return list(merged.values())


def _parse_json(content: str, filename: str, name_override: str | None) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise WordbookError(f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise WordbookError("词本 JSON 必须是一个对象")
    fmt = str(data.get("format") or "").strip()
    if fmt != FORMAT:
        raise WordbookError(
            f"format 必须是 {FORMAT!r}；当前是 {fmt!r}。请下载模板对照。"
        )
    try:
        version = int(data.get("version"))
    except (TypeError, ValueError):
        version = 0
    if version != VERSION:
        raise WordbookError(f"version 必须是 {VERSION}；当前是 {data.get('version')!r}")
    name = _clean_name(name_override or data.get("name"), filename)
    description = str(data.get("description") or "").strip()
    words = _parse_words(data.get("words"))
    return {"name": name, "description": description, "words": words}


def _parse_csv(content: str, filename: str, name_override: str | None) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise WordbookError("CSV 需要表头：word,translation,phonetic,frequency")
    fields = [str(field or "").strip().lower() for field in reader.fieldnames]
    if "word" not in fields or "translation" not in fields:
        raise WordbookError("CSV 表头至少需要 word,translation（可选 phonetic,frequency）")
    if len(content) > MAX_CONTENT:
        raise WordbookError("文件太大（最多 2 MB）")
    raw_words: list[dict[str, Any]] = []
    for line_number, row in enumerate(reader, start=2):
        normalized = {
            str(key or "").strip().lower(): (value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        if not normalized.get("word") and not normalized.get("translation"):
            continue
        raw_words.append({
            "word": normalized.get("word", ""),
            "translation": normalized.get("translation", ""),
            "phonetic": normalized.get("phonetic", ""),
            "frequency": normalized.get("frequency") or normalized.get("frq") or 0,
            "_line": line_number,
        })
        if len(raw_words) > MAX_WORDS:
            raise WordbookError(f"词本最多 {MAX_WORDS} 个词")
    if not raw_words:
        raise WordbookError("CSV 没有数据行")
    try:
        words = _parse_words([
            {k: v for k, v in row.items() if k != "_line"} for row in raw_words
        ])
    except WordbookError as exc:
        raise WordbookError(f"CSV 数据有误：{exc}") from exc
    name = _clean_name(name_override, filename)
    return {"name": name, "description": "", "words": words}


def parse_import(
    content: str,
    filename: str = "",
    name_override: str | None = None,
) -> dict[str, Any]:
    if content is None:
        raise WordbookError("缺少文件内容")
    content = str(content).lstrip("\ufeff")
    if not content.strip():
        raise WordbookError("文件是空的")
    if len(content.encode("utf-8")) > MAX_CONTENT:
        raise WordbookError("文件太大（最多 2 MB）")
    if content.lstrip().startswith("{"):
        return _parse_json(content, filename, name_override)
    return _parse_csv(content, filename, name_override)


def save_book(data_dir: str | Path, parsed: dict[str, Any]) -> dict[str, Any]:
    book_id = f"wb-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"
    book = {
        "format": FORMAT,
        "version": VERSION,
        "id": book_id,
        "name": parsed["name"],
        "description": parsed.get("description", ""),
        "created_at": now_iso(),
        "words": parsed["words"],
    }
    directory = wordbooks_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / f"{book_id}.json", book)
    return book


def load_books(data_dir: str | Path) -> list[dict[str, Any]]:
    directory = wordbooks_dir(data_dir)
    if not directory.exists():
        return []
    books = []
    for path in directory.glob("*.json"):
        if not BOOK_ID_RE.match(path.stem):
            continue
        try:
            data = read_json(path, None)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            continue
        if not isinstance(data.get("words"), list) or not data["words"]:
            continue
        data.setdefault("id", path.stem)
        data.setdefault("name", path.stem)
        data.setdefault("created_at", "")
        books.append(data)
    books.sort(key=lambda book: str(book.get("created_at") or ""), reverse=True)
    return books


def delete_book(data_dir: str | Path, book_id: str) -> bool:
    if not BOOK_ID_RE.match(book_id or ""):
        raise WordbookError("词本 ID 格式不正确")
    path = wordbooks_dir(data_dir) / f"{book_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def summarize_book(book: dict[str, Any], progress: Any | None = None) -> dict[str, Any]:
    words = book.get("words") or []
    summary = {
        "id": book.get("id"),
        "name": book.get("name"),
        "description": book.get("description", ""),
        "count": len(words),
        "created_at": book.get("created_at"),
    }
    if progress is not None:
        active = 0
        mastered = 0
        for word in words:
            state = progress.state(word.get("word", ""))
            if state and state.mastered:
                mastered += 1
            else:
                active += 1
        summary["new_count"] = active
        summary["mastered_count"] = mastered
    return summary


def template_json() -> str:
    data = {
        "format": FORMAT,
        "version": VERSION,
        "name": "我的新词本",
        "description": "可选说明，例如：考研核心词",
        "words": [
            {"word": "abandon", "translation": "放弃；抛弃", "phonetic": "ə'bændən", "frequency": 1000},
            {"word": "ability", "translation": "能力；才干", "phonetic": "ə'biliti", "frequency": 800},
            {"word": "apple", "translation": "苹果；苹果树", "phonetic": "'æpl", "frequency": 1500},
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def template_csv() -> str:
    lines = [
        "word,translation,phonetic,frequency",
        "abandon,放弃；抛弃,ə'bændən,1000",
        "ability,能力；才干,ə'biliti,800",
        "apple,苹果；苹果树,'æpl,1500",
    ]
    return "\n".join(lines) + "\n"
