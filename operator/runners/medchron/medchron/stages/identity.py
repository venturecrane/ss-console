"""`identity`: the cross-client gate, run after build_units (an update: after
vision) and before the map. $0. Every flag is logged in one line; a joint
matter without every DOB authored runs at half strength and says so.

Since 2026-10-13 it also reads every FOLDED email attachment's text, now that
vision has transcribed the scanned ones, for a different person
(``gates/foreign_identity``). The fold hook checked the text layers it could
read before vision; this is the check that sees the scans.

An attachment that names someone else is LEFT OUT BY RULE (2026-10-24, the
Captain): it used to hold the run until the firm said whose it was, and a
chronology now reaches the firm only when it is delivered. The withhold is
recorded where a person's would be (``FI.withhold_by_rule``, ``by: "rule"``),
the attachment is taken out of the units build_units wrote (exactly what a
rerun of build_units does with a withheld id, ``_leave_out_withheld``), and the
run goes on; decide_orphans then explains it with the rule's wording, which
the Records Reviewed and Limitations section prints. What the record says
about the other person (a date of birth, a name) is SMD's detail and stays in
``identity_holds.json`` beside the manifest, never in a log line or in the
delivered package.
"""

from __future__ import annotations

import json
from typing import Any

from ..gates import cross_client, foreign_identity as FI
from .base import StageRun, read_jsonl

HOLDS_FILE = "identity_holds.json"


def _foreign_folded(sr: StageRun) -> list[dict[str, Any]]:
    """Every folded attachment, not yet decided, whose text names someone other
    than the client: its id, its file name, the saved email it came from, and
    what was found (SMD's detail)."""
    surnames, dobs = FI.identities(sr.job.units)
    # Released is the client's; withheld is out of every unit (build_units)
    # and disclosed as left out (decisions.orphans). Neither holds again.
    decided = FI.released(sr.slug_dir) | FI.withheld(sr.slug_dir)
    out: list[dict[str, Any]] = []
    for r in read_jsonl(sr.slug_dir / "raw_manifest.jsonl"):
        if not r.get("ok") or not r.get("from_email"):
            continue
        rid = str(r.get("id") or "")
        if FI.matches(rid, decided):
            continue
        tp = sr.slug_dir / "text" / f"{rid}.txt"
        text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else ""
        found = FI.findings(text, surnames, dobs, FI.first_names(sr.job.units))
        if found:
            out.append(
                {
                    "id": rid,
                    "name": str(r.get("name") or "") or None,
                    "container": str(r.get("container") or "") or None,
                    "email_subject": str(r.get("from_email") or "") or None,
                    "found": found,
                }
            )
    return out


def _named(hold: dict[str, Any]) -> str:
    """How the hold line names one attachment: id, file name, and the saved
    email it came from, as far as the manifest knows them. Never the finding."""
    out = hold["id"]
    if hold.get("name"):
        out += f' "{hold["name"]}"'
    if hold.get("container"):
        out += f' attached to the email "{hold["container"]}"'
    return out


def _leave_out(sr: StageRun, ids: list[str]) -> int:
    """Take withheld ids out of every unit file build_units wrote; the count
    removed."""
    removed = 0
    for f in sorted((sr.slug_dir / "units").glob("*.json")):
        if f.name.startswith("_"):
            continue
        rows = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            continue
        keep = [r for r in rows if not (isinstance(r, dict) and FI.matches(str(r.get("id") or ""), set(ids)))]
        if len(keep) != len(rows):
            removed += len(rows) - len(keep)
            f.write_text(json.dumps(keep, indent=1), encoding="utf-8")
    return removed


def run(sr: StageRun) -> int:
    foreign = _foreign_folded(sr)
    if foreign:
        # The findings (what the record says about the other person) are
        # SMD's detail and stay on the seat; the log names files only.
        (sr.slug_dir / HOLDS_FILE).write_text(json.dumps(foreign, indent=1), encoding="utf-8")
        ids = [h["id"] for h in foreign]
        FI.withhold_by_rule(sr.slug_dir, ids, ground=FI.RULE_ANOTHER_PERSON)
        removed = _leave_out(sr, ids)
        sr.log(
            f"IDENTITY: {len(foreign)} folded email attachment(s) name a different person than the client; "
            f"left out by rule ({removed} unit row(s) removed) and disclosed in the package: "
            f"{'; '.join(_named(h) for h in foreign[:5])}"
        )
    if not sr.job.joint:
        sr.log(f"{sr.slug}: single-unit matter; nothing to check")
        return 0
    found, checked, missing = cross_client.flags(sr.slug_dir)
    for f in found:
        sr.log(
            f"IDENTITY? '{(f['file'] or '')[:56]}' routed to {f['unit']} but names {f['names']} {f['other']}x vs own {f['own']}x "
            f"(folder {f.get('folder') or '/'})"
        )
    (sr.slug_dir / "runs" / sr.unit.unit / "identity_flags.json").write_text(
        json.dumps(found, indent=1), encoding="utf-8"
    )
    sr.log(f"unit-identity check: {len(found)} flag(s) across {checked} file(s)")
    if missing:
        sr.log(f"HALF STRENGTH: no dob authored for {', '.join(missing)}")
    return 0
