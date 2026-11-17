"""Vocabulary loading, source filtering and frequency order."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .config import SOURCES, SOURCE_BY_ID, normalize_word_key
from .store import read_json


class WordRepository:
    def __init__(self, data_dir: str | Path):
        path = Path(data_dir) / "words.json"
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

    # ------------------------------------------------------------------
    # levels
    # ------------------------------------------------------------------
    def level_words(self, level: str) -> list[dict[str, Any]]:
        if level == "all":
            return list(self.words)
        if level == "cet4":
            return [w for w in self.words if "cet4" in w["tags"]]
        if level == "cet6":
            return [w for w in self.words if "cet6" in w["tags"]]
        if level == "cet6_new":
            return [w for w in self.words if "cet6" in w["tags"] and "cet4" not in w["tags"]]
        raise ValueError(f"未知词库级别：{level}")

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
        source = SOURCE_BY_ID.get(source_id)
        if source is None:
            raise ValueError(f"未知词库来源：{source_id}")
        base = self.level_words(source["level"])
        book = source["book"]
        if book == "all":
            return base
        out = []
        for word in base:
            state = progress.state(word["word"]) if progress is not None else None
            if state is not None and state.mastered:
                continue
            if book == "wrong" and (state is None or state.wrong_count <= 0):
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
        return result

    def total_count(self) -> int:
        return len(self.words)

    def get(self, word: str) -> dict[str, Any] | None:
        return self.by_word.get(normalize_word_key(word))

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
