#!/usr/bin/env python3
"""Build data/words.json from the ECDICT CSV export.

Usage:
    python tools/build_words.py                # download and build
    python tools/build_words.py --csv path.csv # build from a local CSV
    python tools/build_words.py --keep-csv     # keep the downloaded CSV

Only CET-4 / CET-6 / IELTS tagged single words are kept.  The resulting JSON is the
word source used by the dictation app; runtime never needs network access.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "words.json"
CACHE_DIR = ROOT / "data" / ".cache"
# Pinned so the generated word list is reproducible even if master changes.
ECDICT_REF = "39cfb9672c0906b63569e0453c2e6abfb9ab182a"
ECDICT_URL = f"https://raw.githubusercontent.com/skywind3000/ECDICT/{ECDICT_REF}/ecdict.csv"
ECDICT_HOME = "https://github.com/skywind3000/ECDICT"
LICENSE = "MIT"

WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-]*$")
LITERAL_NEWLINE_RE = re.compile(r"\\+n")


def normalize_translation(raw: str) -> str:
    """ECDICT stores line breaks as the literal characters ``\\n``."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = LITERAL_NEWLINE_RE.sub("\n", text)
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def to_int(value: str) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return 0


def download_csv(dest: Path, *, keep: bool, url: str = ECDICT_URL) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading ECDICT ({url}) ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "dictation-app/1.0"})
    tmp = dest.with_suffix(".part")
    start = time.time()
    with urllib.request.urlopen(req, timeout=180) as src, tmp.open("wb") as out:
        expected = int(src.headers.get("Content-Length") or 0)
        total = 0
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
            if total % (10 * 1024 * 1024) < 1024 * 1024:
                print(f"  {total / 1024 / 1024:.0f} MB", flush=True)
    if expected and total != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"short download: got {total} bytes, expected {expected}")
    tmp.replace(dest)
    print(f"Downloaded {dest.stat().st_size / 1024 / 1024:.1f} MB in {time.time() - start:.1f}s")
    if not keep:
        # Keep the file this run; caller removes it when finished.
        pass
    return dest


def build(csv_path: Path) -> dict:
    merged: dict[str, dict] = {}
    rows = 0
    kept = 0
    rejected_phrase = 0
    rejected_translation = 0
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"word", "translation", "tag"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"Unexpected CSV header, missing: {sorted(missing)}")
        for row in reader:
            rows += 1
            tags = set((row.get("tag") or "").split())
            if not tags & {"cet4", "cet6", "ielts"}:
                continue
            word = (row.get("word") or "").strip()
            if not WORD_RE.match(word):
                rejected_phrase += 1
                continue
            translation = normalize_translation(row.get("translation") or "")
            if not translation:
                rejected_translation += 1
                continue
            key = word.lower()
            frq = to_int(row.get("frq"))
            bnc = to_int(row.get("bnc"))
            entry = {
                "word": word,
                "phonetic": (row.get("phonetic") or "").strip(),
                "translation": translation,
                "frq": frq,
                "bnc": bnc,
                "tags": sorted(tags & {"cet4", "cet6", "ielts"}),
                "_translation_len": len(translation),
            }
            old = merged.get(key)
            if old is None:
                merged[key] = entry
                kept += 1
                continue
            # Merge duplicates: union tags, prefer the richer translation,
            # keep the smallest (best) frequency rank.
            old["tags"] = sorted(set(old["tags"]) | set(entry["tags"]))
            if entry["_translation_len"] > old["_translation_len"]:
                old["translation"] = entry["translation"]
                old["_translation_len"] = entry["_translation_len"]
            if entry["frq"] > 0:
                old["frq"] = entry["frq"] if old["frq"] <= 0 else min(old["frq"], entry["frq"])
            if entry["bnc"] > 0:
                old["bnc"] = entry["bnc"] if old["bnc"] <= 0 else min(old["bnc"], entry["bnc"])
            if len(entry["phonetic"]) > len(old["phonetic"]):
                old["phonetic"] = entry["phonetic"]

    words = list(merged.values())
    for entry in words:
        entry.pop("_translation_len", None)
    words.sort(key=lambda w: (w["frq"] if w["frq"] > 0 else 10**9,
                              w["bnc"] if w["bnc"] > 0 else 10**9,
                              w["word"].lower()))

    def count(tag: str | None) -> int:
        if tag is None:
            return len(words)
        return sum(1 for w in words if tag in w["tags"])

    counts = {
        "all": len(words),
        "cet4": count("cet4"),
        "cet6": count("cet6"),
        "ielts": count("ielts"),
        "cet6_new": sum(1 for w in words if "cet6" in w["tags"] and "cet4" not in w["tags"]),
    }
    meta = {
        "source": "ECDICT",
        "source_url": ECDICT_HOME,
        "source_ref": ECDICT_REF,
        "license": LICENSE,
        "frequency": "COCA contemporary corpus rank (frq; lower = more frequent)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "counts": counts,
        "stats": {
            "csv_rows": rows,
            "kept": kept,
            "rejected_phrase": rejected_phrase,
            "rejected_translation": rejected_translation,
        },
    }
    return {"meta": meta, "words": words}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=None, help="local ECDICT CSV")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--keep-csv", action="store_true", help="keep the downloaded 66MB CSV")
    parser.add_argument("--refresh", action="store_true", help="re-download even if a cache exists")
    args = parser.parse_args()

    csv_path = args.csv
    downloaded = False
    if csv_path is None:
        csv_path = CACHE_DIR / "ecdict.csv"
        if args.refresh or not csv_path.exists():
            download_csv(csv_path, keep=args.keep_csv)
            downloaded = True
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1

    print(f"Reading {csv_path} ...", flush=True)
    data = build(csv_path)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".json.part")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    tmp.replace(args.out)
    meta = data["meta"]
    print("Built", args.out)
    print("  counts:", meta["counts"])
    print("  stats :", meta["stats"])
    print(f"  size  : {args.out.stat().st_size / 1024 / 1024:.2f} MB")
    if downloaded and not args.keep_csv:
        try:
            csv_path.unlink()
            print("Removed downloaded CSV (use --keep-csv to keep it).")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
