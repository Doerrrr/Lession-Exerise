from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dictation import ai  # noqa: E402
from dictation.checking import apply_result, summarize_items  # noqa: E402
from dictation.store import load_check, load_session  # noqa: E402
from dictation.words import WordRepository  # noqa: E402
from server import App, make_handler  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "words.json"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        for name in ("sessions", "checks", "archive"):
            (self.data / name).mkdir()
        shutil.copy(FIXTURE, self.data / "words.json")
        self.app = App(self.data, ROOT / "web")
        handler = make_handler(self.app)
        handler.log_message = lambda self, fmt, *args: None  # type: ignore[method-assign]
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, method, path, body=None):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            method=method,
            headers={"Content-Type": "application/json"} if payload is not None else {},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return response.status, json.loads(raw)
            return response.status, raw

    def apply_manual(self, session, verdicts, total_words=None):
        if isinstance(verdicts, str):
            verdicts = {str(w["n"]): verdicts for w in session["words"]}
        items = []
        for word in session["words"]:
            n = str(word["n"])
            verdict = verdicts.get(n, "wrong")
            items.append({
                "n": word["n"],
                "word": word["word"],
                "phonetic": word.get("phonetic", ""),
                "answer": "",
                "verdict": verdict,
                "score": 1.0 if verdict == "correct" else (0.5 if verdict == "uncertain" else 0.0),
                "matched": "",
                "reason": "test",
                "expected": word.get("translation", ""),
            })
        result = {
            "session_id": session["id"],
            "session_created_at": session.get("created_at"),
            "seed": session.get("seed"),
            "source_name": session.get("source_name"),
            "threshold": 5,
            "heard": len(items),
            "items": items,
            "summary": summarize_items(items),
            "warnings": [],
            "engine": "deepseek",
            "model": "test",
        }
        return apply_result(
            self.data,
            session,
            result,
            total_words if total_words is not None else self.app.words.catalog_total_count(),
        )

    # ------------------------------------------------------------------
    def test_state_and_static(self):
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["stats"]["total"], 7)
        self.assertEqual(state["stats"]["new"], 7)
        sources = {source["id"]: source for source in state["sources"]}
        self.assertEqual(sources["new_cet4"]["count"], 6)
        self.assertIn("new_ielts", sources)
        self.assertIn("new_cet4_listening", sources)
        self.assertIn("new_cet6_listening", sources)
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("听写本", html)

    def test_create_session_is_reproducible_and_hides_answers(self):
        body = {"source": "new_cet4", "pool": 5, "count": 3, "seed": 123456,
                "mode": "auto", "interval": 6, "repeat": 1, "order": "random"}
        status, first = self.request("POST", "/api/sessions", body)
        self.assertEqual(status, 201)
        session = first["session"]
        self.assertEqual(session["seed"], 123456)
        self.assertEqual(len(session["words"]), 3)
        self.assertEqual(session["pool_size"], 5)
        self.assertNotIn("translation", session["words"][0])
        status, second = self.request("POST", "/api/sessions", body)
        self.assertEqual(
            [w["word"] for w in session["words"]],
            [w["word"] for w in second["session"]["words"]],
        )
        # The new-word book is CET-4 only and its top-5 pool is fixed.
        self.assertTrue(all(w["word"] != "zebra" for w in session["words"]))

    def test_listening_frequency_selects_and_exports_by_transcript_count(self):
        (self.data / "listening_frequency.json").write_text(
            json.dumps({"cet4": {"words": [["zebra", 25, 8], ["banana", 9, 4]]}}),
            encoding="utf-8",
        )
        self.app.words = WordRepository(self.data)
        status, data = self.request("POST", "/api/sessions", {
            "source": "new_cet4_listening", "pool": 1, "count": 1, "seed": 5,
        })
        self.assertEqual(status, 201)
        self.assertEqual([word["word"] for word in data["session"]["words"]], ["zebra"])
        _, book = self.request("GET", "/api/books?level=cet4_listening&book=all")
        self.assertEqual(book["items"][0]["listening_frequency"], 25)
        self.assertEqual(book["items"][0]["listening_papers"], 8)
        _, markdown = self.request("GET", "/api/export/notebook?level=cet4_listening&book=all")
        self.assertIn("听力词次 | 覆盖套数", markdown)
        self.assertLess(markdown.index("zebra"), markdown.index("banana"))

    def test_count_is_capped_by_pool(self):
        status, data = self.request("POST", "/api/sessions", {
            "source": "new_cet4", "pool": 2, "count": 10, "seed": 5,
        })
        self.assertEqual(status, 201)
        self.assertEqual(len(data["session"]["words"]), 2)
        self.assertTrue(data["warnings"])

    def test_heard_answers_report_and_books(self):
        status, created = self.request("POST", "/api/sessions", {
            "source": "new_all", "pool": 7, "count": 7, "seed": 99,
        })
        session_id = created["session"]["id"]
        self.request("POST", f"/api/sessions/{session_id}/heard", {"heard_count": 7})
        status, detail = self.request("GET", f"/api/sessions/{session_id}")
        self.assertEqual(detail["heard_count"], 7)
        self.assertFalse(detail["has_answers"])
        self.assertFalse(detail["has_check"])

        answers = {
            "abandon": "放弃", "ability": "能力", "apple": "苹果",
            "banana": "香蕉", "car": "汽车", "dog": "狗", "zebra": "斑马",
        }
        by_number = {str(w["n"]): answers[w["word"]] for w in detail["words"]}
        status, saved = self.request("POST", f"/api/sessions/{session_id}/answers", {"answers": by_number})
        self.assertEqual(status, 200)
        self.assertIn("markdown", saved["paths"])
        status, detail = self.request("GET", f"/api/sessions/{session_id}")
        self.assertTrue(detail["has_answers"])
        self.assertEqual(len(detail["answers"]), 7)

        session = load_session(self.data, session_id)
        self.apply_manual(session, "correct")
        status, report = self.request("GET", f"/api/sessions/{session_id}/report")
        self.assertEqual(status, 200)
        self.assertEqual(report["check"]["summary"]["correct"], 7)
        self.assertTrue(report["markdown"])

        status, wrongbook = self.request("GET", "/api/books?book=wrong")
        self.assertEqual(wrongbook["total"], 0)
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["stats"]["wrong"], 0)
        self.assertEqual(state["recent_sessions"][0]["status"], "checked")

    def test_threshold_setting(self):
        status, data = self.request("PUT", "/api/settings", {"threshold": 3})
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["threshold"], 3)
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["settings"]["threshold"], 3)
        self.assertEqual(state["stats"]["threshold"], 3)

    def test_ai_settings_and_connection(self):
        status, data = self.request("PUT", "/api/ai/settings", {
            "api_key": "sk-test-123456",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["ai"]["configured"])
        self.assertNotIn("sk-test-123456", json.dumps(data))
        self.assertEqual(data["ai"]["masked_key"], "sk-t…3456")
        status, state = self.request("GET", "/api/state")
        self.assertTrue(state["ai"]["configured"])
        with patch("dictation.ai.call_chat", return_value="连接正常"):
            status, data = self.request("POST", "/api/ai/test", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["reply"], "连接正常")

    def test_ai_check_with_fake_api(self):
        ai.save_ai_settings(self.data, api_key="sk-test")
        status, created = self.request("POST", "/api/sessions", {
            "source": "new_all", "pool": 7, "count": 2, "seed": 321,
        })
        session_id = created["session"]["id"]
        self.request("POST", f"/api/sessions/{session_id}/heard", {"heard_count": 2})
        fake = json.dumps({
            "results": [
                {"n": 1, "verdict": "correct", "matched": "命中", "reason": "ok"},
                {"n": 2, "verdict": "wrong", "matched": "", "reason": "不对"},
            ]
        }, ensure_ascii=False)
        with patch("dictation.ai.call_chat", return_value=fake):
            status, data = self.request(
                "POST",
                f"/api/sessions/{session_id}/ai-check",
                {"answers": {"1": "x", "2": "y"}},
            )
        self.assertEqual(status, 200)
        self.assertEqual(data["check"]["engine"], "deepseek")
        self.assertEqual(data["check"]["summary"]["correct"], 1)
        self.assertEqual(data["check"]["summary"]["wrong"], 1)
        self.assertTrue(data["session"]["has_check"])
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["stats"]["wrong"], 1)
        status, wrongbook = self.request("GET", "/api/books?book=wrong")
        self.assertEqual(wrongbook["total"], 1)

    def test_retry_wrong_session(self):
        status, created = self.request("POST", "/api/sessions", {
            "source": "new_all", "pool": 7, "count": 3, "seed": 11,
        })
        session_id = created["session"]["id"]
        words = created["session"]["words"]
        self.request("POST", f"/api/sessions/{session_id}/heard", {"heard_count": 3})
        answers = {str(word["n"]): "完全错误的答案" for word in words}
        self.request("POST", f"/api/sessions/{session_id}/answers", {"answers": answers})
        loaded = load_session(self.data, session_id)
        self.apply_manual(loaded, "wrong")

        status, data = self.request(
            "POST",
            f"/api/sessions/{session_id}/retry-wrong",
            {"mode": "manual", "interval": 4, "repeat": 1},
        )
        self.assertEqual(status, 201)
        retry = data["session"]
        self.assertEqual(data["wrong_count"], 3)
        self.assertEqual(retry["count"], 3)
        self.assertEqual(retry["source"], "retry_wrong")
        self.assertIn("本轮错题", retry["source_name"])
        self.assertEqual(retry["mode"], "manual")
        self.assertEqual([w["word"] for w in retry["words"]], [w["word"] for w in words])
        self.assertNotIn("translation", retry["words"][0])
        saved = load_session(self.data, retry["id"])
        self.assertIsNotNone(saved)
        self.assertEqual(len(saved["words"]), 3)
        self.assertTrue(all(w.get("translation") for w in saved["words"]))

    def test_wordbook_import_and_custom_source(self):
        content = json.dumps({
            "format": "dictation-wordbook",
            "version": 1,
            "name": "测试自定义词本",
            "words": [
                {"word": "xyzzy", "translation": "测试词一", "frequency": 10},
                {"word": "plugh", "translation": "测试词二", "frequency": 20},
                {"word": "frobnitz", "translation": "测试词三", "frequency": 30},
            ],
        }, ensure_ascii=False)
        status, imported = self.request("POST", "/api/wordbooks/import", {
            "filename": "custom.json",
            "content": content,
        })
        self.assertEqual(status, 200)
        book = imported["book"]
        self.assertEqual(book["count"], 3)
        status, state = self.request("GET", "/api/state")
        self.assertEqual(len(state["wordbooks"]), 1)
        self.assertEqual(state["stats"]["total"], 10)  # 7 fixture + 3 custom
        custom_sources = [s for s in state["sources"] if s["book"] == "custom"]
        self.assertEqual(len(custom_sources), 2)
        source_id = f"custom_new:{book['id']}"
        self.assertIn(source_id, [s["id"] for s in custom_sources])

        status, created = self.request("POST", "/api/sessions", {
            "source": source_id, "pool": 3, "count": 2, "seed": 42,
        })
        self.assertEqual(status, 201)
        session = created["session"]
        self.assertIn("自定义", session["source_name"])
        words = session["words"]
        self.assertTrue(all(w["word"] in {"xyzzy", "plugh", "frobnitz"} for w in words))
        self.assertEqual(len(words), 2)

        self.request("POST", f"/api/sessions/{session['id']}/heard", {"heard_count": 2})
        answers = {str(w["n"]): "错误答案" for w in words}
        self.request("POST", f"/api/sessions/{session['id']}/answers", {"answers": answers})
        loaded = load_session(self.data, session["id"])
        self.apply_manual(loaded, "wrong")
        status, state = self.request("GET", "/api/state")
        wrong_source = next(s for s in state["sources"] if s["id"] == "wrong_all")
        self.assertEqual(wrong_source["count"], 2)
        status, wrongbook = self.request("GET", "/api/books?book=wrong&level=all")
        self.assertEqual(wrongbook["total"], 2)
        self.assertTrue(all(item["tags"] == ["custom"] for item in wrongbook["items"]))

        status, deleted = self.request("POST", "/api/wordbooks/delete", {"id": book["id"]})
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["wordbooks"], [])
        self.assertFalse([s for s in state["sources"] if s["book"] == "custom"])

    def test_five_correct_sessions_master_words(self):
        answers = {
            "abandon": "放弃", "ability": "能力", "apple": "苹果",
            "banana": "香蕉", "car": "汽车", "dog": "狗", "zebra": "斑马",
        }
        for index in range(5):
            status, created = self.request("POST", "/api/sessions", {
                "source": "new_all", "pool": 7, "count": 7, "seed": 1000 + index,
            })
            self.assertEqual(status, 201)
            session_id = created["session"]["id"]
            self.request("POST", f"/api/sessions/{session_id}/heard", {"heard_count": 7})
            session = load_session(self.data, session_id)
            by_number = {str(w["n"]): answers[w["word"]] for w in session["words"]}
            self.request("POST", f"/api/sessions/{session_id}/answers", {"answers": by_number})
            self.apply_manual(session, "correct")
            status, state = self.request("GET", "/api/state")
            if index == 3:
                self.assertEqual(state["stats"]["mastered"], 0)
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["stats"]["mastered"], 7)
        self.assertEqual(state["stats"]["new"], 0)
        new_all = next(s for s in state["sources"] if s["id"] == "new_all")
        self.assertEqual(new_all["count"], 0)

    def test_word_reset_and_full_reset(self):
        status, created = self.request("POST", "/api/sessions", {
            "source": "new_all", "pool": 7, "count": 7, "seed": 7,
        })
        session_id = created["session"]["id"]
        self.request("POST", f"/api/sessions/{session_id}/heard", {"heard_count": 7})
        session = load_session(self.data, session_id)
        answers = {str(w["n"]): "不对" for w in session["words"]}
        self.request("POST", f"/api/sessions/{session_id}/answers", {"answers": answers})
        self.apply_manual(session, "wrong")
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["stats"]["wrong"], 7)

        status, data = self.request("POST", "/api/reset", {"scope": "word", "word": "abandon"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["stats"]["wrong"], 6)

        status, data = self.request("POST", "/api/reset", {"scope": "all"})
        self.assertEqual(status, 200)
        status, state = self.request("GET", "/api/state")
        self.assertEqual(state["stats"]["wrong"], 0)
        self.assertEqual(state["stats"]["mastered"], 0)
        self.assertFalse(load_check(self.data, session_id))


if __name__ == "__main__":
    unittest.main()
