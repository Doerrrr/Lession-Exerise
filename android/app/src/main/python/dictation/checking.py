"""Check computation, application and report generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import now_iso
from .progress import Progress, derive
from .store import (
    answers_json_path,
    answers_md_path,
    check_path,
    list_checks,
    list_sessions,
    load_check,
    load_familiar_words,
    load_resets,
    load_session,
    load_settings,
    save_check,
    write_text,
)

VERDICT_LABELS = {
    "correct": "正确",
    "wrong": "错误",
    "blank": "未作答",
    "uncertain": "待确认",
}


# ---------------------------------------------------------------------------
# progress over all applied checks
# ---------------------------------------------------------------------------

def load_events(data_dir: str | Path, exclude_session_id: str | None = None) -> list[dict[str, Any]]:
    sessions = {s["id"]: s for s in list_sessions(data_dir)}
    events: list[dict[str, Any]] = []
    for check in list_checks(data_dir):
        session_id = str(check.get("session_id") or "")
        if not session_id or session_id == exclude_session_id:
            continue
        session = sessions.get(session_id)
        created_at = (
            (session or {}).get("created_at")
            or check.get("session_created_at")
            or check.get("applied_at")
            or ""
        )
        items = [
            {"word": str(item.get("word") or ""), "verdict": str(item.get("verdict") or "")}
            for item in (check.get("items") or [])
            if item.get("word")
        ]
        events.append({
            "session_id": session_id,
            "created_at": created_at,
            "checked_at": check.get("applied_at") or check.get("graded_at") or created_at,
            "items": items,
        })
    return events


def build_progress(
    data_dir: str | Path,
    threshold: int | None = None,
    exclude_session_id: str | None = None,
) -> Progress:
    if threshold is None:
        threshold = int(load_settings(data_dir).get("threshold", 5))
    events = load_events(data_dir, exclude_session_id=exclude_session_id)
    resets = load_resets(data_dir)
    return derive(events, threshold, resets, load_familiar_words(data_dir))


def book_counts(progress: Progress, total_words: int) -> dict[str, int]:
    counts = progress.counts()
    mastered = min(counts["mastered"], total_words)
    return {
        "total": total_words,
        "new": max(0, total_words - mastered),
        "wrong": counts["wrong"],
        "mastered": mastered,
        "seen": counts["seen"],
        "threshold": progress.threshold,
    }


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def has_answers(data_dir: str | Path, session_id: str) -> bool:
    return (
        answers_json_path(data_dir, session_id).exists()
        or answers_md_path(data_dir, session_id).exists()
    )


def has_check(data_dir: str | Path, session_id: str) -> bool:
    return check_path(data_dir, session_id).exists()


def session_summary(data_dir: str | Path, session: dict[str, Any]) -> dict[str, Any]:
    session_id = session["id"]
    check = load_check(data_dir, session_id)
    answered = has_answers(data_dir, session_id)
    status = "checked" if check else ("answered" if answered else (
        "heard" if int(session.get("heard_count") or 0) > 0 else "new"
    ))
    summary = check.get("summary") if check else None
    return {
        "id": session_id,
        "created_at": session.get("created_at"),
        "source_name": session.get("source_name"),
        "seed": session.get("seed"),
        "count": session.get("count"),
        "heard_count": session.get("heard_count"),
        "pool_size": session.get("pool_size"),
        "status": status,
        "accuracy": (summary or {}).get("accuracy"),
        "correct": (summary or {}).get("correct"),
        "wrong": (summary or {}).get("wrong"),
        "blank": (summary or {}).get("blank"),
        "has_answers": answered,
        "has_check": bool(check),
    }


def find_session(
    data_dir: str | Path,
    session_id: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    if session_id:
        session = load_session(data_dir, session_id)
        if not session:
            raise ValueError(f"找不到场次：{session_id}")
        return session
    sessions = list_sessions(data_dir)
    if seed is not None:
        matches = [s for s in sessions if int(s.get("seed") or 0) == int(seed)]
        if not matches:
            raise ValueError(f"找不到种子为 {seed} 的场次")
        if len(matches) > 1:
            ids = ", ".join(s["id"] for s in matches[:5])
            raise ValueError(f"种子 {seed} 对应多个场次，请用 --session 指定：{ids}")
        return matches[0]
    pending = [s for s in sessions if has_answers(data_dir, s["id"]) and not has_check(data_dir, s["id"])]
    if pending:
        return pending[0]
    if sessions:
        return sessions[0]
    raise ValueError("还没有任何场次，请先在应用里开始一轮听写")


def pending_sessions(data_dir: str | Path) -> list[dict[str, Any]]:
    out = []
    for session in list_sessions(data_dir):
        if has_answers(data_dir, session["id"]) and not has_check(data_dir, session["id"]):
            out.append(session_summary(data_dir, session))
    return out


# ---------------------------------------------------------------------------
# check application
# ---------------------------------------------------------------------------

def summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    counts = {"correct": 0, "wrong": 0, "blank": 0, "uncertain": 0}
    for item in items:
        verdict = str(item.get("verdict") or "wrong")
        counts[verdict if verdict in counts else "wrong"] += 1
    decided = counts["correct"] + counts["wrong"] + counts["blank"]
    accuracy = round(counts["correct"] / decided, 4) if decided else 0.0
    return {
        "total": total,
        **counts,
        "accuracy": accuracy,
    }


def apply_result(
    data_dir: str | Path,
    session: dict[str, Any],
    result: dict[str, Any],
    total_words: int,
) -> dict[str, Any]:
    threshold = int(result.get("threshold") or load_settings(data_dir).get("threshold", 5))
    session_id = session["id"]
    events = load_events(data_dir, exclude_session_id=session_id)
    resets = load_resets(data_dir)
    familiar_words = load_familiar_words(data_dir)
    before = derive(events, threshold, resets, familiar_words)
    applied_at = now_iso()
    event = {
        "session_id": session_id,
        "created_at": session.get("created_at") or applied_at,
        "checked_at": applied_at,
        "items": [
            {"word": item["word"], "verdict": item["verdict"]}
            for item in result.get("items") or []
        ],
    }
    after = derive(events + [event], threshold, resets, familiar_words)
    newly_mastered = []
    for key, state in after.states.items():
        if state.mastered:
            old = before.states.get(key)
            if not (old and old.mastered):
                newly_mastered.append(state.word)
    newly_mastered.sort(key=str.lower)
    for item in result.get("items") or []:
        state = after.state(item["word"])
        if state and state.review_on:
            item["review_on"] = state.review_on.isoformat()
    result["applied_at"] = applied_at
    result["threshold"] = threshold
    result["newly_mastered"] = newly_mastered
    result["book_counts_before"] = book_counts(before, total_words)
    result["book_counts_after"] = book_counts(after, total_words)
    report_path = check_path(data_dir, session_id).with_suffix(".report.md")
    result["report_path"] = str(report_path)
    save_check(data_dir, result)
    write_text(report_path, report_markdown(session, result))
    return result


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

def _table_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", "<br>").strip()


def report_markdown(session: dict[str, Any], result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    items = result.get("items") or []
    lines = [
        f"# 听写检查报告 · {session.get('id', '')}",
        "",
        f"- 词库：{session.get('source_name', '')}",
        f"- 种子：{session.get('seed', '')}",
        f"- 检查时间：{result.get('applied_at', '')}",
    ]
    if result.get("engine") == "deepseek":
        if result.get("model_called") is False:
            lines.append("- 判卷：本地")
        else:
            lines.append(f"- 判卷：DeepSeek（{result.get('model', 'deepseek-chat')}）")
    lines.extend([
        f"- 题目：{summary.get('total', 0)}；正确 {summary.get('correct', 0)}；错误 {summary.get('wrong', 0)}；未作答 {summary.get('blank', 0)}；待确认 {summary.get('uncertain', 0)}",
        f"- 正确率：{round(float(summary.get('accuracy') or 0) * 100, 1)}%",
        "",
        "## 全部题目",
        "",
        "| # | 单词 | 你的答案 | 结果 |",
        "|---|------|----------|------|",
    ])
    for item in items:
        lines.append(
            f"| {item.get('n')} | {_table_cell(item.get('word', ''))} | "
            f"{_table_cell(item.get('answer', '') or '—')} | "
            f"{VERDICT_LABELS.get(item.get('verdict'), item.get('verdict'))} |"
        )
    mistakes = [i for i in items if i.get("verdict") != "correct"]
    lines.extend(["", "## 错题与参考释义", ""])
    if not mistakes:
        lines.append("本轮全部正确。")
    else:
        for item in mistakes:
            lines.append(f"### {item.get('n')}. {item.get('word')}")
            lines.append("")
            lines.append(f"- 你的答案：{item.get('answer') or '（未作答）'}")
            lines.append(f"- 判定：{VERDICT_LABELS.get(item.get('verdict'), item.get('verdict'))}")
            if item.get("reason"):
                lines.append(f"- AI 理由：{item.get('reason')}")
            expected = str(item.get("expected") or "").replace("\n", "；")
            lines.append(f"- 参考释义：{expected}")
            lines.append("")
    newly = result.get("newly_mastered") or []
    lines.extend(["## 词本变化", ""])
    before = result.get("book_counts_before") or {}
    after = result.get("book_counts_after") or {}
    lines.append(
        f"- 新词本：{before.get('new', '?')} → {after.get('new', '?')}"
    )
    lines.append(
        f"- 错题本：{before.get('wrong', '?')} → {after.get('wrong', '?')}"
    )
    lines.append(
        f"- 已掌握：{before.get('mastered', '?')} → {after.get('mastered', '?')}"
    )
    if newly:
        lines.append(f"- 本轮新掌握：{'、'.join(newly)}")
    lines.extend(["", "---", "", "下一轮直接在应用里开始即可；错题本会自动包含本轮错词。"])
    return "\n".join(lines) + "\n"


def resolve_overrides(
    result: dict[str, Any],
    overrides: dict[str, str],
) -> list[str]:
    """Apply ``{key: correct|wrong}`` where key is a number or a word.

    Returns human-readable warnings for keys that did not match.
    """
    warnings = []
    by_n = {str(item.get("n")): item for item in result.get("items") or []}
    by_word = {str(item.get("word")).lower(): item for item in result.get("items") or []}
    for key, verdict in overrides.items():
        verdict = verdict.strip().lower()
        if verdict not in {"correct", "wrong"}:
            warnings.append(f"忽略无效判定 {key}={verdict}")
            continue
        item = by_n.get(str(key)) or by_word.get(str(key).lower())
        if item is None:
            warnings.append(f"找不到要覆盖的题目：{key}")
            continue
        item["verdict"] = verdict
        item["overridden"] = True
    result["summary"] = summarize_items(result.get("items") or [])
    return warnings


def finalize_verdicts(result: dict[str, Any], uncertain_as: str = "wrong") -> None:
    for item in result.get("items") or []:
        if item.get("verdict") == "uncertain":
            item["verdict"] = "correct" if uncertain_as == "correct" else "wrong"
    result["summary"] = summarize_items(result.get("items") or [])


def blank_count(result: dict[str, Any]) -> int:
    return sum(1 for item in result.get("items") or [] if item.get("verdict") == "blank")
