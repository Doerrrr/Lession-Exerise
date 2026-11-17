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

from dictation import wordbooks  # noqa: E402
from dictation.progress import Progress, WordState  # noqa: E402
from dictation.store import ensure_dirs  # noqa: E402
from dictation.words import WordRepository  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "words.json"


def make_data() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name)
    ensure_dirs(data)
    shutil.copy(FIXTURE, data / "words.json")
    return tmp, data


def make_book_content() -> str:
    return json.dumps({
        "format": "dictation-wordbook",
        "version": 1,
        "name": "测试词本",
        "description": "单元测试",
        "words": [
            {"word": "xyzzy", "translation": "测试词一", "phonetic": "ˈzɪzi", "frequency": 10},
            {"word": "plugh", "translation": "测试词二", "frequency": 20},
            {"word": "frobnitz", "translation": "测试词三", "frequency": 30},
        ],
    }, ensure_ascii=False)


class ParseTests(unittest.TestCase):
    def test_json(self):
        parsed = wordbooks.parse_import(make_book_content(), "demo.json")
        self.assertEqual(parsed["name"], "测试词本")
        self.assertEqual(len(parsed["words"]), 3)
        self.assertEqual(parsed["words"][0]["frq"], 10)
        self.assertEqual(parsed["words"][0]["tags"], ["custom"])

    def test_csv(self):
        content = (
            "word,translation,phonetic,frequency\n"
            'abandon,"放弃, 抛弃",ə\'bændən,1000\n'
            "ability,能力；才干,,800\n"
        )
        parsed = wordbooks.parse_import(content, "my words.csv", "覆盖名称")
        self.assertEqual(parsed["name"], "覆盖名称")
        self.assertEqual(len(parsed["words"]), 2)
        self.assertIn("放弃", parsed["words"][0]["translation"])
        self.assertEqual(parsed["words"][1]["frq"], 800)

    def test_dedupe_last_wins(self):
        content = json.dumps({
            "format": wordbooks.FORMAT,
            "version": 1,
            "name": "dup",
            "words": [
                {"word": "apple", "translation": "苹果"},
                {"word": "Apple", "translation": "苹果；苹果树"},
            ],
        }, ensure_ascii=False)
        parsed = wordbooks.parse_import(content)
        self.assertEqual(len(parsed["words"]), 1)
        self.assertIn("苹果树", parsed["words"][0]["translation"])

    def test_invalid(self):
        cases = [
            "{}",
            json.dumps({"format": "wrong", "version": 1, "name": "x", "words": []}),
            json.dumps({"format": wordbooks.FORMAT, "version": 2, "name": "x", "words": []}),
            json.dumps({"format": wordbooks.FORMAT, "version": 1, "name": "x", "words": []}),
            json.dumps({"format": wordbooks.FORMAT, "version": 1, "name": "x",
                        "words": [{"word": "two words", "translation": "短语"}]}),
            json.dumps({"format": wordbooks.FORMAT, "version": 1, "name": "x",
                        "words": [{"word": "ok", "translation": ""}]}),
            "word,translation\n",
        ]
        for content in cases:
            with self.subTest(content=content[:40]):
                with self.assertRaises(wordbooks.WordbookError):
                    wordbooks.parse_import(content)


class StorageTests(unittest.TestCase):
    def test_save_load_delete(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        parsed = wordbooks.parse_import(make_book_content(), "demo.json")
        book = wordbooks.save_book(data, parsed)
        self.assertTrue(wordbooks.BOOK_ID_RE.match(book["id"]))
        books = wordbooks.load_books(data)
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["name"], "测试词本")
        summary = wordbooks.summarize_book(book)
        self.assertEqual(summary["count"], 3)
        self.assertTrue(wordbooks.delete_book(data, book["id"]))
        self.assertFalse(wordbooks.delete_book(data, book["id"]))
        with self.assertRaises(wordbooks.WordbookError):
            wordbooks.delete_book(data, "../../x")


class RepositoryTests(unittest.TestCase):
    def test_custom_source_and_catalog(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        book = wordbooks.save_book(data, wordbooks.parse_import(make_book_content()))
        repo = WordRepository(data)
        self.assertEqual(len(repo.custom_books), 1)
        self.assertEqual(repo.catalog_total_count(), 10)  # 7 fixture + 3 custom
        self.assertIn("xyzzy", [w["word"] for w in repo.all_words()])
        self.assertEqual(len(repo.catalog_words("custom:" + book["id"])), 3)
        self.assertIn("custom:" + book["id"], repo.catalog_levels())
        info = repo.source_info("custom_new:" + book["id"])
        self.assertIsNotNone(info)
        self.assertEqual(info["book"], "custom")
        self.assertEqual(len(repo.source_pool("custom_new:" + book["id"])), 3)
        self.assertEqual(len(repo.source_pool("custom_all:" + book["id"])), 3)
        self.assertIsNotNone(repo.get("xyzzy"))
        sources = repo.source_counts()
        custom_sources = [s for s in sources if s["book"] == "custom"]
        self.assertEqual(len(custom_sources), 2)
        summaries = repo.custom_book_summaries()
        self.assertEqual(summaries[0]["count"], 3)

    def test_custom_words_in_wrongbook(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        book = wordbooks.save_book(data, wordbooks.parse_import(make_book_content()))
        repo = WordRepository(data)
        progress = Progress(threshold=5)
        progress.states["xyzzy"] = WordState(word="xyzzy", wrong_count=1, total_seen=1)
        wrong = repo.source_pool("wrong_all", progress)
        self.assertIn("xyzzy", [w["word"] for w in wrong])
        counts = {s["id"]: s["count"] for s in repo.source_counts(progress)}
        self.assertEqual(counts["wrong_all"], 1)
        self.assertEqual(counts["custom_new:" + book["id"]], 3)

    def test_delete_reload(self):
        tmp, data = make_data()
        self.addCleanup(tmp.cleanup)
        book = wordbooks.save_book(data, wordbooks.parse_import(make_book_content()))
        repo = WordRepository(data)
        self.assertTrue(wordbooks.delete_book(data, book["id"]))
        repo.reload_custom()
        self.assertEqual(repo.custom_books, [])
        self.assertEqual(repo.catalog_total_count(), 7)
        self.assertIsNone(repo.source_info("custom_new:" + book["id"]))


if __name__ == "__main__":
    unittest.main()
