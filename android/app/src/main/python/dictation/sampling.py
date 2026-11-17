"""Seeded, reproducible word sampling."""
from __future__ import annotations

import random
from typing import Any, Iterable


def frequency_key(word: dict[str, Any]) -> tuple[int, int, str]:
    frq = int(word.get("frq") or 0)
    bnc = int(word.get("bnc") or 0)
    return (
        frq if frq > 0 else 10**9,
        bnc if bnc > 0 else 10**9,
        str(word.get("word") or "").lower(),
    )


def sort_by_frequency(words: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(words, key=frequency_key)


def take_pool(words: Iterable[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sort_by_frequency(words)
    if limit and limit > 0:
        return ordered[:limit]
    return ordered


def sample_words(
    pool: list[dict[str, Any]],
    count: int,
    seed: int,
    order: str = "random",
) -> list[dict[str, Any]]:
    """Sample without replacement, deterministically from *seed*.

    The pool is expected to be already sorted by frequency (top-N cutoff has
    happened); selection is independent of that order because ``random.sample``
    is used on the list.  Assigns the playback index ``n``.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    if count > len(pool):
        raise ValueError("count cannot exceed pool size")
    rng = random.Random(int(seed))
    selected = rng.sample(pool, count)
    if order == "frequency":
        selected = sort_by_frequency(selected)
    elif order == "alpha":
        selected = sorted(selected, key=lambda w: str(w.get("word") or "").lower())
    # Any other value keeps the seeded random order.
    out = []
    for index, word in enumerate(selected, start=1):
        item = dict(word)
        item["n"] = index
        out.append(item)
    return out
