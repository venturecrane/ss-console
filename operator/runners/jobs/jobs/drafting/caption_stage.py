"""The ``caption`` stage: choose the court document, read its caption, compare
it to the matter record, correct what the court's paper settles, and write
``caption.json`` (what compose, the attorney notes and the verdict read).

Never blocks the draft. A caption that cannot be read, or a correction that
fails, is recorded and the job goes on. Two things still stop it: a matter
record that cannot be READ (``RecordUnreadable``: "could not look" must never
read as "agrees") and a cost limit (``LimitHold``), both raised through.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ..attorneys.seat import roster_for
from . import attorneys, caption as caption_mod, caption_fix, caption_read


def _read(data: Path, doorway: Any, model: str, doc: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    """The caption read, paid at most once per job (a resume reuses it)."""
    got = caption_read.cached(data)
    if got is None:
        try:
            got = caption_read.read(doorway, model, doc, log)
        except caption_read.Unread as exc:
            got = {"unread": str(exc)}
        caption_read.save(data, got)
    return got


def _journaled(data: Path) -> list[dict[str, Any]]:
    """Corrections an earlier, interrupted attempt of this stage applied."""
    p = data / "caption-writes.jsonl"
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln] if p.is_file() else []
    return [r for r in rows if r.get("event") == "result" and r.get("status") == caption_fix.APPLIED]


#: A skeleton slot code fills from the court's caption:
#: ``{{FILL: CAPTION court_name | THE CAPTION}}``. Written as a FILL so a seat
#: on an image without this code still reads a known marker kind and fills it
#: from THE CAPTION block the drafter is given; with it, code fills the slot.
_SLOT = re.compile(r"`?\{\{FILL:\s*CAPTION\s+([a-z_]+)\s*\|\s*THE CAPTION\s*\}\}`?")


def fill_skeleton(skeleton: str, cap: dict[str, Any] | None) -> str:
    """The class skeleton with each ``{{FILL: CAPTION <field> | THE CAPTION}}`` slot set to the
    court's caption as the caption stage read it (``caption.json`` fields),
    or a NOT IN RECORD marker when the court's paper does not carry the field.

    A skeleton that prints a caption line itself states the firm's guess at
    what every court's paper says: on 2026-11-04 a brief's court line carried
    a word its pleadings do not, while the caption stage held the court's own
    form. Code fills the slot, so the drafter never chooses."""

    def one(m: re.Match[str]) -> str:
        field = m.group(1)
        v = ((cap or {}).get("fields") or {}).get(field)
        value = v.get("value") if isinstance(v, dict) else None
        if value and str(value).strip():
            return str(value).strip()
        return "{{NOT IN RECORD: " + field.replace("_", " ") + ", not on the court's caption}}"

    return _SLOT.sub(one, skeleton)


def context(cap: dict[str, Any]) -> list[str]:
    """What compose and audit receive from ``caption.json``: the court's caption,
    then the firm's authored attorney block when one was chosen (attorneys.py)."""
    out = [caption_mod.block(cap["fields"], cap["source"])]
    if cap.get("attorney_block"):
        out.append(attorneys.text(cap["attorney_block"]))
    return out


def run(
    data: Path,
    doorway: Any,
    firm: Any,
    seat: Any,
    matter_id: str,
    facts: dict[str, Any],
    requester: str,
    log: Callable[[str], None],
) -> dict[str, Any]:
    model, firm_domains = firm.model("transcription"), firm.firm_domains
    doc = caption_mod.source_document(data)
    name = str(doc.get("name")) if doc else None
    got = _read(data, doorway, model, doc, log) if doc else None
    fields = caption_read.fields(got, firm_domains, doc["kind"] != "answer") if doc and got and "caption" in got else {}
    record = caption_mod.read_record(seat, matter_id, facts)
    diffs, compared = caption_mod.compare(fields, record, name or "") if fields else ([], [])
    earlier = _journaled(data)
    try:
        corrections, diffs, alarms = caption_fix.apply(getattr(seat, "client", None), matter_id, diffs, data)
    except Exception as exc:  # noqa: BLE001 - a correction never blocks the draft; the differences are still reported
        log(f"  caption corrections: {type(exc).__name__}: {str(exc)[:200]}")
        corrections, alarms = [], []
        for d in diffs:
            d.pop("fix", None)
    corrections += [
        {"field": r["field"], "from": r["from"], "to": r["to"], "source_document": name}
        for r in earlier
        if not any(c["field"] == r["field"] and c["to"] == r["to"] for c in corrections)
    ]
    unread = (got or {}).get("unread")
    if compared:
        why = ""
    elif not doc:
        why = "no court document in the file carries a caption"
    elif unread:
        why = f"the caption could not be read: {unread}"
    else:
        why = "neither the court's paper nor the matter record carries a comparable field"
    scanned = bool(got and "caption" in got and int(got.get("text_chars") or 0) < caption_read.MIN_TEXT_CHARS)
    return {
        "source": name,
        "kind": doc["kind"] if doc else None,
        "scanned": scanned,
        "fields": fields,
        "discrepancies": diffs,
        "corrections": corrections,
        "restore_incomplete": alarms,
        "compared": compared,
        "none_because": why,
        # The matter record's parties, by side (names only): which parties a
        # request serves its sets on is checked against them (parties.py).
        "parties": [
            {"side": p.get("side"), "name": p.get("name")}
            for p in record.get("parties") or []
            if isinstance(p, dict) and p.get("name")
        ],
        # The firm's authored attorney block for this job: the responsible
        # attorney's, else the requester's, else none (attorneys.py); from the
        # firm's attorney roster when one is installed (attorneys.entries).
        "attorney_block": attorneys.pick(
            attorneys.entries(firm.attorneys, roster_for(seat)), record.get("attorney_email"), requester
        ),
    }
