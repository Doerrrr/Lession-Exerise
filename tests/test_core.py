from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dictation.checking import (  # noqa: E402
    apply_result,
    book_counts,
    build_progress,
    has_check,
    summarize_items,
)
from dictation.progress import derive  # noqa: E402
from dictation.sampling import sample_words, sort_by_frequency, take_pool  # noqa: E402
from dictation.sheet import build_sheet, parse_sheet  # noqa: E402
from dictation.store import (  # noqa: E402
    ensure_dirs,
    load_check,
    load_resets,
    reset_word,
    save_session,
)
from dictation.words import WordRepository  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "words.json"


def fixture_words() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["words"]


def make_temp_data() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name)
    ensure_dirs(data)
    shutil.copy(FIXTURE, data / "words.json")
    return tmp, data


def event(created_at: str, *pairs: tuple[str, str]) -> dict:
    return {
        "session_id": "20260101-000000-abcd",
        "created_at": created_at,
        "items": [{"word": word, "verdict": verdict} for word, verdict in pairs],
    }


class SamplingTests(unittest.TestCase):
    def test_frequency_order_and_top_n(self):
        words = fixture_words()
        ordered = sort_by_frequency(words)
        self.assertEqual(ordered[0]["word"], "abandon")
        self.assertEqual([w["word"] for w in take_pool(words, 3)], ["abandon", "ability", "apple"])

    def test_same_seed_same_selection(self):
        words = fixture_words()
        pool = take_pool(words, 7)
        a = sample_words(pool, 5, seed=12345)
        b = sample_words(pool, 5, seed=12345)
        self.assertEqual([w["word"] for w in a], [w["word"] for w in b])
        self.assertEqual([w["n"] for w in a], [1, 2, 3, 4, 5])
        c = sample_words(pool, 5, seed=99999)
        self.assertNotEqual([w["word"] for w in a], [w["word"] for w in c])

    def test_order_frequency(self):
        words = fixture_words()
        selected = sample_words(take_pool(words, 7), 7, seed=7, order="frequency")
        ranks = [w["frq"] for w in selected]
        self.assertEqual(ranks, sorted(ranks))

    def test_count_cannot_exceed_pool(self):
        with self.assertRaises(ValueError):
            sample_words(fixture_words(), 99, seed=1)


def manual_result(session: dict, verdicts: dict[str, str], threshold: int = 5) -> dict:
    items = []
    for word in session.get("words") or []:
        n = str(word.get("n"))
        verdict = verdicts.get(n, "wrong")
        items.append({
            "n": word.get("n"),
            "word": word.get("word", ""),
            "phonetic": word.get("phonetic", ""),
            "answer": "",
            "verdict": verdict,
            "score": 1.0 if verdict == "correct" else (0.5 if verdict == "uncertain" else 0.0),
            "matched": "",
            "reason": "test",
            "expected": word.get("translation", ""),
        })
    return {
        "session_id": session["id"],
        "session_created_at": session.get("created_at"),
        "seed": session.get("seed"),
        "source_name": session.get("source_name"),
        "threshold": threshold,
        "heard": len(items),
        "items": items,
        "summary": summarize_items(items),
        "warnings": [],
        "engine": "deepseek",
        "model": "test",
    }


class ProgressTests(unittest.TestCase):
    def test_five_correct_master(self):
        events = [
            event(f"2026-01-0{i + 1}T10:00:00+08:00", ("abandon", "correct"))
            for i in range(5)
        ]
        progress = derive(events, threshold=5)
        state = progress.state("abandon")
        self.assertIsNotNone(state)
        self.assertEqual(state.streak, 5)
        self.assertTrue(state.mastered)
        self.assertTrue(progress.is_mastered("abandon"))

    def test_wrong_resets_streak(self):
        events = [
            event("2026-01-01T10:00:00+08:00", ("abandon", "correct")),
            event("2026-01-02T10:00:00+08:00", ("abandon", "correct")),
            event("2026-01-03T10:00:00+08:00", ("abandon", "wrong")),
            event("2026-01-04T10:00:00+08:00", ("abandon", "correct")),
        ]
        progress = derive(events, threshold=5)
        state = progress.state("abandon")
        self.assertEqual(state.streak, 1)
        self.assertEqual(state.wrong_count, 1)
        self.assertFalse(state.mastered)
        self.assertTrue(progress.is_wrong("abandon"))

    def test_wrongbook_is_subset_of_new(self):
        events = [event("2026-01-01T10:00:00+08:00", ("apple", "wrong"), ("banana", "correct"))]
        progress = derive(events, threshold=5)
        self.assertTrue(progress.is_wrong("apple"))
        self.assertFalse(progress.is_mastered("apple"))
        self.assertFalse(progress.is_wrong("banana"))

    def test_reset_ignores_older_events(self):
        events = [
            event(created_at, ("apple", "correct"))
            for created_at in (
                "2026-01-01T10:00:00+08:00",
                "2026-01-02T10:00:00+08:00",
                "2026-01-03T10:00:00+08:00",
                "2026-01-04T10:00:00+08:00",
                "2026-01-05T10:00:00+08:00",
            )
        ]
        resets = {"apple": "2026-01-06T00:00:00+08:00"}
        progress = derive(events, 5, resets)
        self.assertFalse(progress.is_mastered("apple"))

    def test_threshold_change(self):
        events = [event("2026-01-01T10:00:00+08:00", ("apple", "correct"))] * 3
        self.assertFalse(derive(events, 5).is_mastered("apple"))
        self.assertTrue(derive(events, 3).is_mastered("apple"))


