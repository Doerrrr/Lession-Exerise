from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dictation import ai  # noqa: E402
from dictation.store import ensure_dirs  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "words.json"


def make_data() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name)
    ensure_dirs(data)
    shutil.copy(FIXTURE, data / "words.json")
    return tmp, data


def make_session() -> dict:
    words = json.loads(FIXTURE.read_text(encoding="utf-8"))["words"][:3]
    for index, word in enumerate(words, 1):
        word["n"] = index
    return {
        "id": "20260101-120000-abcd",
        "created_at": "2026-01-01T12:00:00+08:00",
        "source_name": "测试词库",
        "seed": 123,
        "count": 3,
        "heard_count": 3,
        "words": words,
    }


class AiSettingsTests(unittest.TestCase):
    def test_settings_roundtrip_and_mask(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        status = ai.ai_status(data)
        self.assertFalse(status["configured"])
        self.assertEqual(status["model"], "deepseek-flash")

        saved = ai.save_ai_settings(
            data,
            api_key="sk-1234567890",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/",
        )
        self.assertEqual(saved["base_url"], "https://api.deepseek.com")
        status = ai.ai_status(data)
        self.assertTrue(status["configured"])
        self.assertNotIn("sk-1234567890", json.dumps(status))
        self.assertEqual(status["masked_key"], "sk-1…7890")

        secrets = json.loads((data / "secrets.json").read_text(encoding="utf-8"))
        self.assertEqual(secrets["deepseek_api_key"], "sk-1234567890")

        ai.save_ai_settings(data, clear=True)
        status = ai.ai_status(data)
        self.assertFalse(status["configured"])
        secrets = json.loads((data / "secrets.json").read_text(encoding="utf-8"))
        self.assertNotIn("deepseek_api_key", secrets)

    def test_invalid_base_url(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ValueError):
            ai.save_ai_settings(data, base_url="api.deepseek.com")


class AiParseTests(unittest.TestCase):
    def test_parse_object_and_aliases(self):
        content = json.dumps({
            "results": [
                {"n": 1, "verdict": "correct", "matched": "放弃", "reason": "ok"},
                {"n": "2", "verdict": "错误", "reason": "不对"},
                {"n": 3, "verdict": "未作答"},
            ]
        }, ensure_ascii=False)
        parsed = ai.parse_ai_results(content)
        self.assertEqual(parsed["1"]["verdict"], "correct")
        self.assertEqual(parsed["2"]["verdict"], "wrong")
        self.assertEqual(parsed["3"]["verdict"], "blank")

    def test_parse_fenced_list(self):
        content = "```json\n[{\"n\": 1, \"verdict\": \"uncertain\"}]\n```"
        parsed = ai.parse_ai_results(content)
        self.assertEqual(parsed["1"]["verdict"], "uncertain")

    def test_parse_bad(self):
        with self.assertRaises(ai.AiError):
            ai.parse_ai_results("not json")
        with self.assertRaises(ai.AiError):
            ai.parse_ai_results('{"results": []}')


class AiGradeTests(unittest.TestCase):
    def test_build_request_contains_reference_and_answer(self):
        session = make_session()
        messages, items = ai.build_grade_request(session, {"1": "舍弃", "2": "能力"})
        self.assertEqual(len(messages), 2)
        self.assertIn("JSON", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(len(payload["items"]), 1)
        n, word, reference, answer = payload["items"][0]
        self.assertEqual((n, word, answer), (1, "abandon", "舍弃"))
        self.assertIn("放弃", reference)
        self.assertEqual(len(items), 3)

    def test_grade_with_fake_api(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        ai.save_ai_settings(data, api_key="sk-test")
        session = make_session()
        answers = {"1": "放弃", "2": "水果", "3": ""}
        fake = '{"results":[[2,"W","意思不对"]]}'
        with patch("dictation.ai.call_chat", return_value=fake) as mocked:
            result = ai.grade_session(data, session, answers)
        mocked.assert_called_once()
        self.assertEqual(result["engine"], "deepseek")
        self.assertEqual(result["model"], "deepseek-flash")
        self.assertEqual(result["summary"]["correct"], 1)
        self.assertEqual(result["summary"]["wrong"], 1)
        self.assertEqual(result["summary"]["blank"], 1)
        self.assertEqual(result["items"][0]["engine"], "local")
        self.assertEqual(result["items"][1]["reason"], "意思不对")
        self.assertIn("放弃", result["items"][0]["expected"])

    def test_missing_ai_item_raises_instead_of_local_grading(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        ai.save_ai_settings(data, api_key="sk-test")
        session = make_session()
        answers = {"1": "放弃", "2": "能力", "3": "香蕉"}
        fake = json.dumps({"results": [{"n": 1, "verdict": "correct"}]})
        with patch("dictation.ai.call_chat", return_value=fake):
            with self.assertRaises(ai.AiError):
                ai.grade_session(data, session, answers)

    def test_no_key_raises(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(ai.AiError):
            ai.grade_session(data, make_session(), {"1": "舍弃"})


if __name__ == "__main__":
    unittest.main()
