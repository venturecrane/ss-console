"""An update the entry read HELD, and SMD's finish of it (``stages/entry_audit_update``
holds; this keeps what the resume needs). $0, no model.

SMD finishes a held update by correcting each held line in
``runs/<unit>/final-chronology.md`` (or deleting a held entry whole) and
resuming the job. Proof 10 then fails a value that left the document, unless
something named it: the numbers SMD took out of the held lines are named
here. When the stage holds it saves the held document and its held lines
(``held.json``, ``held-document.md`` beside the answers). On the next run, if
the document differs, it compares the two: for each entry holding a held
line, an entry still carried under the same date and provider lines excuses
the held lines' numbers it no longer states; an entry gone whole excuses all
of its numbers. The excused numbers accumulate in ``excused.json`` (numbers
only) and the held files are consumed. A resume with no edit changes nothing:
the document is the held one, every answer is on file, and it holds again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..matter.store import write_atomic
from ..medchron.update import body as B
from ..medchron.update.values import numbers
from . import entry_audit_io as IO

HELD, HELD_DOC, EXCUSED = "held.json", "held-document.md", "excused.json"


def _dir(slug_dir: Path, unit: str) -> Path:
    return slug_dir / "runs" / unit / IO.DIR


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def save(slug_dir: Path, unit: str, md: str, held: list[tuple[str, str]]) -> None:
    """The held document and its held (line, class) pairs, for the resume
    and SMD's ``update_settle``."""
    d = _dir(slug_dir, unit)
    d.mkdir(parents=True, exist_ok=True)
    write_atomic(d / HELD_DOC, md)
    rows = [{"line": ln, "class": k} for ln, k in sorted(set(held))]
    rec = {"doc_sha": _sha(md), "lines": sorted({ln for ln, _k in held}), "held": rows}
    write_atomic(d / HELD, json.dumps(rec, indent=1))


def excused(slug_dir: Path, unit: str) -> set[str]:
    p = _dir(slug_dir, unit) / EXCUSED
    try:
        got = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    except ValueError:
        return set()
    return {str(x) for x in got} if isinstance(got, list) else set()


def _named(held_md: str, md: str, lines: list[str]) -> set[str]:
    now = B.entries_of(md)[1]
    heads = {tuple(e.split("\n")[:2]): e for e in now}
    out: set[str] = set()
    for e in B.entries_of(held_md)[1]:
        mine = [ln for ln in lines if ln in e]
        if not mine or e in now:
            continue
        kept = heads.get(tuple(e.split("\n")[:2]))
        if kept is None:
            out |= numbers(e)  # the held entry was deleted whole
        else:
            out |= set().union(*(numbers(ln) for ln in mine)) - numbers(kept)
    return out


def consume(slug_dir: Path, unit: str, md: str) -> set[str]:
    """On a run after a hold: when SMD changed the document, the numbers SMD
    took out of the held lines, added to ``excused.json``; the held files go.
    Nothing when there was no hold or the document is the held one."""
    d = _dir(slug_dir, unit)
    try:
        rec = json.loads((d / HELD).read_text(encoding="utf-8"))
        held_md = (d / HELD_DOC).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return set()
    if not isinstance(rec, dict) or rec.get("doc_sha") == _sha(md):
        return set()
    got = _named(held_md, md, [str(x) for x in rec.get("lines") or []])
    write_atomic(d / EXCUSED, json.dumps(sorted(excused(slug_dir, unit) | got)))
    (d / HELD).unlink(missing_ok=True)
    (d / HELD_DOC).unlink(missing_ok=True)
    return got