class SheetTests(unittest.TestCase):
    def session(self) -> dict:
        words = fixture_words()[:3]
        for i, word in enumerate(words, 1):
            word["n"] = i
        return {
            "id": "20260101-120000-abcd",
            "created_at": "2026-01-01T12:00:00+08:00",
            "source_name": "四级·新词本",
            "seed": 123456,
            "count": 3,
            "heard_count": 2,
            "pool_size": 3,
            "words": words,
        }

    def test_build_and_parse(self):
        session = self.session()
        text = build_sheet(session, answers={"1": "放弃"})
        self.assertIn("20260101-120000-abcd", text)
        self.assertIn("1. abandon", text)
        self.assertIn("你的答案：放弃", text)
        self.assertNotIn("3. apple", text)  # heard_count limits the sheet
        parsed = parse_sheet(text)
        self.assertEqual(parsed.session_id, "20260101-120000-abcd")
        self.assertEqual(parsed.seed, 123456)
        self.assertEqual(parsed.answers["1"], "放弃")
        self.assertEqual(parsed.words["1"], "abandon")
        self.assertEqual(parsed.answers["2"], "")

    def test_parse_hand_filled(self):
        text = """
# 单词听写答题纸
- 场次：20260101-120000-abcd
- 种子：123456

1. abandon 放弃
2. ability
   你的答案：能力
   才干
3. apple
   苹果
"""
        parsed = parse_sheet(text)
        self.assertEqual(parsed.answers["1"], "放弃")
        self.assertEqual(parsed.answers["2"], "能力；才干")
        self.assertEqual(parsed.answers["3"], "苹果")


class ApplyTests(unittest.TestCase):
    def test_apply_is_idempotent(self):
        tmp, data = make_temp_data()
        self.addCleanup(tmp.cleanup)
        repo = WordRepository(data)
        words = []
        for i, word in enumerate(fixture_words(), 1):
            item = dict(word)
            item["n"] = i
            words.append(item)
        session = {
            "id": "20260101-120000-abcd",
            "created_at": "2026-01-01T12:00:00+08:00",
            "source_name": "四级·新词本",
            "seed": 1,
            "count": len(words),
            "heard_count": len(words),
            "words": words,
        }
        save_session(data, session)
        result = manual_result(session, {"1": "correct"}, threshold=1)
        applied = apply_result(data, session, result, repo.total_count())
        self.assertTrue(has_check(data, session["id"]))
        self.assertEqual(applied["summary"]["correct"], 1)
        self.assertEqual(applied["summary"]["wrong"], len(words) - 1)
        self.assertIn("abandon", applied["newly_mastered"])

        progress = build_progress(data, threshold=1)
        self.assertEqual(progress.state("abandon").total_seen, 1)
        self.assertEqual(progress.state("abandon").wrong_count, 0)
        self.assertEqual(progress.state("ability").wrong_count, 1)
        counts = book_counts(progress, repo.total_count())
        self.assertEqual(counts["wrong"], len(words) - 1)

        # Re-check with everything correct: the old result is replaced.
        result2 = manual_result(
            session,
            {str(i): "correct" for i in range(1, len(words) + 1)},
            threshold=1,
        )
        apply_result(data, session, result2, repo.total_count())
        progress2 = build_progress(data, threshold=1)
        self.assertEqual(progress2.state("abandon").total_seen, 1)
        self.assertEqual(progress2.state("abandon").streak, 1)
        self.assertEqual(len(load_check(data, session["id"])["items"]), len(words))

    def test_word_reset(self):
        tmp, data = make_temp_data()
        self.addCleanup(tmp.cleanup)
        reset_word(data, "apple")
        resets = load_resets(data)
        self.assertIn("apple", resets)


class StorageTests(unittest.TestCase):
    def test_session_roundtrip(self):
        tmp, data = make_temp_data()
        self.addCleanup(tmp.cleanup)
        session = {
            "id": "20260101-120000-abcd",
            "created_at": "2026-01-01T12:00:00+08:00",
            "count": 2,
            "heard_count": 2,
            "words": [],
        }
        save_session(data, session)
        from dictation.store import load_session
        loaded = load_session(data, session["id"])
        self.assertEqual(loaded["id"], session["id"])


if __name__ == "__main__":
    unittest.main()
