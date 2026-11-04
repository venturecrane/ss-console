"""`gate`: the drafting discipline's mechanical gates over the finished letter,
before anything is rendered or filed.

The seat already carries the checker (``drafting_gate_check.py``, at the path
``smokeball_connector.record_check`` pins) and the connector's disposition table
around it, which refuses on every non-pass: FAIL findings, a usage error (the
likely case, not the exotic one), a timeout, an absent checker. This stage
reuses that table rather than a second reading of the checker's exits.

What the job hands it, and why:

* ``sources``: every document the drafter's digest was built from, by name.
  The firm's skeleton and fixed strings ride along as sources too: the
  skeleton's standing quotation (a case the firm quotes in every demand) is
  the firm's authored text, not the record, and must not read as invented.
* ``vision_sources``: the documents a machine transcribed; the checker counts
  them as record and WARNS, naming each.
* ``held_out``: the emails behind the privilege wall, by name, so the checker's
  leakage gate proves no eight-word run of a client's letter to her lawyer
  reached the draft.
* ``unextractable`` is deliberately EMPTY. The connector refuses on it because
  a draft quoted from a document the checker cannot read would look
  fabricated. Here the drafter never saw such a document either (the digest
  was built from the same extracted set), so nothing can be quoted from it; the
  gap audit lists it instead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .house import NOTES_MARK


def letter_part(md: str) -> str:
    return md.split(NOTES_MARK, 1)[0]


def collect(data: Path, firm: Any) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    rows = [json.loads(line) for line in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if line]
    machine = (
        set(json.loads((data / "transcribed.json").read_text(encoding="utf-8")))
        if (data / "transcribed.json").is_file()
        else set()
    )
    sources, vision = [], []
    for r in rows:
        p = r.get("text_path")
        if not p or not Path(p).is_file():
            continue
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        (vision if str(r.get("name")) in machine else sources).append((str(r.get("name")), text))
    if "_variant" in firm.data:  # a gap-audit-only job selects no demand variant
        sources.append(("firm demand skeleton", firm.text("skeleton")))
    sources.append(("firm fixed strings", firm.text("voice_fixed_strings")))
    walled = json.loads((data / "walled.json").read_text(encoding="utf-8")) if (data / "walled.json").is_file() else []
    names = [str(r.get("name") or "") for r in rows] + [str(w.get("name") or "") for w in walled]
    held = [
        (
            f"held-out {w['name']}",
            strip_names(Path(w["text_path"]).read_text(encoding="utf-8", errors="replace"), names),
        )
        for w in walled
        if Path(w["text_path"]).is_file()
    ]
    return sources, vision, held


_STEM = re.compile(r"\.(pdf|docx?|msg|txt|eml|jpe?g|png|tiff?)$", re.I)


def strip_names(text: str, names: list[str]) -> str:
    """A held-out email's text minus every document NAME in the file, its own
    included, before the leakage check reads it. Names are not content: the
    wall lists walled documents by name on purpose, and a deliverable cites
    record documents by name. A live demand job, 2026-10-07: a client email
    forwarding the carrier's letter carried that letter's name as its subject,
    so the gap audit's citation of the (unwalled) letter read as leakage."""
    for name in sorted({_STEM.sub("", n) for n in names if n}, key=len, reverse=True):
        toks = re.findall(r"[A-Za-z0-9]+", name)
        while toks and toks[0].lower() in ("fw", "fwd", "re"):  # a forward's name is its subject, prefixed
            toks = toks[1:]
        if len(toks) >= 4:
            text = re.sub(r"\b" + r"\W+".join(map(re.escape, toks)) + r"\b", " ", text, flags=re.I)
    return text


def run(data: Path, firm: Any, draft_md: str, name: str = "gate.json") -> dict[str, Any]:
    from smokeball_connector.record_check import run_record_check

    sources, vision, held = collect(data, firm)
    verdict = run_record_check(
        letter_part(draft_md),
        sources + held,
        held_out_names={name for name, _ in held},
        unextractable=[],
        vision_sources=vision,
    )
    out = {
        "passed": verdict.passed,
        "disposition": verdict.disposition,
        "refusals": verdict.refusals,
        "warnings": verdict.warnings,
        "infos": verdict.infos,
        "checked_sources": verdict.checked_sources,
    }
    (data / name).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
