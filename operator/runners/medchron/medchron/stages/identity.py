"""`identity`: the cross-client gate, run after build_units (an update: after
vision) and before the map. $0. Every flag is logged in one line; a joint
matter without every DOB authored runs at half strength and says so.

Since 2026-10-13 it also reads every FOLDED email attachment's text, now that
vision has transcribed the scanned ones, for a different person
(``gates/foreign_identity``): an attachment that names someone else holds the
run (exit 2) until a person releases it. The fold hook checked the text
layers it could read before vision; this is the check that sees the scans.

The hold line is the firm's wording, because the driver copies a stage's last
output into the job's reason and the Operator reads the reason to word the
hold. So the line names each attachment by its own file name and the saved
email it came from, which is what the requester can find and the one question
can be asked about, and never what the record says about the other person (a
date of birth, a name). That detail is SMD's and stays in ``identity_holds.json``
beside the manifest, as the update path keeps it. On 2026-10-22 the line
carried the id and the other person's date of birth: the Operator relayed the
id as "an internal tag", could name no file, and sent the attorney to look
through 350 documents.
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


def run(sr: StageRun) -> int:
    foreign = _foreign_folded(sr)
    if foreign:
        # The findings (what the record says about the other person) are
        # SMD's detail and stay on the seat; the line, which becomes the job's
        # reason and so the Operator's words, carries names only.
        (sr.slug_dir / HOLDS_FILE).write_text(json.dumps(foreign, indent=1), encoding="utf-8")
        sr.log(
            f"IDENTITY HOLD: {len(foreign)} folded email attachment(s) name a different person than the client: "
            f"{'; '.join(_named(h) for h in foreign[:5])}; a person reads them and releases any that are the "
            f"client's, or the firm says to leave them out (medchron.identity_decide)"
        )
        return 2
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
