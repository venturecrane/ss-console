"""$0 rehearsal of the paper reader on a folder of filed Word files.

    python -m jobs.drafting.paper.rehearse <dir-of-docx> [--key <answer-key.json>]

Reads every .docx in the folder and prints, per file, the type guess, the
item count, whether numbering holds, the defined terms, the declaration's
count mentions and reason-list sizes, and the Unread when the reader could
not read it. It prints counts, terms and reasons only, never a request's or
answer's text. With ``--key`` it compares each file against an answer key:

    {"<file name>": {"items": 40, "terms": ["YOU", "INCIDENT"],
                     "declaration": {"counts": [40, 40, 40], "lists": {"A": [1, 2], "": [1, 40]}}}}

(a list under no reason letter is keyed ""). Exit 0 when every file reads and
matches its key, 1 otherwise. No model call, no network, no seat.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import blocks as blocks_mod, read as read_mod
from .model import Paper, Unread


def _lists(d: Any) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for r in d.reason_lists:
        out.setdefault(r.reason or "", []).extend(r.numbers)
    return out


def summary(paper: Paper) -> dict[str, Any]:
    d = paper.declaration
    return {
        "type": paper.type,
        "items": len(paper.items),
        "numbering_ok": True,
        "terms": [x.term for x in paper.definitions],
        "declaration": None
        if d is None
        else {
            "counts": [c.value for c in d.count_mentions],
            "lists": _lists(d),
        },
        "detectors": paper.detectors,
    }


def compare(got: dict[str, Any], key: dict[str, Any]) -> list[str]:
    """The ways ``got`` differs from the answer key, as short reasons."""
    out = []
    if "items" in key and key["items"] != got["items"]:
        out.append(f"items {got['items']} != key {key['items']}")
    if "terms" in key and list(key["terms"]) != got["terms"]:
        missing = sorted(set(key["terms"]) - set(got["terms"]))
        extra = sorted(set(got["terms"]) - set(key["terms"]))
        out.append(f"terms differ: missing {missing}, extra {extra}")
    kd, gd = key.get("declaration"), got["declaration"] or {"counts": [], "lists": {}}
    if kd is not None:
        if "counts" in kd and list(kd["counts"]) != gd["counts"]:
            out.append(f"declaration counts {gd['counts']} != key {kd['counts']}")
        for reason, nums in (kd.get("lists") or {}).items():
            if gd["lists"].get(reason) != nums:
                out.append(f"declaration list ({reason or 'whole set'}) differs")
    return out


def _line(name: str, got: dict[str, Any]) -> str:
    d = got["declaration"]
    decl = (
        "no declaration"
        if d is None
        else f"declaration counts {d['counts']}, lists {{{', '.join(f'{k or chr(45)}: {len(v)}' for k, v in d['lists'].items())}}}"
    )
    return (
        f"{name}: {got['type']}, {got['items']} items, numbering ok, "
        f"terms {got['terms']}, {decl}, detectors {got['detectors']}"
    )


def rehearse(folder: Path, key: dict[str, Any] | None) -> int:
    files = sorted(folder.glob("*.docx"))
    if not files:
        print(f"no .docx files in {folder}")
        return 1
    bad = 0
    for f in files:
        got = read_mod.read(blocks_mod.read_docx(f))
        if isinstance(got, Unread):
            print(f"{f.name}: UNREAD region={got.region} block={got.block_id} why={got.why}")
            bad += 1
            continue
        s = summary(got)
        print(_line(f.name, s))
        if key is not None:
            diffs = compare(s, key[f.name]) if f.name in key else ["not in the key"]
            print(f"  key: {'match' if not diffs else '; '.join(diffs)}")
            bad += bool(diffs)
    print(f"{len(files)} files, {len(files) - bad} read{' and matched' if key is not None else ''}, {bad} not")
    return 0 if bad == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobs.drafting.paper.rehearse")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--key", type=Path)
    a = ap.parse_args(argv)
    key = json.loads(a.key.read_text(encoding="utf-8")) if a.key else None
    return rehearse(a.folder, key)


if __name__ == "__main__":
    sys.exit(main())
