"""The pages an update charges: the pages a model read in this run, and no others. $0.

WHY. 2026-11-06: an update charged the cycle page allowance with every page
it extracted, and most of what an update pulls is pages the delivered
chronology already read (two updates charged 451 and 1,386 pages; SMD
credited them back by hand). The fix then took the delivered copies off.
2026-11-17: the firm's agreement says an update reads and counts only records
not yet covered, and on a real update 204 of the 839 pages charged were
repeats of another NEW document's page, 95 were re-filed extracts and 34 were
pages of a withheld file nobody read. A page no model read was counted
against the firm's allowance.

THE RULE. A page is charged if and only if the update sent it to be read
on its own account: its row in the per-page record (``runs/<unit>/pages.jsonl``,
``medchron/pages_record``) has status ``read`` and is not a suspected copy
(``pages_record.charged``). So the charge and the page record cannot
disagree. Not charged: a copy of a delivered page, a copy of another new
document's page (the first occurrence is read and charged once; its copies
are not), a re-filed extract, a byte-identical or skipped file, a withheld
file, a blank or billing page set aside by code, a page that could not be
read, and (2026-11-22, the Captain) a page READ only because a rule
suspected it of copying a page already covered (``evidence.suspected``: a
near picture, a text match): code
can no longer set such a page aside, and the firm does not pay for SMD's
caution. A page read with no suspicion IS charged: it was read.

ASKED AND CHARGED. Before ``update_select`` writes the full record,
``update_pages`` has written every candidate page as ``read`` unless it is
CERTAINLY never sent to a model (an exact picture match of a delivered page
or of an earlier new document's page, or a page of an exact re-filed copy;
``page_rows.candidate_rows``): a near picture match, a page of a file that
could not be drawn and anything undecided count as to be read. That count is
what the page question before the first paid step is asked with
(``asked_pages``, recorded in ``ASKED``, written whole). An exact picture
match is never transcribed and no later step reads it, and a page read as a
suspected copy is never charged (it is not counted when asked either), so
pages charged never exceed pages asked by construction (``read_exceeds_asked``,
0, named for its first form); the charge is
still capped at the asked figure as a backstop, and a cap records
``charged_capped`` with both numbers (``capped``) in the run's notes
(``cap_notes``); nothing stops. An asked record that is absent,
truncated or malformed means no cap (the charge is the pages read) and its
state is recorded (``asked_state``); it never raises. With no page record at
all (an update stopped before ``update_pages``), the 2026-11-06 rule stands
(``old_rule_pages``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ...stages.base import read_json, read_jsonl
from ...stages.prior_reading import PREFIX as PRIOR_PREFIX
from .. import pages_record as PR
from .select import ALREADY

#: ``update_select``'s skip key for a file the firm already holds (``_candidates``).
BYTE_IDENTICAL = "byte-identical to a delivered exhibit"
#: The page-record statuses of a page sent to be read.
READ_STATUSES = PR.READ_STATUSES


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def read_pages(slug_dir: Path, unit: str) -> int | None:
    """Pages this update sent to be read (``READ_STATUSES``), or None with no page record."""
    rows = PR.read(slug_dir / "runs" / unit)
    if not rows:
        return None
    return sum(1 for r in rows if r.get("status") in READ_STATUSES)


def chargeable_pages(slug_dir: Path, unit: str) -> int | None:
    """Pages sent to be read less those read only as a suspected copy
    (``pages_record.charged``), or None with no page record."""
    rows = PR.read(slug_dir / "runs" / unit)
    if not rows:
        return None
    return sum(1 for r in rows if PR.charged(r))


def delivered_pages(slug_dir: Path, unit: str) -> int:
    """Pages of this update's pulled documents the delivered chronology already
    read (the 2026-11-06 rule's deduction)."""
    sel = read_json(slug_dir / "runs" / unit / "update" / "selection.json", None)
    if not isinstance(sel, dict):
        return 0
    n = sum(_int((x.get("excluded") or {}).get(ALREADY)) for x in sel.get("exhibits") or [] if isinstance(x, dict))
    n += sum(
        _int(s.get("pages"))
        for s in sel.get("subsets") or []
        if isinstance(s, dict) and isinstance(s.get("subset_of"), dict) and not s["subset_of"].get("id")
    )
    skipped = sel.get("skipped") or {}
    held = {
        str(i) for k, ids in skipped.items() if k == BYTE_IDENTICAL or k.startswith(PRIOR_PREFIX) for i in ids or []
    }
    n += sum(_int(r.get("pages")) for r in read_jsonl(slug_dir / "extracted.jsonl") if str(r.get("id")) in held)
    return n


def old_rule_pages(slug_dir: Path, unit: str, pages_read: int) -> int:
    """The 2026-11-06 charge, kept for comparison: ``pages_read`` less ``delivered_pages``."""
    return max(0, pages_read - delivered_pages(slug_dir, unit))


#: The page figure the update was asked about before its first paid step (the
#: most it may charge), kept beside the update's other records.
ASKED = "update/asked_pages.json"
#: The code a run records when the pages read exceeded the pages asked about.
CHARGED_CAPPED = "charged_capped"


def asked_pages(slug_dir: Path, unit: str, pages_read: int) -> int:
    """The figure the page question is asked with, and recorded (the largest
    asked on any attempt): ``chargeable_pages`` (an upper bound on the charge
    before selection: a candidate page a picture already marks a suspected
    copy is read and never charged, so a job is not held for it), or with no
    page record ``old_rule_pages``."""
    n = chargeable_pages(slug_dir, unit)
    asked = n if n is not None else old_rule_pages(slug_dir, unit, pages_read)
    p = slug_dir / "runs" / unit / ASKED
    before = _asked(slug_dir, unit)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")  # whole or not at all: a kill mid-write never leaves half a file
        tmp.write_text(json.dumps({"pages": max(asked, before or 0)}), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass  # unrecorded means no cap later (``asked_state``): never a reason to stop
    return asked


#: How the recorded asked figure read: present, absent, or unreadable (then there is no cap).
ASKED_OK, ASKED_ABSENT, ASKED_UNREADABLE = "ok", "absent", "unreadable"


def asked_state(slug_dir: Path, unit: str) -> tuple[int | None, str]:
    """(the recorded asked figure or None, how it read). Absent, truncated or
    malformed means no cap: the charge is then the pages read, and the state
    says so. Never raises."""
    p = slug_dir / "runs" / unit / ASKED
    if not p.is_file():
        return None, ASKED_ABSENT
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ASKED_UNREADABLE
    v = raw.get("pages") if isinstance(raw, dict) else None
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v, ASKED_OK
    return None, ASKED_UNREADABLE


def _asked(slug_dir: Path, unit: str) -> int | None:
    return asked_state(slug_dir, unit)[0]


def charged_pages(slug_dir: Path, unit: str, pages_read: int) -> int:
    """The pages this update charges: ``chargeable_pages`` (with no page
    record, ``old_rule_pages``), never more than the figure the firm was asked
    about (``ASKED``); a run that read more is charged the asked figure and
    says so (``capped``)."""
    n = chargeable_pages(slug_dir, unit)
    n = n if n is not None else old_rule_pages(slug_dir, unit, pages_read)
    asked = _asked(slug_dir, unit)
    return min(n, asked) if asked is not None else n


def capped(slug_dir: Path, unit: str) -> dict[str, Any] | None:
    """``{"code": CHARGED_CAPPED, "read": n, "asked": m}`` when this update
    would charge more pages than it was asked about (`n`: ``chargeable_pages``;
    it is then charged the asked figure; nothing stops), else None."""
    n, asked = chargeable_pages(slug_dir, unit), _asked(slug_dir, unit)
    if n is None or asked is None or n <= asked:
        return None
    return {"code": CHARGED_CAPPED, "read": n, "asked": asked}


def unit_pages(slug_dir: Path, unit: str, extracted: Path, update: object) -> int:
    """The pages a unit counts against the allowance: every page a run
    extracted; on an update (`update` set), the pages it sent to be read
    (``charged_pages``). The one figure every outcome, hold and stage row of
    ``Driver`` carries."""
    from ...budget import pages_read as extracted_pages

    pages = extracted_pages(extracted)
    return pages if update is None else charged_pages(slug_dir, unit, pages)


def question_pages(slug_dir: Path, unit: str, extracted: Path, update: object) -> int:
    """The figure the page question before the first paid step is asked with:
    every page on a run; on an update, the recorded upper bound (``asked_pages``)."""
    from ...budget import pages_read as extracted_pages

    pages = extracted_pages(extracted)
    return pages if update is None else asked_pages(slug_dir, unit, pages)


def cap_notes(slug_dir: Path, unit: str) -> list[str]:
    """The outcome note when this update read more than it was asked about (triage reads the code)."""
    cap = capped(slug_dir, unit)
    if cap is None:
        return []
    return [f"{cap['code']}: read {cap['read']} page(s), asked about {cap['asked']}; charged {cap['asked']}"]


def figures(slug_dir: Path, unit: str) -> dict[str, int | None]:
    """{pages_read, pages_suspected_copies, pages_charged, charged_old_rule,
    extracted}: what the rehearsal prints (``charge_line``). ``pages_charged``
    is ``pages_read`` less ``pages_suspected_copies`` (capped at the asked figure)."""
    from ...budget import pages_read as extracted_pages

    extracted = extracted_pages(slug_dir / "extracted.jsonl")
    return reconcile(slug_dir, unit) | {
        "pages_asked": asked_state(slug_dir, unit)[0],
        "asked_record": asked_state(slug_dir, unit)[1],
        "pages_read": read_pages(slug_dir, unit),
        "pages_suspected_copies": _suspected(slug_dir, unit),
        "pages_charged": charged_pages(slug_dir, unit, extracted),
        "charged_capped": 1 if capped(slug_dir, unit) else 0,
        # The property the upper bound promises: 0. A page set aside is never read and a suspected
        # copy is never charged, so the pages charged never exceed the pages asked about.
        "read_exceeds_asked": 1 if capped(slug_dir, unit) else 0,
        "charged_old_rule": old_rule_pages(slug_dir, unit, extracted),
        "extracted": extracted,
    }


def _suspected(slug_dir: Path, unit: str) -> int | None:
    n, c = read_pages(slug_dir, unit), chargeable_pages(slug_dir, unit)
    return None if n is None or c is None else n - c


def reconcile(slug_dir: Path, unit: str) -> dict[str, Any]:
    """The page record against the extraction, per document. The CHARGE reads
    the RECORD, never the extraction: the record holds one row per page the
    update saw (an image file, which the extraction may count as no page, is
    one page there, and is charged only if it was sent to be read). So the
    two may differ, and every document where they do is named (by its id,
    with both counts), so the totals never drift unexplained."""
    rows = PR.read(slug_dir / "runs" / unit)
    rec: dict[str, int] = {}
    for r in rows:
        if int(r.get("page") or 0) > 0:
            rec[str(r["doc"])] = rec.get(str(r["doc"]), 0) + 1
    ext = {str(x.get("id")): _int(x.get("pages")) for x in read_jsonl(slug_dir / "extracted.jsonl")}
    differ = [
        {"doc": d, "record": rec.get(d, 0), "extracted": ext.get(d, 0)} for d in sorted(rec) if rec[d] != ext.get(d, 0)
    ]
    return {"record_pages": sum(rec.values()), "record_vs_extracted": differ, "charged_from": "record"}


def charge_line(slug_dir: Path, unit: str, outcome_pages: int) -> str:
    """One line: pages asked about, read, read as suspected copies, charged
    (the outcome's figure), the old rule's charge."""
    f = figures(slug_dir, unit)
    if f["pages_read"] is None:
        return f"charge: no page record; {outcome_pages} charged under the 2026-11-06 rule; {f['extracted']} extracted"
    cap = capped(slug_dir, unit)
    owed = f["pages_read"] - (f["pages_suspected_copies"] or 0)
    same = "equal" if owed == outcome_pages else (f"{CHARGED_CAPPED}" if cap else "NOT EQUAL")
    return (
        f"charge: asked {f['pages_asked']}; {f['pages_read']} page(s) sent to be read, "
        f"{f['pages_suspected_copies']} of them suspected copies (not charged); {outcome_pages} charged "
        f"({same}); {f['charged_old_rule']} under the 2026-11-06 rule; {f['extracted']} extracted"
    )
