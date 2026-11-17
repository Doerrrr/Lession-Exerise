"""Derive the new-word book / wrong-answer book from applied check history.

Progress is never stored incrementally.  Every request recomputes it from the
chronological list of applied session results, which makes re-checking a
session idempotent: overwrite its check file and the derived state changes
exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .config import normalize_word_key


@dataclass
class WordState:
    word: str
    streak: int = 0
    wrong_count: int = 0
    total_seen: int = 0
    total_correct: int = 0
    mastered: bool = False
    last_seen: str | None = None
    last_result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "streak": self.streak,
            "wrong_count": self.wrong_count,
            "total_seen": self.total_seen,
            "total_correct": self.total_correct,
            "mastered": self.mastered,
            "last_seen": self.last_seen,
            "last_result": self.last_result,
        }


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Progress:
    threshold: int
    states: dict[str, WordState] = field(default_factory=dict)

    def state(self, word: str) -> WordState | None:
        return self.states.get(normalize_word_key(word))

    def state_or_default(self, word: str) -> WordState:
        key = normalize_word_key(word)
        state = self.states.get(key)
        if state is None:
            state = WordState(word=word)
            self.states[key] = state
        return state

    def is_mastered(self, word: str) -> bool:
        state = self.states.get(normalize_word_key(word))
        return bool(state and state.mastered)

    def wrong_count(self, word: str) -> int:
        state = self.states.get(normalize_word_key(word))
        return state.wrong_count if state else 0

    def is_wrong(self, word: str) -> bool:
        state = self.states.get(normalize_word_key(word))
        return bool(state and state.wrong_count > 0 and not state.mastered)

    def counts(self) -> dict[str, int]:
        mastered = 0
        wrong = 0
        seen = 0
        for state in self.states.values():
            if state.total_seen:
                seen += 1
            if state.mastered:
                mastered += 1
            elif state.wrong_count > 0:
                wrong += 1
        return {"mastered": mastered, "wrong": wrong, "seen": seen}

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "states": {key: state.to_dict() for key, state in self.states.items()},
        }


def derive(
    events: Iterable[dict[str, Any]],
    threshold: int,
    resets: dict[str, str] | None = None,
) -> Progress:
    threshold = max(1, int(threshold))
    resets = resets or {}
    reset_times: dict[str, datetime | None] = {
        word: _parse_time(value) for word, value in resets.items()
    }
    ordered = sorted(
        events,
        key=lambda event: (str(event.get("created_at") or ""), str(event.get("session_id") or "")),
    )
    progress = Progress(threshold=threshold)
    for event in ordered:
        created_at = str(event.get("created_at") or "")
        created_dt = _parse_time(created_at)
        for item in event.get("items") or []:
            word = str(item.get("word") or "").strip()
            if not word:
                continue
            key = normalize_word_key(word)
            reset_dt = reset_times.get(key)
            if reset_dt is not None and created_dt is not None and created_dt <= reset_dt:
                continue
            state = progress.states.get(key)
            if state is None:
                state = WordState(word=word)
                progress.states[key] = state
            state.total_seen += 1
            state.last_seen = created_at or state.last_seen
            if item.get("verdict") == "correct":
                state.streak += 1
                state.total_correct += 1
                state.last_result = "correct"
            else:
                state.streak = 0
                state.wrong_count += 1
                state.last_result = "wrong"
            state.mastered = state.streak >= threshold
    return progress
