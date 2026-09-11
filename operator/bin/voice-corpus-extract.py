#!/usr/bin/env python3
"""Extract the author's own prose from Claude Code transcripts into a corpus.

Tracer Stage 1. Reads ``role=user`` text turns from ``~/.claude/projects``
transcripts, filters harness noise and pasted code, de-dupes, and writes a
reviewable corpus JSONL (``{"id","source","text"}`` per line). The output is
RAW prose and stays local — never uploaded. The ingest step turns it into
content-free structural-diff samples.

Usage::

    cd operator
    python bin/voice-corpus-extract.py --out /tmp/scott-corpus.jsonl --limit 30
    # review /tmp/scott-corpus.jsonl by eye, delete any sample you don't want
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # operator/ on sys.path

from bin.lib.voice_corpus import extract_corpus  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract author prose from CC transcripts.")
    p.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"))
    p.add_argument("--glob", default="**/*.jsonl", help="Glob under --projects-dir.")
    p.add_argument("--out", required=True, help="Corpus JSONL output path (local only).")
    p.add_argument("--min-words", type=int, default=12)
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args(argv)

    paths = sorted(Path(args.projects_dir).glob(args.glob))
    corpus = extract_corpus(paths, min_words=args.min_words, limit=args.limit)
    out = Path(args.out)
    out.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in corpus),
        encoding="utf-8",
    )
    print(f"extracted {len(corpus)} samples from {len(paths)} transcripts -> {out}")
    print("REVIEW the corpus by eye before ingesting; it contains raw prose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
