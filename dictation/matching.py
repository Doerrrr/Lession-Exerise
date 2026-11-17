"""Normalize Chinese answers and compare them with ECDICT translations.

The matcher is deliberately conservative: exact / substring sense hits are
auto-correct, close-but-not-exact answers become ``uncertain`` for the agent
to review, and everything else is wrong.  This keeps the learning record fair
when the dictionary lacks a synonym the user chose.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

POS_PREFIX_RE = re.compile(
    r"^(?:n|v|vt|vi|adj|adv|a|ad|prep|conj|pron|num|art|int|aux|abbr|pl|sing|phr|词组|短语)\.\s*",
    re.IGNORECASE,
)
LABEL_RE = re.compile(r"^(?:\[[^\]]*\]|【[^】]*】|〔[^〕]*〕)\s*")
PAREN_RE = re.compile(r"\([^)]*\)|（[^）]*）|\[[^\]]*\]|【[^】]*】")
SENSE_SPLIT_RE = re.compile(r"[;；,，、/\n\r|]+")
ANSWER_SPLIT_RE = re.compile(r"[;；,，、/\n\r|]+|\s+")
EDGE_CHARS = " \t\r\n.。:：;；,，、()（）[]【】\"'“”‘’"


@dataclass
class MatchResult:
    verdict: str
    score: float
    matched: str

    def to_dict(self) -> dict[str, object]:
        return {"verdict": self.verdict, "score": self.score, "matched": self.matched}


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("…", "").replace("...", "")
    value = re.sub(r"\s+", "", value)
    value = value.strip(EDGE_CHARS)
    return value.lower()


def clean_sense(raw: str) -> str:
    value = (raw or "").strip()
    for _ in range(3):
        changed = False
        new = LABEL_RE.sub("", value).strip()
        if new != value:
            value = new
            changed = True
        new = POS_PREFIX_RE.sub("", value).strip()
        if new != value:
            value = new
            changed = True
        if not changed:
            break
    value = value.strip("\"'“”‘’")
    return normalize(value)


def _variants(raw: str) -> list[str]:
    base = clean_sense(raw)
    if not base:
        return []
    out = [base]
    no_paren = normalize(PAREN_RE.sub("", raw))
    if no_paren and no_paren != base:
        out.append(no_paren)
    return out


def split_senses(translation: str) -> list[str]:
    text = (translation or "").replace("\\n", "\n")
    result: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        for part in SENSE_SPLIT_RE.split(line):
            for variant in _variants(part):
                if variant and variant not in seen:
                    seen.add(variant)
                    result.append(variant)
    return result


def split_answer(answer: str) -> list[str]:
    text = (answer or "").strip()
    if not text:
        return []
    result: list[str] = []
    seen: set[str] = set()
    whole = normalize(text)
    if whole:
        result.append(whole)
        seen.add(whole)
    for part in ANSWER_SPLIT_RE.split(text):
        for variant in _variants(part):
            if variant and variant not in seen:
                seen.add(variant)
                result.append(variant)
    return result


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def match_answer(answer: str, translation: str) -> MatchResult:
    if not (answer or "").strip():
        return MatchResult("blank", 0.0, "")
    answer_parts = split_answer(answer)
    senses = split_senses(translation)
    if not answer_parts:
        return MatchResult("blank", 0.0, "")
    if not senses:
        return MatchResult("uncertain", 0.0, "")

    best_score = 0.0
    best_sense = senses[0]
    uncertain_hit = False

    for part in answer_parts:
        if not part:
            continue
        for sense in senses:
            if part == sense:
                return MatchResult("correct", 1.0, sense)
            if len(part) >= 2 and len(sense) >= 2 and (part in sense or sense in part):
                return MatchResult("correct", 1.0, sense)
            score = _ratio(part, sense)
            if score > best_score:
                best_score = score
                best_sense = sense
            if score >= 0.5 and (len(part) >= 2 or len(sense) >= 2):
                uncertain_hit = True

    best_score = round(best_score, 3)
    if uncertain_hit:
        return MatchResult("uncertain", best_score, best_sense)
    return MatchResult("wrong", best_score, best_sense)


def concise_translation(translation: str, limit: int = 3) -> str:
    """First few senses for tables / cards."""
    senses = split_senses(translation)
    if not senses:
        return (translation or "").strip()
    # Preserve original text a bit better than the normalized senses: use the
    # first non-empty lines, compacted.
    lines = [line.strip() for line in (translation or "").split("\n") if line.strip()]
    text = "；".join(lines[:limit]) if lines else "；".join(senses[:limit])
    return text
