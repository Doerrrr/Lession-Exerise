"""Optional DeepSeek (OpenAI-compatible) AI grading for a dictation session.

The API key lives only in ``data/secrets.json`` on this machine and is never
returned to the browser. Only answers that cannot be graded locally are sent
to the configured endpoint, with their word and reference meaning.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from .checking import summarize_items
from .config import now_iso
from .sheet import effective_heard
from .store import list_checks, load_settings, read_json, write_json

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
SECRETS_FILE = "secrets.json"
# Bump when the grading criteria change so old model decisions are rechecked.
GRADING_RULES_VERSION = 1

SYSTEM_PROMPT = """批改英语单词听写的中文释义。输入 JSON 的 items 每行是 [题号, 单词, 参考释义, 学生答案]。
忽略释义里的词性、领域标签和编号。答对任一义项即可，接受同义词、口语、附加解释及不改变原意的错漏字。只有字面相似、核心义项错误或改变词义的错别字判错。空白或仅有标点判未作答。无法确定时判待确认。以给定释义为准。
只返回 JSON，每个输入题号恰好一条结果，题号原样保留。
- 正确：[题号,"C","命中义项"]，同义或错漏字需要说明时加第四列简短理由。
- 错误、待确认：[题号,"W"或"U","20字内的具体理由"]。
- 未作答：[题号,"B"]。
例如 {"results":[[1,"C","放弃"],[2,"W","把能力答成了水果"],[3,"B"],[4,"U","答案含义不明确"]]}。"""

SENSE_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,20}")
PART_OF_SPEECH = re.compile(
    r"^(?:(?:n|v|vt|vi|a|adj|adv|prep|pron|conj|interj|int|num|art|aux|abbr)\.\s*)+",
    re.IGNORECASE,
)
COMPACT_VERDICTS = {"C": "correct", "W": "wrong", "B": "blank", "U": "uncertain"}

VERDICT_ALIASES = {    "correct": "correct", "正确": "correct", "对": "correct", "right": "correct",
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
    base_url = (
        str(raw.get("deepseek_base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
        or DEFAULT_BASE_URL
    )
    model = str(raw.get("deepseek_model") or "").strip()
    if not model:
        model = DEFAULT_MODEL if urlsplit(base_url).hostname == "api.deepseek.com" else "deepseek-chat"
    elif model == "deepseek-chat" and urlsplit(base_url).hostname == "api.deepseek.com":
        model = DEFAULT_MODEL
    return {
        "api_key": str(raw.get("deepseek_api_key") or "").strip(),
        "model": model,
        "base_url": base_url,
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
    usage: dict[str, Any] | None = None,
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
    if payload["model"] == DEFAULT_MODEL and urlsplit(base_url).hostname == "api.deepseek.com":
        payload["thinking"] = {"type": "disabled"}
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
    if usage is not None and isinstance(data.get("usage"), dict):
        usage.update(data["usage"])
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

def _short_reference(text: str, limit: int = 220) -> str:
    """Keep only the leading senses; ECDICT tails are huge and rarely needed."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    for separator in ("；", ";", "，", ",", "。", " "):
        pos = cut.rfind(separator)
        if pos >= 80:
            cut = cut[:pos]
            break
    return cut.strip()


def local_verdict(reference: str, answer: str) -> str | None:
    """Decide blanks and exact Chinese senses listed in the reference."""
    if not answer or all(
        char.isspace() or unicodedata.category(char).startswith("P")
        for char in answer
    ):
        return "blank"
    if not SENSE_PATTERN.fullmatch(answer):
        return None

    for line in str(reference or "").splitlines():
        line = line.strip()
        # Dictionary lines marked with a part of speech also use commas as
        # sense separators. Free-form definitions can contain prose commas.
        separators = r"[,，;；]" if PART_OF_SPEECH.match(line) else r"[;；]"
        for part in re.split(separators, line):
            if PART_OF_SPEECH.sub("", part.strip()).strip() == answer:
                return "correct"
    return None


def load_accepted_answers(
    data_dir: str | Path, model: str, base_url: str
) -> set[tuple[str, str, str]]:
    """Index consistent positive model decisions from this user's saved checks."""
    accepted = set()
    disputed = set()
    for check in list_checks(data_dir):
        if (
            check.get("engine") != "deepseek"
            or check.get("model_called") is not True
            or check.get("model") != model
            or check.get("base_url") != base_url
            or check.get("grading_rules_version", 1) != GRADING_RULES_VERSION
        ):
            continue
        # Before explicit versioning, the compact grader recorded model_called.
        for item in check.get("items") or []:
            if not isinstance(item, dict) or item.get("engine") != "ai":
                continue
            word = item.get("word")
            reference = item.get("expected")
            answer = item.get("answer")
            if not all(isinstance(value, str) and value.strip() for value in (word, reference, answer)):
                continue
            key = (word, reference, answer.strip())
            if item.get("verdict") == "correct":
                accepted.add(key)
            else:
                disputed.add(key)
    return accepted - disputed


