#!/usr/bin/env python3
"""Check one dictation session against its answer sheet.

Examples:
    python tools/check.py --pending
    python tools/check.py --session 20261117-113000-a1b2
    python tools/check.py --session 20261117-113000-a1b2 --all
    python tools/check.py --session 20261117-113000-a1b2 --override 7=correct --apply
    python tools/check.py --session 20261117-113000-a1b2 --accept-uncertain=wrong --allow-blank --apply
    python tools/check.py --seed 483920
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dictation.checking import (  # noqa: E402
    apply_result,
    blank_count,
    compute_result,
    finalize_verdicts,
    find_session,
    pending_sessions,
    resolve_overrides,
)
from dictation.config import DEFAULT_THRESHOLD  # noqa: E402
from dictation.sheet import ParsedSheet, effective_heard, parse_sheet, read_sheet  # noqa: E402
from dictation.store import (  # noqa: E402
    answers_json_path,
    answers_md_path,
    load_settings,
    read_json,
)
from dictation.words import WordRepository  # noqa: E402

VERDICT_LABELS = {
    "correct": "正确",
    "wrong": "错误",
    "blank": "未作答",
    "uncertain": "待确认",
}


def default_data_dir() -> Path:
    return ROOT / "data"


def load_answers_for(
    data_dir: Path,
    session: dict,
    explicit_sheet: str | None,
) -> tuple[dict[str, str], dict[str, str], list[str], str]:
    """Return (answers, sheet_words, warnings, source_label)."""
    warnings: list[str] = []
    if explicit_sheet:
        parsed = read_sheet(explicit_sheet)
        source = explicit_sheet
    else:
        md_path = answers_md_path(data_dir, session["id"])
        json_path = answers_json_path(data_dir, session["id"])
        if md_path.exists():
            parsed = parse_sheet(md_path.read_text(encoding="utf-8"))
            source = str(md_path)
        elif json_path.exists():
            data = read_json(json_path, {}) or {}
            parsed = ParsedSheet()
            parsed.answers = {str(k): str(v) for k, v in (data.get("answers") or {}).items()}
            source = str(json_path)
        else:
            raise SystemExit(
                f"没有找到答案文件：\n  {md_path}\n  {json_path}\n"
                "请先在应用里保存答题纸，或用 --sheet 指定文件。"
            )
    if parsed.session_id and parsed.session_id != session["id"]:
        raise SystemExit(
            f"答题纸的场次是 {parsed.session_id}，与指定场次 {session['id']} 不一致。"
        )
    if parsed.warnings:
        warnings.extend(parsed.warnings)
    if not parsed.answers:
        raise SystemExit(f"答题纸没有解析到答案：{source}")
    return parsed.answers, parsed.words, warnings, source


def print_pending(data_dir: Path) -> int:
    pending = pending_sessions(data_dir)
    if not pending:
        print("没有待检查的答题纸。")
        return 0
    print("待检查答题纸：")
    for item in pending:
        print(
            f"  {item['id']}  {item.get('source_name', '')}  "
            f"{item.get('count', '?')} 题  已听 {item.get('heard_count', '?')}  "
            f"{item.get('created_at', '')}"
        )
    return 0


def print_items(items: list[dict], show_all: bool) -> None:
    shown = items if show_all else [i for i in items if i.get("verdict") != "correct"]
    if not shown:
        print("  没有错题。")
        return
    for item in shown:
        verdict = VERDICT_LABELS.get(item.get("verdict"), item.get("verdict"))
        answer = item.get("answer") or "（未作答）"
        print(f"  {item.get('n'):>3}. {item.get('word', '')}")
        print(f"       你的答案：{answer}")
        if item.get("verdict") == "uncertain":
            print(f"       判定：{verdict}（相似度 {item.get('score', 0)}，接近：{item.get('matched', '')}）")
        elif item.get("verdict") == "wrong":
            print(f"       判定：{verdict}（相似度 {item.get('score', 0)}）")
        else:
            print(f"       判定：{verdict}")
        expected = str(item.get("expected") or "").replace("\n", "；")
        print(f"       参考：{expected}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", help="场次 ID")
    parser.add_argument("--seed", type=int, help="按种子查找最近一场")
    parser.add_argument("--sheet", help="答题纸 Markdown 路径（默认自动找）")
    parser.add_argument("--pending", action="store_true", help="列出待检查的答题纸")
    parser.add_argument("--apply", action="store_true", help="写入检查记录并更新新词本/错题本")
    parser.add_argument("--override", action="append", default=[], metavar="N=correct|wrong",
                        help="人工改判，例如 7=correct；可重复")
    parser.add_argument("--accept-uncertain", choices=["wrong", "correct"], default=None,
                        help="把待确认项统一判错或判对")
    parser.add_argument("--allow-blank", action="store_true", help="允许未作答题存在并写入")
    parser.add_argument("--all", action="store_true", help="打印全部题目，而不只是错题")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if args.pending:
        return print_pending(data_dir)

    try:
        session = find_session(data_dir, session_id=args.session, seed=args.seed)
    except ValueError as exc:
        raise SystemExit(str(exc))

    settings = load_settings(data_dir)
    threshold = int(settings.get("threshold", DEFAULT_THRESHOLD))
    try:
        answers, sheet_words, warnings, source = load_answers_for(data_dir, session, args.sheet)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"读取答题纸失败：{exc}")

    result = compute_result(session, answers, threshold=threshold)
    result["warnings"].extend(warnings)
    overrides: dict[str, str] = {}
    for raw in args.override:
        if "=" not in raw:
            raise SystemExit(f"--override 格式应为 N=correct|wrong，收到：{raw}")
        key, value = raw.split("=", 1)
        overrides[key.strip()] = value.strip()
    if overrides:
        result["warnings"].extend(resolve_overrides(result, overrides))

    heard = effective_heard(session)
    session_words = {str(w.get("n")): w.get("word", "") for w in (session.get("words") or [])[:heard]}
    for n, sheet_word in sheet_words.items():
        expected_word = session_words.get(str(n))
        if expected_word and sheet_word.lower() != str(expected_word).lower():
            result["warnings"].append(
                f"第 {n} 题答题纸写的是 {sheet_word}，本轮实际是 {expected_word}；按题号对应检查。"
            )
    result["sheet_path"] = source

    if args.json and not args.apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    summary = result["summary"]
    print(f"场次：{session['id']}  {session.get('source_name', '')}  种子 {session.get('seed')}")
    print(
        f"本轮 {summary['total']} 题：正确 {summary['correct']}，错误 {summary['wrong']}，"
        f"未作答 {summary['blank']}，待确认 {summary['uncertain']}；"
        f"正确率 {round(float(summary['accuracy']) * 100, 1)}%"
    )
    if result["warnings"]:
        print("\n提示：")
        for warning in result["warnings"]:
            print(f"  - {warning}")
    print("\n需要关注的题目：" if not args.all else "\n全部题目：")
    print_items(result["items"], show_all=args.all)

    if not args.apply:
        print("未写入进度。确认无误后加 --apply；示例：")
        print(f"  python tools/check.py --session {session['id']} --apply")
        return 0

    if blank_count(result) > 0 and not args.allow_blank:
        raise SystemExit(
            f"有 {blank_count(result)} 题未作答。确认要按错题处理后，加 --allow-blank 再执行。"
        )
    uncertain = summary["uncertain"]
    if uncertain > 0 and args.accept_uncertain is None:
        raise SystemExit(
            f"还有 {uncertain} 题待确认。复核后用 --accept-uncertain=wrong 或 "
            "--accept-uncertain=correct（也可用 --override N=correct）再执行。"
        )
    if uncertain > 0:
        finalize_verdicts(result, uncertain_as=args.accept_uncertain or "wrong")
        result["summary"] = result["summary"]

    total_words = WordRepository(data_dir).total_count()
    applied = apply_result(data_dir, session, result, total_words)
    summary = applied["summary"]
    print("\n已写入检查记录。")
    print(
        f"结果：正确 {summary['correct']}，错误 {summary['wrong']}，未作答 {summary['blank']}，"
        f"待确认 {summary['uncertain']}；正确率 {round(float(summary['accuracy']) * 100, 1)}%"
    )
    before = applied.get("book_counts_before") or {}
    after = applied.get("book_counts_after") or {}
    print(
        f"新词本：{before.get('new')} → {after.get('new')}；"
        f"错题本：{before.get('wrong')} → {after.get('wrong')}；"
        f"已掌握：{before.get('mastered')} → {after.get('mastered')}"
    )
    if applied.get("newly_mastered"):
        print("本轮新掌握：" + "、".join(applied["newly_mastered"]))
    print("检查记录：" + str(Path(args.data_dir) / "checks" / f"{session['id']}.json"))
    print("报告：" + str(applied.get("report_path", "")))
    if args.json:
        print(json.dumps(applied, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
