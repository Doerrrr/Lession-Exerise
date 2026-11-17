"""Optional DeepSeek (OpenAI-compatible) AI grading for a dictation session.

The API key lives only in ``data/secrets.json`` on this machine and is never
returned to the browser.  The word list, reference meanings and the user's
answers are sent to the configured endpoint when the user clicks "AI 判卷".
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .checking import summarize_items
from .config import now_iso
from .matching import match_answer
from .sheet import effective_heard
from .store import load_settings, read_json, write_json

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
SECRETS_FILE = "secrets.json"

SYSTEM_PROMPT = """你是一位严格、公平的英语单词听写批改老师。
学生会听到英文单词，然后写出中文意思。输入是一个 JSON，包含本轮每一题的题号 n、英文单词 word、参考释义 reference 和学生的答案 answer。
请逐题判断：
1. 学生答案命中 reference 中任意一个中文义项，或公认的同义表达，判 correct。
2. 有错别字、漏字，但意思正确，判 correct。
3. 答案为空，判 blank。
4. 意思错误，判 wrong。
5. 实在无法判断，判 uncertain，并在 reason 说明。
只返回一个 JSON 对象，不要 markdown 代码块，不要额外文字。格式：
{"results":[{"n":1,"verdict":"correct","matched":"命中的义项","reason":"不超过20字"}]}
results 必须覆盖输入的每一题；verdict 只能是 correct、wrong、blank、uncertain。"""

VERDICT_ALIASES = {
    "correct": "correct", "正确": "correct", "对": "correct", "right": "correct",
    "wrong": "wrong", "错误": "wrong", "错": "wrong", "incorrect": "wrong",
    "blank": "blank", "空": "blank", "未作答": "blank", "空白": "blank",
    "uncertain": "uncertain", "不确定": "uncertain", "待确认": "uncertain",
}


class AiError(Exception):
    """Raised for configuration, network or response-shape problems."""


# ---------------------------------------------------------------------------
# settings / secrets
# ---------------------------------------------------------------------------

def secrets_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / SECRETS_FILE


def load_ai_settings(data_dir: str | Path) -> dict[str, str]:
    raw = read_json(secrets_path(data_dir), {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "api_key": str(raw.get("deepseek_api_key") or "").strip(),
        "model": str(raw.get("deepseek_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "base_url": (str(raw.get("deepseek_base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
                     or DEFAULT_BASE_URL),
    }


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}…{key[-4:]}"


def save_ai_settings(
    data_dir: str | Path,
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    clear: bool = False,
) -> dict[str, str]:
    current = load_ai_settings(data_dir)
    if clear:
        current["api_key"] = ""
    elif api_key is not None and str(api_key).strip():
        current["api_key"] = str(api_key).strip()
    if model is not None and str(model).strip():
        current["model"] = str(model).strip()
    if base_url is not None and str(base_url).strip():
        candidate = str(base_url).strip().rstrip("/")
        if not re.match(r"^https?://", candidate, re.IGNORECASE):
            raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
        current["base_url"] = candidate
    payload = {
        "deepseek_model": current["model"],
        "deepseek_base_url": current["base_url"],
    }
    if current["api_key"]:
        payload["deepseek_api_key"] = current["api_key"]
    write_json(secrets_path(data_dir), payload)
    return current


def ai_status(data_dir: str | Path) -> dict[str, Any]:
    settings = load_ai_settings(data_dir)
    return {
        "configured": bool(settings["api_key"]),
        "model": settings["model"],
        "base_url": settings["base_url"],
        "masked_key": mask_key(settings["api_key"]),
    }


# ---------------------------------------------------------------------------
# HTTP call
# ---------------------------------------------------------------------------

def _endpoint(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def call_chat(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 4096,
    temperature: float = 0,
    response_format: bool = True,
    timeout: int = 120,
) -> str:
    if not api_key:
        raise AiError("还没有配置 DeepSeek API Key，请先在设置里填写。")
    payload: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if response_format:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        _endpoint(base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "dictation-app/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        message = detail
        try:
            data = json.loads(detail)
            message = (data.get("error") or {}).get("message") or detail
        except (ValueError, AttributeError):
            pass
        raise AiError(f"DeepSeek API 返回 {exc.code}：{str(message)[:500]}") from exc
    except urllib.error.URLError as exc:
        raise AiError(f"连接 DeepSeek 失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise AiError("DeepSeek API 超时，请稍后重试或减少本轮题数") from exc
    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AiError("DeepSeek 返回格式无法解析") from exc
    if not isinstance(content, str) or not content.strip():
        raise AiError("DeepSeek 返回内容为空")
    return content


def test_connection(data_dir: str | Path) -> dict[str, Any]:
    settings = load_ai_settings(data_dir)
    content = call_chat(
        settings["api_key"],
        settings["base_url"],
        settings["model"],
        [{"role": "user", "content": "只回复四个字：连接正常"}],
        max_tokens=16,
        response_format=False,
        timeout=30,
    )
    return {
        "ok": True,
        "model": settings["model"],
        "base_url": settings["base_url"],
        "reply": content.strip()[:120],
    }


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------

def build_grade_request(
    session: dict[str, Any],
    answers: dict[str, str],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    heard = effective_heard(session)
    items: list[dict[str, Any]] = []
    for word in (session.get("words") or [])[:heard]:
        n = str(word.get("n", ""))
        items.append({
            "n": int(word.get("n") or 0),
            "word": word.get("word", ""),
            "reference": word.get("translation", ""),
            "answer": str(answers.get(n, "") or "").strip(),
        })
    user_content = json.dumps(
        {"source": session.get("source_name", ""), "items": items},
        ensure_ascii=False,
    )
    return (
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        items,
    )


def parse_ai_results(content: str) -> dict[str, dict[str, str]]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    candidates = [text]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
    data: Any = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            break
        except (ValueError, TypeError):
            continue
    if data is None:
        raise AiError("AI 判卷返回的不是合法 JSON")
    if isinstance(data, list):
        results = data
    elif isinstance(data, dict):
        results = data.get("results") or data.get("items") or data.get("data")
        if isinstance(results, dict):
            results = list(results.values())
    else:
        results = None
    if not isinstance(results, list):
        raise AiError("AI 判卷 JSON 缺少 results 数组")
    parsed: dict[str, dict[str, str]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n") or item.get("index") or item.get("number"))
        except (TypeError, ValueError):
            continue
        verdict = str(item.get("verdict") or item.get("result") or "").strip().lower()
        verdict = VERDICT_ALIASES.get(verdict, "uncertain")
        parsed[str(n)] = {
            "verdict": verdict,
            "matched": str(item.get("matched") or "")[:200],
            "reason": str(item.get("reason") or "")[:200],
        }
    if not parsed:
        raise AiError("AI 判卷没有返回任何可用的题目结果")
    return parsed


def grade_session(
    data_dir: str | Path,
    session: dict[str, Any],
    answers: dict[str, str],
    *,
    threshold: int | None = None,
) -> dict[str, Any]:
    settings = load_ai_settings(data_dir)
    if not settings["api_key"]:
        raise AiError("还没有配置 DeepSeek API Key，请先在设置里填写。")
    messages, request_items = build_grade_request(session, answers)
    content = call_chat(
        settings["api_key"],
        settings["base_url"],
        settings["model"],
        messages,
        max_tokens=4096,
        response_format=True,
        timeout=120,
    )
    ai_results = parse_ai_results(content)
    if threshold is None:
        threshold = int(load_settings(data_dir).get("threshold", 5))
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in request_items:
        n = str(item["n"])
        answer = str(item.get("answer") or "").strip()
        ai = ai_results.get(n)
        if not answer:
            verdict, matched, reason = "blank", "", (ai or {}).get("reason", "")
        elif ai is not None:
            verdict = ai["verdict"]
            matched = ai["matched"]
            reason = ai["reason"]
        else:
            missing.append(n)
            local = match_answer(answer, str(item.get("reference") or ""))
            verdict, matched, reason = local.verdict, local.matched, "AI 未返回本题，使用本地匹配"
        score = 1.0 if verdict == "correct" else (0.5 if verdict == "uncertain" else 0.0)
        items.append({
            "n": item["n"],
            "word": item["word"],
            "phonetic": "",
            "answer": answer,
            "verdict": verdict,
            "score": score,
            "matched": matched,
            "reason": reason,
            "expected": item["reference"],
            "engine": "ai" if ai is not None and answer else "local",
        })
    # Fill phonetics from the session word records (request items don't carry
    # them to keep the prompt compact).
    phonetic_by_n = {str(w.get("n")): w.get("phonetic", "") for w in session.get("words") or []}
    for item in items:
        item["phonetic"] = phonetic_by_n.get(str(item["n"]), "")
    summary = summarize_items(items)
    warnings: list[str] = []
    if missing:
        warnings.append(
            "AI 没有返回以下题号，已改用本地匹配：" + "、".join(missing[:20])
        )
    return {
        "session_id": session["id"],
        "session_created_at": session.get("created_at"),
        "seed": session.get("seed"),
        "source_name": session.get("source_name"),
        "threshold": int(threshold),
        "heard": effective_heard(session),
        "items": items,
        "summary": summary,
        "warnings": warnings,
        "engine": "deepseek",
        "model": settings["model"],
        "base_url": settings["base_url"],
        "graded_at": now_iso(),
    }
