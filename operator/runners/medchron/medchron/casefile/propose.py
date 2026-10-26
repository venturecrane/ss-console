"""`propose`: the numbered changes a person may approve, decided in code.

A line is proposed only when every rule already holds, so an approval never
has to be second-guessed:

* a TASK CLOSURE: the reader's ``mark_complete`` passed the validator (proof
  documents read, no money or court words, not a statute reminder) AND the
  second reader ``confirmed`` it;
* a MEDICALS ROW: a new row (not already on the tab) on a file whose posture
  the firm writes rows on (``write_postures``), whose charge appears in its
  source bill's text, whose source is not a scan, and which carries both
  service dates.

Each line carries the facts its apply needs and a snapshot of the record it
changes (the task as read, the Medicals tab as read): at apply time the record
is read again and a line whose snapshot no longer matches is refused, never
written. The facts live in the lane's state (``state.save_proposals``); the
broker holds the line's ids and the hash of its facts, so an approval binds to
exactly what was proposed.
"""

from __future__ import annotations

import re
from typing import Any

from . import state as state_mod
from .validate import scanned_bill

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def task_snapshot(t: dict[str, Any]) -> str:
    """What a closure was proposed against: changed means refused at apply."""
    return state_mod.digest(
        {
            "subject": str(t.get("subject") or ""),
            "isCompleted": bool(t.get("isCompleted")),
            "due": str(t.get("dueDate") or t.get("dueDateOnly") or "")[:10],
            "note": str(t.get("note") or ""),
        }
    )


def medicals_snapshot(inv: dict[str, Any], design: str) -> tuple[str, str] | None:
    """(item id, hash of its values) of the matter's ONE Medicals tab; None
    when the matter has none or several (one per claimant: a person picks)."""
    tabs = [
        (k, v)
        for k, v in (inv.get("layouts") or {}).items()
        if design
        in str(
            ((v.get("row") or {}).get("layoutDesign") or {}).get("id")
            or (v.get("row") or {}).get("layoutDesignId")
            or ""
        )
    ]
    if len(tabs) != 1:
        return None
    return tabs[0][0], state_mod.digest(tabs[0][1].get("values"))


def task_lines(rec: dict[str, Any], inv: dict[str, Any], meta: dict[str, Any], verdicts: dict[str, Any]) -> list[dict]:
    by_id = {str(t.get("id")): t for t in inv.get("tasks_open") or []}
    out = []
    for t in rec.get("open_tasks") or []:
        tid = str(t.get("task_id") or "")
        if (
            t.get("action") != "mark_complete"
            or (verdicts.get(tid) or {}).get("verdict") != "confirmed"
            or tid not in by_id
        ):
            continue
        names = [
            str((meta.get(str(d).lower()) or meta.get(str(d)) or {}).get("name") or d)
            for d in t.get("proof_doc_ids") or []
        ]
        out.append(
            {
                "kind": "task",
                "source_id": tid,
                "facts": {
                    "number": rec.get("number"),
                    "task_id": tid,
                    "subject": str(by_id[tid].get("subject") or ""),
                    "snapshot": task_snapshot(by_id[tid]),
                    "proof_doc_ids": [str(d) for d in t.get("proof_doc_ids") or []],
                    "proof_names": names,
                    "why": str(t.get("why") or ""),
                    "audit_reason": str((verdicts.get(tid) or {}).get("reason") or ""),
                },
            }
        )
    return out


def medicals_lines(
    rec: dict[str, Any], inv: dict[str, Any], meta: dict[str, Any], firm: Any, text_of: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """(lines, why each other new row was not proposed)."""
    if rec.get("posture") not in set(firm.get("write_postures")):
        return [], []
    tab = medicals_snapshot(inv, str(firm.get("medicals_layout_design")))
    out, skipped = [], []
    for i, m in enumerate(rec.get("medicals") or []):
        if not isinstance(m, dict) or m.get("existing_row"):
            continue
        src = str(m.get("source_doc_id") or "")
        start, end = str(m.get("service_start") or ""), str(m.get("service_end") or m.get("service_start") or "")
        if tab is None:
            skipped.append("the matter has no single Medicals tab")
        elif scanned_bill(meta, text_of, src):
            skipped.append("the bill is a scan, not read in this run")
        elif not (_DATE.match(start) and _DATE.match(end)):
            skipped.append("the bill's dates of service were not read")
        else:
            src_meta = meta.get(src.lower()) or meta.get(src) or {}
            out.append(
                {
                    "kind": "medicals",
                    "row_index": i,
                    "facts": {
                        "number": rec.get("number"),
                        "provider": str(m.get("provider")),
                        "charge": f"{float(m['charge']):.2f}",
                        "service_start": start,
                        "service_end": end,
                        "source_doc_id": src,
                        "source_name": str(src_meta.get("name") or src),
                        "page": m.get("page"),
                        "item_id": tab[0],
                        "snapshot": tab[1],
                        "why": str(m.get("note") or ""),
                    },
                }
            )
    for ln in out:
        ln["source_id"] = (
            "med-"
            + state_mod.digest([ln["facts"][k] for k in ("provider", "charge", "service_start", "source_doc_id")])[:24]
        )
    return out, skipped


def number_lines(per_matter: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Lines numbered 1..N in the workbook's matter order, each with the hash
    of its facts (what the broker binds an approval to)."""
    out, n = [], 0
    for mid, lines in per_matter:
        for ln in lines:
            n += 1
            out.append({**ln, "n": n, "matter_id": mid, "sha256": state_mod.digest(ln["facts"])})
    return out


def broker_view(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the broker records: ids and hashes, never a fact from a file."""
    return [{k: ln[k] for k in ("n", "kind", "matter_id", "source_id", "sha256")} for ln in lines]


__all__ = ["broker_view", "medicals_lines", "medicals_snapshot", "number_lines", "task_lines", "task_snapshot"]
