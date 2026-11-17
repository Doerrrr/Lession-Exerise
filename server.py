#!/usr/bin/env python3
"""Local HTTP server for the dictation app.

Run:
    python server.py --open
The server binds to 127.0.0.1 only.  All data lives under ./data.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dictation import __version__, ai, wordbooks  # noqa: E402
from dictation.checking import (  # noqa: E402
    apply_result,
    book_counts,
    build_progress,
    pending_sessions,
    report_markdown,
    session_summary,
)
from dictation.config import (  # noqa: E402
    DEFAULT_SOURCE,
    SOURCE_BY_ID,
    is_valid_session_id,
    new_session_id,
    normalize_word_key,
    now_iso,
    random_seed,
)
from dictation.sampling import sample_words, take_pool  # noqa: E402
from dictation.sheet import build_sheet, effective_heard  # noqa: E402
from dictation.store import (  # noqa: E402
    archive_learning,
    ensure_dirs,
    list_sessions,
    load_answers,
    load_check,
    load_session,
    load_settings,
    reset_word,
    save_answers,
    save_session,
    save_settings,
)
from dictation.words import WordRepository, public_word, review_word  # noqa: E402

MAX_BODY = 4 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class App:
    def __init__(self, data_dir: Path, web_dir: Path):
        self.data_dir = data_dir.resolve()
        self.web_dir = web_dir.resolve()
        ensure_dirs(self.data_dir)
        self.words = WordRepository(self.data_dir)
        self.lock = threading.Lock()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        return load_settings(self.data_dir)

    def progress(self, exclude_session_id: str | None = None):
        return build_progress(self.data_dir, exclude_session_id=exclude_session_id)

    def counts(self, progress=None) -> dict[str, Any]:
        progress = progress or self.progress()
        return book_counts(progress, self.words.catalog_total_count())

    def public_session(self, session: dict[str, Any]) -> dict[str, Any]:
        data = {
            "id": session["id"],
            "created_at": session.get("created_at"),
            "source": session.get("source"),
            "source_name": session.get("source_name"),
            "level": session.get("level"),
            "book": session.get("book"),
            "seed": session.get("seed"),
            "count": session.get("count"),
            "heard_count": session.get("heard_count") or 0,
            "effective_heard": effective_heard(session),
            "pool_requested": session.get("pool_requested"),
            "pool_available": session.get("pool_available"),
            "pool_size": session.get("pool_size"),
            "order": session.get("order"),
            "mode": session.get("mode"),
            "interval": session.get("interval"),
            "repeat": session.get("repeat"),
            "threshold": session.get("threshold"),
            "words": [public_word(w, int(w.get("n") or i + 1)) for i, w in enumerate(session.get("words") or [])],
        }
        return data

    def state(self) -> dict[str, Any]:
        progress = self.progress()
        counts = self.counts(progress)
        settings = self.settings()
        sessions = list_sessions(self.data_dir)
        recent = [session_summary(self.data_dir, s) for s in sessions[:8]]
        return {
            "version": __version__,
            "server_time": now_iso(),
            "meta": self.words.meta,
            "word_counts": self.words.level_counts(),
            "stats": counts,
            "settings": settings,
            "sources": self.words.source_counts(progress),
            "recent_sessions": recent,
            "pending": pending_sessions(self.data_dir),
            "ai": ai.ai_status(self.data_dir),
            "wordbooks": self.words.custom_book_summaries(progress),
        }

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_id = str(payload.get("source") or self.settings().get("default_source") or DEFAULT_SOURCE)
        source = self.words.source_info(source_id)
        if source is None:
            raise ApiError(400, f"未知词库来源：{source_id}")
        try:
            pool_requested = int(payload.get("pool") or 0)
            count_requested = int(payload.get("count") or 50)
            seed = int(payload.get("seed") or 0)
            interval = int(payload.get("interval") or 6)
            repeat = int(payload.get("repeat") or 1)
        except (TypeError, ValueError):
            raise ApiError(400, "数字参数格式不正确")
        order = str(payload.get("order") or "random")
        mode = str(payload.get("mode") or "auto")
        if order not in {"random", "frequency", "alpha"}:
            raise ApiError(400, "order 只能是 random / frequency / alpha")
        if mode not in {"auto", "manual"}:
            raise ApiError(400, "mode 只能是 auto / manual")
        if not 1 <= count_requested <= 500:
            raise ApiError(400, "听写数量需在 1–500 之间")
        if not 0 <= pool_requested <= 100000:
            raise ApiError(400, "候选池数量需在 0–100000 之间（0 表示全部）")
        if seed < 0 or seed > 999_999_999:
            raise ApiError(400, "种子需在 1–999999999 之间")
        interval = max(1, min(30, interval))
        repeat = 2 if repeat == 2 else 1

        progress = self.progress()
        pool_all = self.words.source_pool(source_id, progress)
        if not pool_all:
            raise ApiError(409, "当前来源没有可听的词。新词本可能已清空；可改用全部词库复习，或在已掌握里重置单词。")
        candidate = take_pool(pool_all, pool_requested)
        actual_count = min(count_requested, len(candidate))
        if actual_count < 1:
            raise ApiError(409, "候选池为空，无法开始。")
        if seed == 0:
            seed = random_seed()
        selected = sample_words(candidate, actual_count, seed, order=order)
        settings = self.settings()
        session = {
            "id": new_session_id(),
            "created_at": now_iso(),
            "app_version": __version__,
            "source": source_id,
            "source_name": source["name"],
            "level": source["level"],
            "book": source["book"],
            "seed": seed,
            "order": order,
            "mode": mode,
            "interval": interval,
            "repeat": repeat,
            "threshold": int(settings.get("threshold") or 5),
            "pool_requested": pool_requested,
            "pool_available": len(pool_all),
            "pool_size": len(candidate),
            "count_requested": count_requested,
            "count": actual_count,
            "heard_count": 0,
            "words": selected,
        }
        warnings: list[str] = []
        if actual_count < count_requested:
            warnings.append(
                f"候选池只有 {len(candidate)} 个词，本轮实际听 {actual_count} 个（原计划 {count_requested} 个）。"
            )
        if pool_requested and len(pool_all) < pool_requested:
            warnings.append(f"当前词库只有 {len(pool_all)} 个可用词，已按实际数量取池。")
        with self.lock:
            save_session(self.data_dir, session)
        return {
            "session": self.public_session(session),
            "warnings": warnings,
            "generated_seed": seed,
        }

    def session_detail(self, session_id: str) -> dict[str, Any]:
        session = load_session(self.data_dir, session_id)
        if not session:
            raise ApiError(404, "找不到这个场次")
        detail = self.public_session(session)
        answers = load_answers(self.data_dir, session_id)
        detail["answers"] = answers["answers"] if answers else {}
        detail["has_answers"] = bool(answers)
        detail["status"] = session_summary(self.data_dir, session)["status"]
        check = load_check(self.data_dir, session_id)
        detail["has_check"] = bool(check)
        if check:
            detail["check_summary"] = check.get("summary")
            detail["report_available"] = True
        return detail

    def update_heard(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = load_session(self.data_dir, session_id)
        if not session:
            raise ApiError(404, "找不到这个场次")
        try:
            heard = int(payload.get("heard_count") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "heard_count 必须是数字")
        heard = max(0, min(heard, int(session.get("count") or 0)))
        session["heard_count"] = max(int(session.get("heard_count") or 0), heard)
        with self.lock:
            save_session(self.data_dir, session)
        return {"heard_count": session["heard_count"], "effective_heard": effective_heard(session)}

    def save_answers(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = load_session(self.data_dir, session_id)
        if not session:
            raise ApiError(404, "找不到这个场次")
        if load_check(self.data_dir, session_id):
            raise ApiError(409, "这一轮已经检查过了，不能修改答案。需要重查请让 AI 重新检查。")
        raw = payload.get("answers")
        if not isinstance(raw, dict):
            raise ApiError(400, "answers 必须是 {题号: 答案} 的对象")
        answers: dict[str, str] = {}
        for key, value in raw.items():
            key_text = str(key)
            if not key_text.isdigit():
                continue
            n = int(key_text)
            if n < 1 or n > int(session.get("count") or 0):
                continue
            text = str(value or "").strip()
            if len(text) > 500:
                text = text[:500]
            answers[str(n)] = text
        with self.lock:
            paths = save_answers(self.data_dir, session, answers)
        return {
            "answers": answers,
            "paths": paths,
            "command": f"检查 {session_id}",
            "heard_count": session.get("heard_count"),
        }

    def sheet(self, session_id: str, filled: bool) -> tuple[str, str]:
        session = load_session(self.data_dir, session_id)
        if not session:
            raise ApiError(404, "找不到这个场次")
        answers = {}
        if filled:
            loaded = load_answers(self.data_dir, session_id)
            answers = loaded["answers"] if loaded else {}
        text = build_sheet(session, answers=answers or None)
        return text, f"dictation-sheet-{session_id}.md"

    def report(self, session_id: str) -> dict[str, Any]:
        session = load_session(self.data_dir, session_id)
        if not session:
            raise ApiError(404, "找不到这个场次")
        check = load_check(self.data_dir, session_id)
        if not check:
            raise ApiError(404, "这一轮还没有检查记录")
        return {
            "check": check,
            "session": self.public_session(session),
            "markdown": report_markdown(session, check),
        }

    # ------------------------------------------------------------------
    # AI grading
    # ------------------------------------------------------------------
    def ai_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            ai.save_ai_settings(
                self.data_dir,
                api_key=payload.get("api_key"),
                model=payload.get("model"),
                base_url=payload.get("base_url"),
                clear=bool(payload.get("clear")),
            )
        except ValueError as exc:
            raise ApiError(400, str(exc))
        return {"ok": True, "ai": ai.ai_status(self.data_dir)}

    def ai_test(self) -> dict[str, Any]:
        try:
            result = ai.test_connection(self.data_dir)
        except ai.AiError as exc:
            raise ApiError(502, str(exc))
        return result

    def ai_check(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = load_session(self.data_dir, session_id)
        if not session:
            raise ApiError(404, "找不到这个场次")
        if load_check(self.data_dir, session_id):
            raise ApiError(409, "这一轮已经检查过了。需要重新判卷，请让 AI 助手复核或先重置本轮记录。")
        raw = payload.get("answers")
        if isinstance(raw, dict):
            # Reuse the normal answer validation/save path.
            self.save_answers(session_id, {"answers": raw})
        loaded = load_answers(self.data_dir, session_id)
        answers = loaded["answers"] if loaded else {}
        if not any(str(value).strip() for value in answers.values()):
            raise ApiError(400, "还没有填写答案，请先填写或批量粘贴。")
        try:
            result = ai.grade_session(
                self.data_dir,
                session,
                answers,
                threshold=int(self.settings().get("threshold") or 5),
            )
        except ai.AiError as exc:
            raise ApiError(502, str(exc))
        with self.lock:
            applied = apply_result(self.data_dir, session, result, self.words.catalog_total_count())
        detail = self.session_detail(session_id)
        return {
            "check": applied,
            "session": detail,
            "markdown": report_markdown(session, applied),
            "ai": ai.ai_status(self.data_dir),
        }

    # ------------------------------------------------------------------
    # custom wordbooks
    # ------------------------------------------------------------------
    def wordbook_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get("content")
        if not isinstance(content, str):
            raise ApiError(400, "缺少文件内容（content）")
        filename = str(payload.get("filename") or "")
        name = payload.get("name")
        try:
            parsed = wordbooks.parse_import(
                content,
                filename=filename,
                name_override=str(name) if name else None,
            )
            book = wordbooks.save_book(self.data_dir, parsed)
        except wordbooks.WordbookError as exc:
            raise ApiError(400, str(exc))
        self.words.reload_custom()
        progress = self.progress()
        return {
            "ok": True,
            "book": wordbooks.summarize_book(book, progress),
            "wordbooks": self.words.custom_book_summaries(progress),
        }

    def wordbook_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        book_id = str(payload.get("id") or "")
        try:
            deleted = wordbooks.delete_book(self.data_dir, book_id)
        except wordbooks.WordbookError as exc:
            raise ApiError(400, str(exc))
        self.words.reload_custom()
        progress = self.progress()
        return {
            "ok": True,
            "deleted": deleted,
            "wordbooks": self.words.custom_book_summaries(progress),
        }

    def wordbook_template(self, fmt: str) -> tuple[str, str, str]:
        if str(fmt).lower() == "csv":
            return wordbooks.template_csv(), "wordbook-template.csv", "text/csv; charset=utf-8"
        return wordbooks.template_json(), "wordbook-template.json", "application/json; charset=utf-8"

    # ------------------------------------------------------------------
    # books / history
    # ------------------------------------------------------------------
    def books(self, query: dict[str, list[str]]) -> dict[str, Any]:
        book = (query.get("book") or ["new"])[0]
        level = (query.get("level") or ["all"])[0]
        sort = (query.get("sort") or ["frq"])[0]
        q = (query.get("q") or [""])[0].strip().lower()
        try:
            page = max(1, int((query.get("page") or ["1"])[0]))
            page_size = int((query.get("page_size") or ["50"])[0])
        except ValueError:
            raise ApiError(400, "分页参数格式不正确")
        page_size = max(10, min(200, page_size))
        if book not in {"new", "wrong", "mastered", "all"}:
            raise ApiError(400, "book 只能是 new / wrong / mastered / all")
        if level not in self.words.catalog_levels():
            raise ApiError(400, "level 参数不正确")
        progress = self.progress()
        base = self.words.catalog_words(level)
        filtered = []
        for word in base:
            state = progress.state(word["word"])
            mastered = bool(state and state.mastered)
            wrong_count = state.wrong_count if state else 0
            if book == "new" and mastered:
                continue
            if book == "wrong" and (mastered or wrong_count <= 0):
                continue
            if book == "mastered" and not mastered:
                continue
            if q and q not in word["word"].lower() and q not in word["translation"].lower():
                continue
            filtered.append(word)
        if sort == "alpha":
            filtered.sort(key=lambda w: w["word"].lower())
        elif sort == "wrong":
            filtered.sort(key=lambda w: (-(progress.state(w["word"]).wrong_count if progress.state(w["word"]) else 0), w["word"].lower()))
        elif sort == "streak":
            filtered.sort(key=lambda w: (-(progress.state(w["word"]).streak if progress.state(w["word"]) else 0), w["word"].lower()))
        else:
            filtered.sort(key=lambda w: (
                w["frq"] if w["frq"] > 0 else 10**9,
                w["bnc"] if w["bnc"] > 0 else 10**9,
                w["word"].lower(),
            ))
        total = len(filtered)
        start = (page - 1) * page_size
        items = [review_word(w, progress.state(w["word"])) for w in filtered[start:start + page_size]]
        return {
            "book": book,
            "level": level,
            "sort": sort,
            "q": q,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
            "counts": self.counts(progress),
        }

    def history(self, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        sessions = list_sessions(self.data_dir)[:limit]
        return {"items": [session_summary(self.data_dir, s) for s in sessions]}

    def export_notebook(self, query: dict[str, list[str]]) -> tuple[str, str]:
        book = (query.get("book") or ["wrong"])[0]
        level = (query.get("level") or ["all"])[0]
        if book not in {"new", "wrong", "mastered", "all"}:
            raise ApiError(400, "book 参数不正确")
        if level not in self.words.catalog_levels():
            raise ApiError(400, "level 参数不正确")
        progress = self.progress()
        rows = []
        for word in self.words.catalog_words(level):
            state = progress.state(word["word"])
            mastered = bool(state and state.mastered)
            wrong = state.wrong_count if state else 0
            if book == "new" and mastered:
                continue
            if book == "wrong" and (mastered or wrong <= 0):
                continue
            if book == "mastered" and not mastered:
                continue
            rows.append((word, state))
        title = {"new": "新词本", "wrong": "错题本", "mastered": "已掌握", "all": "全部词"}[book]
        lines = [f"# {title}", "", f"共 {len(rows)} 个词。", "", "| 单词 | 释义 | 连对 | 错次 |", "|---|---|---|---|"]
        for word, state in rows:
            translation = word["translation"].replace("\n", "；").replace("|", "\\|")
            lines.append(
                f"| {word['word']} | {translation} | {state.streak if state else 0} | {state.wrong_count if state else 0} |"
            )
        return "\n".join(lines) + "\n", f"dictation-{book}.md"

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------
    def reset(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = str(payload.get("scope") or "")
        if scope == "all":
            with self.lock:
                archived = archive_learning(self.data_dir)
            return {"ok": True, "archived": str(archived) if archived else None}
        if scope == "word":
            word = normalize_word_key(str(payload.get("word") or ""))
            if not word or not self.words.get(word):
                raise ApiError(400, "找不到这个词")
            with self.lock:
                reset_word(self.data_dir, word)
            return {"ok": True, "word": word, "counts": self.counts()}
        raise ApiError(400, "scope 只能是 all 或 word")

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "threshold" not in payload:
            raise ApiError(400, "缺少 threshold")
        try:
            threshold = int(payload["threshold"])
        except (TypeError, ValueError):
            raise ApiError(400, "threshold 必须是数字")
        if not 1 <= threshold <= 20:
            raise ApiError(400, "掌握阈值需在 1–20 之间")
        settings = save_settings(self.data_dir, {"threshold": threshold})
        return {"ok": True, "settings": settings, "counts": self.counts()}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Dictation/{__version__}"
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        # ------------------------------------------------------------------
        def _dispatch(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if path.startswith("/api/"):
                    self._api(method, path, query)
                elif method == "GET":
                    self._static(path)
                else:
                    raise ApiError(404, "Not found")
            except ApiError as exc:
                self._json(exc.status, {"error": exc.message})
            except BrokenPipeError:
                pass
            except ConnectionResetError:
                pass
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._json(500, {"error": f"服务器内部错误：{exc}"})

        def _api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
            if path == "/api/health" and method == "GET":
                self._json(200, {"ok": True, "version": __version__})
                return
            if path == "/api/state" and method == "GET":
                self._json(200, app.state())
                return
            if path == "/api/sessions" and method == "POST":
                self._json(201, app.create_session(self._body()))
                return
            if path == "/api/sessions" and method == "GET":
                try:
                    limit = int((query.get("limit") or ["50"])[0] or 50)
                except ValueError:
                    raise ApiError(400, "limit 参数不正确")
                self._json(200, app.history(limit))
                return
            if path == "/api/books" and method == "GET":
                self._json(200, app.books(query))
                return
            if path == "/api/history" and method == "GET":
                try:
                    limit = int((query.get("limit") or ["50"])[0] or 50)
                except ValueError:
                    raise ApiError(400, "limit 参数不正确")
                self._json(200, app.history(limit))
                return
            if path == "/api/export/notebook" and method == "GET":
                text, filename = app.export_notebook(query)
                self._text(200, text, "text/markdown; charset=utf-8",
                           extra={"Content-Disposition": f'attachment; filename="{filename}"'})
                return
            if path == "/api/reset" and method == "POST":
                self._json(200, app.reset(self._body()))
                return
            if path == "/api/settings" and method == "PUT":
                self._json(200, app.update_settings(self._body()))
                return
            if path == "/api/ai/settings" and method == "PUT":
                self._json(200, app.ai_settings(self._body()))
                return
            if path == "/api/ai/test" and method == "POST":
                self._json(200, app.ai_test())
                return
            if path == "/api/wordbooks/import" and method == "POST":
                self._json(200, app.wordbook_import(self._body()))
                return
            if path == "/api/wordbooks/delete" and method == "POST":
                self._json(200, app.wordbook_delete(self._body()))
                return
            if path == "/api/wordbooks/template" and method == "GET":
                fmt = (query.get("format") or ["json"])[0]
                text, filename, content_type = app.wordbook_template(fmt)
                self._text(200, text, content_type,
                           extra={"Content-Disposition": f'attachment; filename="{filename}"'})
                return

            prefix = "/api/sessions/"
            if path.startswith(prefix):
                rest = path[len(prefix):].strip("/")
                parts = rest.split("/")
                session_id = parts[0]
                if not is_valid_session_id(session_id):
                    raise ApiError(400, "场次 ID 格式不正确")
                sub = parts[1] if len(parts) > 1 else None
                if sub is None and method == "GET":
                    self._json(200, app.session_detail(session_id))
                    return
                if sub == "sheet" and method == "GET":
                    filled = (query.get("filled") or ["0"])[0] in {"1", "true", "yes"}
                    text, filename = app.sheet(session_id, filled=filled)
                    self._text(200, text, "text/markdown; charset=utf-8",
                               extra={"Content-Disposition": f'attachment; filename="{filename}"'})
                    return
                if sub == "report" and method == "GET":
                    self._json(200, app.report(session_id))
                    return
                if sub == "heard" and method == "POST":
                    self._json(200, app.update_heard(session_id, self._body()))
                    return
                if sub == "answers" and method == "POST":
                    self._json(200, app.save_answers(session_id, self._body()))
                    return
                if sub == "ai-check" and method == "POST":
                    self._json(200, app.ai_check(session_id, self._body()))
                    return
            raise ApiError(404, "接口不存在")

        # ------------------------------------------------------------------
        def _static(self, path: str) -> None:
            if path in {"", "/"}:
                file_path = app.web_dir / "index.html"
            else:
                name = path.lstrip("/")
                if "/" in name or "\\" in name or name.startswith("."):
                    raise ApiError(404, "Not found")
                file_path = app.web_dir / name
            if not file_path.is_file():
                raise ApiError(404, "Not found")
            mime, _ = mimetypes.guess_type(str(file_path))
            mime = mime or "application/octet-stream"
            if mime.startswith("text/") or mime in {"application/javascript", "application/json", "image/svg+xml"}:
                mime += "; charset=utf-8"
            self._bytes(200, file_path.read_bytes(), mime)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY:
                raise ApiError(413, "请求体太大")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError(400, "请求体不是合法 JSON")
            if not isinstance(data, dict):
                raise ApiError(400, "请求体必须是 JSON 对象")
            return data

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._bytes(status, body, "application/json; charset=utf-8")

        def _text(self, status: int, text: str, content_type: str, *, extra: dict[str, str] | None = None) -> None:
            self._bytes(status, text.encode("utf-8"), content_type, extra=extra)

        def _bytes(self, status: int, body: bytes, content_type: str, *, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return Handler


def find_port(host: str, start: int, attempts: int = 12) -> int:
    for port in range(start, start + attempts):
        try:
            probe = ThreadingHTTPServer((host, port), BaseHTTPRequestHandler)
        except OSError:
            continue
        else:
            probe.server_close()
            return port
    raise SystemExit(f"端口 {start}–{start + attempts - 1} 都被占用，请用 --port 指定其他端口")


def main() -> int:
    parser = argparse.ArgumentParser(description="单词听写本地服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--web-dir", default=str(ROOT / "web"))
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--no-open", action="store_true", help="不要打开浏览器")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    web_dir = Path(args.web_dir).resolve()
    if not (web_dir / "index.html").exists():
        print(f"找不到前端文件：{web_dir / 'index.html'}", file=sys.stderr)
        return 1
    try:
        app = App(data_dir, web_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    port = find_port(args.host, args.port)
    server = ThreadingHTTPServer((args.host, port), make_handler(app))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"听写本 running at {url}")
    print(f"数据目录: {data_dir}")
    print("Ctrl+C 停止服务")
    if args.open and not args.no_open:
        def opener() -> None:
            time.sleep(0.5)
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=opener, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止中…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
