"""`identity`: the cross-client gate, run after build_units and before the
map. $0. Every flag is logged in one line; a joint matter without every DOB
authored runs at half strength and says so."""

from __future__ import annotations

import json

from ..gates import cross_client
from .base import StageRun


def run(sr: StageRun) -> int:
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
