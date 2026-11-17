"""Vocabulary loading, source filtering and frequency order."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .config import SOURCES, SOURCE_BY_ID, normalize_word_key
from .store import read_json
from .wordbooks import load_books, summarize_book


class WordRepository:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        path = self.data_dir / "words.json"
        if not path.exists():
            raise FileNotFoundError(
                f"找不到词库 {path}，请先运行：python tools/build_words.py"
            )
        data = read_json(path, {}) or {}
        self.meta: dict[str, Any] = data.get("meta") or {}
        raw_words = data.get("words") or []
        self.words: list[dict[str, Any]] = []
        self.by_word: dict[str, dict[str, Any]] = {}
        for raw in raw_words:
            if not isinstance(raw, dict) or not raw.get("word"):
                continue
            entry = {
                "word": str(raw["word"]).strip(),
                "phonetic": str(raw.get("phonetic") or ""),
                "translation": str(raw.get("translation") or ""),
                "frq": int(raw.get("frq") or 0),
                "bnc": int(raw.get("bnc") or 0),
                "tags": list(raw.get("tags") or []),
            }
            key = normalize_word_key(entry["word"])
            if not key or key in self.by_word:
                continue
            self.by_word[key] = entry
            self.words.append(entry)
        self.custom_books: list[dict[str, Any]] = []
        self.custom_by_id: dict[str, dict[str, Any]] = {}
        self.custom_words: list[dict[str, Any]] = []
        self.custom_by_word: dict[str, dict[str, Any]] = {}
        self.reload_custom()

    # ------------------------------------------------------------------
    # custom wordbooks
    # ------------------------------------------------------------------
    def reload_custom(self) -> None:
        self.custom_books = load_books(self.data_dir)
        self.custom_by_id = {book["id"]: book for book in self.custom_books}
        self.custom_words = [
            word
            for book in self.custom_books
            for word in (book.get("words") or [])
            if isinstance(word, dict) and word.get("word")
        ]
        self.custom_by_word = {}
        for word in self.custom_words:
            key = normalize_word_key(word["word"])
            if key:
                self.custom_by_word[key] = word

    def custom_source_info(self, source_id: str) -> dict[str, Any] | None:
        if not isinstance(source_id, str):
            return None
        mode = None
        book_id = ""
        if source_id.startswith("custom_new:"):
            mode = "new"
            book_id = source_id.split(":", 1)[1]
        elif source_id.startswith("custom_all:"):
            mode = "all"
            book_id = source_id.split(":", 1)[1]
        if mode is None or not book_id:
            return None
        book = self.custom_by_id.get(book_id)
        if not book:
            return None
        label = "新词本" if mode == "new" else "全部"
        desc = (
            "导入的自定义词本（未掌握）"
            if mode == "new"
            else "导入的自定义词本（含已掌握）"
        )
        return {
            "id": source_id,
            "name": f"自定义·{book.get('name', book_id)}·{label}",
            "level": f"custom:{book_id}",
            "book": "custom",
            "desc": desc,
            "mode": mode,
            "custom_id": book_id,
        }

    def source_info(self, source_id: str) -> dict[str, Any] | None:
        built_in = SOURCE_BY_ID.get(source_id)
        if built_in is not None:
            return built_in
        return self.custom_source_info(source_id)

    def custom_book_summaries(self, progress: Any | None = None) -> list[dict[str, Any]]:
        return [summarize_book(book, progress) for book in self.custom_books]

    def has_custom_word(self, word: str) -> bool:
        return normalize_word_key(word) in self.custom_by_word

    def all_words(self) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {
            normalize_word_key(word["word"]): word for word in self.words
        }
        merged.update(self.custom_by_word)
        return list(merged.values())

    # ------------------------------------------------------------------
    # levels
    # ------------------------------------------------------------------
    def level_words(self, level: str) -> list[dict[str, Any]]:
        if level == "all":
            return self.all_words()
        if level == "builtin":
            return list(self.words)
        if level == "cet4":
            return [w for w in self.words if "cet4" in w["tags"]]
        if level == "cet6":
            return [w for w in self.words if "cet6" in w["tags"]]
        if level == "cet6_new":
            return [w for w in self.words if "cet6" in w["tags"] and "cet4" not in w["tags"]]
        if level.startswith("custom:"):
            book = self.custom_by_id.get(level.split(":", 1)[1])
            if book:
                return list(book.get("words") or [])
        raise ValueError(f"未知词库级别：{level}")

    def catalog_words(self, level: str) -> list[dict[str, Any]]:
        if level == "all":
            return self.all_words()
        return self.level_words(level)

    def catalog_levels(self) -> list[str]:
        return (
            ["all", "builtin", "cet4", "cet6", "cet6_new"]
            + [f"custom:{book['id']}" for book in self.custom_books]
        )

    def level_counts(self) -> dict[str, int]:
        counts = {"all": len(self.words), "cet4": 0, "cet6": 0, "cet6_new": 0}
        for w in self.words:
            tags = w["tags"]
            if "cet4" in tags:
                counts["cet4"] += 1
            if "cet6" in tags:
                counts["cet6"] += 1
                if "cet4" not in tags:
                    counts["cet6_new"] += 1
        return counts

    # ------------------------------------------------------------------
    # pools
    # ------------------------------------------------------------------
    def source_pool(self, source_id: str, progress: Any | None = None) -> list[dict[str, Any]]:
        source = self.source_info(source_id)
        if source is None:
            raise ValueError(f"未知词库来源：{source_id}")
        if source.get("book") == "custom":
            book = self.custom_by_id.get(source.get("custom_id") or "")
            if not book:
                raise ValueError(f"未知自定义词本：{source_id}")
            base = list(book.get("words") or [])
            if source.get("mode") == "all":
                return base
            return [
                word for word in base
                if not (progress is not None and progress.is_mastered(word["word"]))
            ]
        if source_id == "wrong_all":
            base = self.all_words()
        else:
            base = self.level_words(source["level"])
        book_type = source["book"]
        if book_type == "all":
            return base
        out = []
        for word in base:
            state = progress.state(word["word"]) if progress is not None else None
            if state is not None and state.mastered:
                continue
            if book_type == "wrong" and (state is None or state.wrong_count <= 0):
                continue
            out.append(word)
        return out

    def source_counts(self, progress: Any | None = None) -> list[dict[str, Any]]:
        result = []
        for source in SOURCES:
            pool = self.source_pool(source["id"], progress)
            result.append({
                "id": source["id"],
                "name": source["name"],
                "level": source["level"],
                "book": source["book"],
                "desc": source["desc"],
                "count": len(pool),
            })
        for book in self.custom_books:
            for mode in ("new", "all"):
                source_id = f"custom_{mode}:{book['id']}"
                source = self.custom_source_info(source_id)
                if not source:
                    continue
                pool = self.source_pool(source_id, progress)
                result.append({
                    "id": source_id,
                    "name": source["name"],
                    "level": source["level"],
                    "book": "custom",
                    "desc": source["desc"],
                    "count": len(pool),
                })
        return result

    def total_count(self) -> int:
        return len(self.words)

    def catalog_total_count(self) -> int:
        return len(self.all_words())

    def get(self, word: str) -> dict[str, Any] | None:
        key = normalize_word_key(word)
        return self.by_word.get(key) or self.custom_by_word.get(key)

    def filter_level(self, words: Iterable[dict[str, Any]], level: str) -> list[dict[str, Any]]:
        allowed = {id(w) for w in self.level_words(level)}
        return [w for w in words if id(w) in allowed]


def public_word(word: dict[str, Any], n: int) -> dict[str, Any]:
    """Strip the answer key before sending a word to the browser."""
    return {
        "n": n,
        "word": word["word"],
        "phonetic": word.get("phonetic", ""),
        "frq": word.get("frq", 0),
    }


def review_word(word: dict[str, Any], state: Any | None = None) -> dict[str, Any]:
    data = {
        "word": word["word"],
        "phonetic": word.get("phonetic", ""),
        "translation": word.get("translation", ""),
        "frq": word.get("frq", 0),
        "bnc": word.get("bnc", 0),
        "tags": word.get("tags", []),
    }
    if state is not None:
        data.update({
            "streak": state.streak,
            "wrong_count": state.wrong_count,
            "total_seen": state.total_seen,
            "total_correct": state.total_correct,
            "mastered": state.mastered,
            "last_seen": state.last_seen,
            "last_result": state.last_result,
        })
    return data
