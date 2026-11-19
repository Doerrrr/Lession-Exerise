"""Derive the new-word book / wrong-answer book from applied check history.

Progress is never stored incrementally.  Every request recomputes it from the
chronological list of applied session results, which makes re-checking a
session idempotent: overwrite its check file and the derived state changes
exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .config import normalize_word_key

REVIEW_INTERVALS = (2, 5, 10, 20, 40, 80)
AFTER_LAPSE_INTERVALS = (1, 3, 7, 14, 30, 60)


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
    review_on: date | None = None
    review_stage: int = 0
    relearning: bool = False
    lapsed: bool = False
    familiar: bool = False

    def schedule_review(self, checked_on: date) -> None:
        intervals = AFTER_LAPSE_INTERVALS if self.lapsed else REVIEW_INTERVALS
        self.review_on = checked_on + timedelta(days=intervals[self.review_stage])

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
            "review_on": self.review_on.isoformat() if self.review_on else None,
            "relearning": self.relearning,
            "familiar": self.familiar,
        }


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone()
    except (TypeError, ValueError):
        return None


@dataclass
class Progress:
    threshold: int
    states: dict[str, WordState] = field(default_factory=dict)

    @property
    def relearning_threshold(self) -> int:
        return max(1, self.threshold - 1)

    def is_familiar(self, word: str) -> bool:
        state = self.state(word)
        return bool(state and state.familiar)

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
    familiar_words: set[str] | None = None,
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
        checked_dt = _parse_time(event.get("checked_at")) or created_dt
        for item in event.get("items") or []:
            word = str(item.get("word") or "").strip()
            if not word:
                continue
            key = normalize_word_key(word)
            reset_dt = reset_times.get(key)
            if reset_dt is not None and created_dt is not None and created_dt <= reset_dt:
                continue
            state = progress.state_or_default(word)
            state.total_seen += 1
            state.last_seen = created_at or state.last_seen
            verdict = item.get("verdict")
            if verdict == "correct":
                state.streak += 1
                state.total_correct += 1
                state.last_result = "correct"
                if state.mastered:
                    if state.review_on and created_dt and created_dt.date() >= state.review_on and checked_dt:
                        state.review_stage = min(state.review_stage + 1, len(REVIEW_INTERVALS) - 1)
                        state.schedule_review(checked_dt.date())
                else:
                    target = progress.relearning_threshold if state.relearning else threshold
                    if state.streak >= target:
                        state.mastered = True
                        state.relearning = False
                        state.review_stage = 0
                        if checked_dt:
                            state.schedule_review(checked_dt.date())
            elif state.mastered and verdict in {"blank", "uncertain"}:
                # An unanswered or inconclusive review stays due for the next round.
                state.last_result = verdict
            else:
                state.streak = 0
                state.wrong_count += 1
                state.last_result = "wrong"
                if state.mastered:
                    state.mastered = False
                    state.relearning = state.lapsed = True
                    state.review_on = None

    for word in familiar_words or ():
        state = progress.state_or_default(word)
        state.mastered = state.familiar = True
        state.review_on = None
    return progress
