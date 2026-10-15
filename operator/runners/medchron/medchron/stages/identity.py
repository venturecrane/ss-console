"""`identity`: the cross-client gate, run after build_units (an update: after
vision) and before the map. $0. Every flag is logged in one line; a joint
matter without every DOB authored runs at half strength and says so.

Since 2026-10-13 it also reads every FOLDED email attachment's text, now that
vision has transcribed the scanned ones, for a different person
(``gates/foreign_identity``): an attachment that names someone else holds the
run (exit 2) until a person releases it. The fold hook checked the text
layers it could read before vision; this is the check that sees the scans.
"""

from __future__ import annotations

import json

from ..gates import cross_client, foreign_identity as FI
from .base import StageRun, read_jsonl


def _foreign_folded(sr: StageRun) -> list[str]:
    surnames, dobs = FI.identities(sr.job.units)
    free = FI.released(sr.slug_dir)
    out = []
    for r in read_jsonl(sr.slug_dir / "raw_manifest.jsonl"):
        if not r.get("ok") or not r.get("from_email"):
            continue
        rid = str(r.get("id") or "")
        tp = sr.slug_dir / "text" / f"{rid}.txt"
        text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else ""
        found = FI.findings(text, surnames, dobs, FI.first_names(sr.job.units))
        if found and rid not in free and rid.removeprefix("msgatt-") not in free:
            out.append(f"{rid}: {'; '.join(found)}")
    return out


def run(sr: StageRun) -> int:
    foreign = _foreign_folded(sr)
    if foreign:
        sr.log(
            f"IDENTITY HOLD: {len(foreign)} folded email attachment(s) name a different person than the client: "
            f"{foreign[:5]}; a person reads them and releases any that are the client's ({FI.RELEASE_FILE})"
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