def build_grade_request(
    session: dict[str, Any],
    answers: dict[str, str],
    *,
    accepted_answers: set[tuple[str, str, str]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    prompt_items = []
    for word in (session.get("words") or [])[:effective_heard(session)]:
        n = int(word.get("n") or 0)
        spelling = word.get("word", "")
        reference = word.get("translation", "")
        answer = str(answers.get(str(n), "") or "").strip()
        verdict = local_verdict(reference, answer)
        learned = (
            verdict is None and accepted_answers is not None
            and (spelling, reference, answer) in accepted_answers
        )
        items.append({
            "n": n,
            "word": spelling,
            "phonetic": word.get("phonetic", ""),
            "reference": reference,
            "answer": answer,
            "local_verdict": verdict,
            "learned": learned,
        })
        if verdict is None and not learned:
            prompt_items.append([n, spelling, _short_reference(reference), answer])

    if not prompt_items:
        return [], items
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(
            {"items": prompt_items}, ensure_ascii=False, separators=(",", ":")
        )},
    ], items


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
        if isinstance(item, list) and 2 <= len(item) <= 4:
            number, status, *details = item
            verdict = COMPACT_VERDICTS.get(str(status))
            if verdict is None or not all(isinstance(value, str) for value in details):
                continue
            detail = details[0] if details else ""
            matched = detail if verdict == "correct" else ""
            if verdict == "correct":
                reason = details[1] if len(details) == 2 else "命中义项"
            elif verdict == "blank":
                reason = "未作答"
            else:
                reason = detail
            if verdict in ("wrong", "uncertain") and not reason.strip():
                continue
        elif isinstance(item, dict):
            number = item.get("n") or item.get("index") or item.get("number")
            status = str(item.get("verdict") or item.get("result") or "").strip().lower()
            verdict = VERDICT_ALIASES.get(status, "uncertain")
            matched = str(item.get("matched") or "")
            reason = str(item.get("reason") or "")
        else:
            continue
        try:
            n = int(number)
        except (TypeError, ValueError):
            continue
        if str(n) in parsed:
            raise AiError(f"AI 判卷重复返回题号 {n}，请重试")
        parsed[str(n)] = {"verdict": verdict, "matched": matched[:200], "reason": reason[:200]}
    if not parsed:
        raise AiError("AI 判卷没有返回任何可用的题目结果")
    return parsed


def grade_session(
    data_dir: str | Path,
    session: dict[str, Any],
    answers: dict[str, str],
    *,
    threshold: int | None = None,
    history_dir: str | Path | None = None,
) -> dict[str, Any]:
    settings = load_ai_settings(data_dir)
    accepted = load_accepted_answers(
        data_dir if history_dir is None else history_dir, settings["model"], settings["base_url"]
    )
    messages, request_items = build_grade_request(session, answers, accepted_answers=accepted)
    usage: dict[str, Any] = {}
    if threshold is None:
        threshold = int(load_settings(data_dir).get("threshold", 5))
    ai_results = {}
    if messages:
        content = call_chat(
            settings["api_key"],
            settings["base_url"],
            settings["model"],
            messages,
            max_tokens=4096,
            response_format=True,
            timeout=120,
            usage=usage,
        )
        ai_results = parse_ai_results(content)

    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in request_items:
        n = str(item["n"])
        answer = item["answer"]
        local = item["local_verdict"]
        ai_result = ai_results.get(n)
        if local == "blank":
            verdict, matched, reason, engine = "blank", "", "未作答", "blank"
        elif local == "correct":
            verdict, matched, reason, engine = "correct", answer, "与参考义项完全匹配", "local"
        elif item["learned"]:
            verdict, matched, reason, engine = "correct", answer, "相同答案此前已判对", "learned"
        elif ai_result is not None:
            verdict = ai_result["verdict"]
            matched = ai_result["matched"]
            reason = ai_result["reason"]
            engine = "ai"
        else:
            missing.append(n)
            continue
        score = 1.0 if verdict == "correct" else (0.5 if verdict == "uncertain" else 0.0)
        items.append({
            "n": item["n"],
            "word": item["word"],
            "phonetic": item["phonetic"],
            "answer": answer,
            "verdict": verdict,
            "score": score,
            "matched": matched,
            "reason": reason,
            "expected": item["reference"],
            "engine": engine,
        })
    if missing:
        raise AiError(
            "AI 判卷没有返回以下题号，请重试："
            + "、".join(missing[:20])
            + ("…" if len(missing) > 20 else "")
        )
    summary = summarize_items(items)
    return {
        "session_id": session["id"],
        "session_created_at": session.get("created_at"),
        "seed": session.get("seed"),
        "source_name": session.get("source_name"),
        "threshold": int(threshold),
        "heard": effective_heard(session),
        "items": items,
        "summary": summary,
        "warnings": [],
        "engine": "deepseek",
        "model_called": bool(messages),
        "grading_rules_version": GRADING_RULES_VERSION,
        "usage": usage,
        "model": settings["model"],
        "base_url": settings["base_url"],
        "graded_at": now_iso(),
    }
