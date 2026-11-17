"""Markdown answer-sheet generation and parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import SESSION_ID_RE

BLOCK_RE = re.compile(r"^\s*(\d+)\s*[.、)]\s*(.*)$")
WORD_RE = re.compile(r"^([A-Za-z][A-Za-z'’\-]*)\s*(.*)$")
ANSWER_LABEL_RE = re.compile(r"^你的答案\s*[:：]\s*(.*)$")
SESSION_RE = re.compile(r"场次\s*[:：]\s*([0-9]{8}-[0-9]{6}-[0-9a-f]{4})")
SEED_RE = re.compile(r"种子\s*[:：]\s*(\d+)")
SKIP_PREFIXES = ("#", "---", "检查方式", "python ", "python3 ", "建议", "提示")


@dataclass
class ParsedSheet:
    session_id: str | None = None
    seed: int | None = None
    words: dict[str, str] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def effective_heard(session: dict[str, Any]) -> int:
    count = int(session.get("count") or len(session.get("words") or []))
    heard = int(session.get("heard_count") or 0)
    if heard <= 0:
        return count
    return max(0, min(heard, count))


def build_sheet(
    session: dict[str, Any],
    answers: dict[str, str] | None = None,
    *,
    heard_only: bool = True,
) -> str:
    answers = {str(k): str(v) for k, v in (answers or {}).items()}
    count = int(session.get("count") or len(session.get("words") or []))
    heard = effective_heard(session) if heard_only else count
    words = (session.get("words") or [])[:heard]
    pool = session.get("pool_size") or session.get("pool_requested") or 0
    pool_text = f"前 {pool} 个" if pool else "全部"
    lines = [
        "# 单词听写答题纸",
        "",
        f"- 场次：{session.get('id', '')}",
        f"- 种子：{session.get('seed', '')}",
        f"- 词库：{session.get('source_name', '')}",
        f"- 候选池：{pool_text}；抽中 {count} 个",
        f"- 题目：{heard} / {count}",
        "- 提示：在「你的答案」后写中文意思；多个义项可用「；」分隔；未作答留空。",
        "",
        "---",
        "",
    ]
    for word in words:
        n = str(word.get("n", ""))
        lines.append(f"{n}. {word.get('word', '')}")
        lines.append(f"   你的答案：{answers.get(n, '')}")
        lines.append("")
    lines.extend([
        "---",
        "",
        "检查方式：在应用里点「AI 判卷」（需先在设置里配置 DeepSeek API Key）。",
        "提示：本应用不提供本地判卷；答题纸可以保存/导出作为备份。",
        "",
    ])
    return "\n".join(lines)


def parse_sheet(text: str) -> ParsedSheet:
    result = ParsedSheet()
    text = (text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    match = SESSION_RE.search(text)
    if match:
        result.session_id = match.group(1)
    match = SEED_RE.search(text)
    if match:
        try:
            result.seed = int(match.group(1))
        except ValueError:
            pass

    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if not current:
            return
        n = str(current["n"])
        word = str(current.get("word") or "").strip()
        parts = [p.strip() for p in current.get("parts", []) if str(p).strip()]
        inline = str(current.get("inline") or "").strip()
        if inline:
            parts.insert(0, inline)
        answer = "；".join(parts).strip()
        if word:
            result.words[n] = word
        else:
            result.warnings.append(f"第 {n} 题没有识别到英文单词")
        result.answers[n] = answer
        current = None

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        block = BLOCK_RE.match(line)
        if block:
            finish()
            n = int(block.group(1))
            rest = block.group(2).strip()
            current = {"n": n, "word": "", "parts": [], "inline": ""}
            if rest:
                word_match = WORD_RE.match(rest)
                if word_match:
                    current["word"] = word_match.group(1)
                    remainder = word_match.group(2).strip()
                    if remainder:
                        label = ANSWER_LABEL_RE.match(remainder)
                        if label:
                            current["inline"] = label.group(1).strip()
                        else:
                            remainder = remainder.lstrip("-—:： ").strip()
                            if remainder:
                                current["inline"] = remainder
                else:
                    # No leading English word: treat it as an answer/comment.
                    current["parts"].append(rest)
            continue

        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith(SKIP_PREFIXES):
            continue
        label = ANSWER_LABEL_RE.match(stripped)
        if label:
            value = label.group(1).strip()
            if value:
                current["parts"].append(value)
            continue
        # Ignore a repeated header-like metadata line.
        if stripped.startswith("-"):
            continue
        if not current.get("word"):
            word_match = WORD_RE.match(stripped)
            if word_match and len(stripped.split()) <= 3:
                current["word"] = word_match.group(1)
                remainder = word_match.group(2).strip()
                if remainder:
                    current["parts"].append(remainder.lstrip("-—:： ").strip())
                continue
        current["parts"].append(stripped)

    finish()
    if not result.answers:
        result.warnings.append("没有解析到任何题目，请确认答题纸格式")
    return result


def read_sheet(path: str | Path) -> ParsedSheet:
    return parse_sheet(Path(path).read_text(encoding="utf-8"))
