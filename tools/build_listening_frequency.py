#!/usr/bin/env python3
"""Count word forms in local CET-4 and CET-6 listening transcripts."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {
    "cet4": {
        "url": "https://github.com/yiyisheh/CET4-vocabulary",
        "revision": "554387c984718cbc504171ad65d694e8c7a6a870",
        "pattern": r"cet4_\d{4}_\d{2}_\d+_ans\.txt",
    },
    "cet6": {
        "url": "https://github.com/Ysoseri1224/CET-6-Listening-10-Years-Resources-and-Analysis",
        "revision": "3dc33904a2c3c5b3eff56b685595e51461ab6332",
        "pattern": r"cet6_\d{4}\.\d{2}(?:_set\d+)?_transcript\.md",
    },
}
WORD = re.compile(r"[a-z]+(?:['-][a-z]+)*")
HEADING = re.compile(
    r"(?:part\s+[ivx]+\s+listening comprehension|section\s+[abc]|"
    r"(?:news report|conversation|passage|recording)\s+(?:[1-9]|one|two|three))"
    r"\s*[:.。]?$", re.I,
)
DIRECTION = re.compile(r"^(?:questions?\s+\d.*\bbased on\b|directions\s*[:：])", re.I)
SPEAKER = re.compile(r"^[MW]\s*[:：]\s*", re.I)


def tokens(transcript: str) -> tuple[str, ...]:
    words = []
    for line in transcript.splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or HEADING.fullmatch(line) or DIRECTION.match(line):
            continue
        text = SPEAKER.sub("", line).replace("’", "'").lower()
        words.extend(WORD.findall(text))
    return tuple(words)


def count_band(folder: Path, band: str, vocabulary: set[str]) -> dict:
    counts, papers = Counter(), Counter()
    seen: list[Counter[str]] = []
    duplicates = []
    filenames = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or not re.fullmatch(SOURCES[band]["pattern"], path.name, re.I):
            continue
        words = tokens(path.read_text(encoding="utf-8-sig"))
        if len(words) < 1000:
            continue
        frequencies = Counter(words)
        # Some source files are alternate copies of the same script.
        if any(sum((frequencies & old).values()) >= 0.95 * sum((frequencies | old).values())
               for old in seen):
            duplicates.append(path.name)
            continue
        seen.append(frequencies)
        filenames.append(path.name)
        counts.update(frequencies)
        papers.update(frequencies.keys())
    if not filenames:
        raise ValueError(f"No complete {band} transcripts found in {folder}")
    eligible = sorted(
        (word for word in vocabulary if papers[word] >= 2),
        key=lambda word: (-counts[word], -papers[word], word),
    )
    return {
        "source": SOURCES[band]["url"],
        "revision": SOURCES[band]["revision"],
        "papers": filenames,
        "duplicates": duplicates,
        "words": [[word, counts[word], papers[word]] for word in eligible],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cet4", required=True, type=Path)
    parser.add_argument("--cet6", required=True, type=Path)
    parser.add_argument("--words", type=Path, default=ROOT / "data" / "words.json")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "listening_frequency.json")
    args = parser.parse_args()
    catalog = json.loads(args.words.read_text(encoding="utf-8"))
    vocabulary = {entry["word"].lower() for entry in catalog["words"]}
    data = {band: count_band(folder, band, vocabulary)
            for band, folder in (("cet4", args.cet4), ("cet6", args.cet6))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    for band, table in data.items():
        print(f"{band}: {len(table['papers'])} papers, {len(table['words'])} words")


if __name__ == "__main__":
    main()
