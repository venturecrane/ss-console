"""FINAL VERDICT GATE: every live claim, finally audited, finally SUPPORTED.

Audit keys hash the claim text, so a claim repaired between rounds gets a
new key and its earlier verdict no longer applies. A delivered chronology
once shipped with 181 of 658 claims never audited in their final form
because nothing forced this intersection. This gate does, and it can fail:
any live claim with NO verdict, any live claim whose last verdict is not
SUPPORTED (a live SUPPORTED_WIDENED means the citation rewrite was skipped),
or any control wrongly SUPPORTED.
"""

from __future__ import annotations

from typing import Any, Callable

from . import claims as CL
from .page_text import exhibit_paths
from .run import AuditPaths


def check(paths: AuditPaths, log: Callable[[str], None]) -> tuple[bool, dict[str, Any]]:
    body = CL.body_of(paths.doc.read_text(encoding="utf-8"))
    live = {c["key"] for c in CL.extract_claims(body, set(exhibit_paths(paths.out)))}
    rows = CL.read_rows(paths.results)
    latest = CL.latest_real(rows, live)
    ctl_bad = [
        r
        for r in rows
        if (str(r.get("kind", "")).startswith("control") and r.get("verdict") == "SUPPORTED")
        or (r.get("kind") == "reverse-control" and not r.get("agree"))
    ]
    never = live - set(latest)
    bad = {k: r for k, r in latest.items() if r["verdict"] != "SUPPORTED"}
    tally: dict[str, int] = {}
    for r in latest.values():
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    log(f"live claims {len(live)}; finally audited {len(latest)}; NEVER audited (final) {len(never)}")
    log("verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1])))
    if ctl_bad:
        log(f"CONTROLS wrongly SUPPORTED: {len(ctl_bad)} -> audit INVALID")
    for r in list(bad.values())[:20]:
        log(f"  !! Ex{r.get('exhibit')} p.{r.get('page_spec')} {r['verdict']}: {str(r.get('note', ''))[:90]}")
    summary = {
        "live": len(live),
        "audited": len(latest),
        "never": len(never),
        "bad": len(bad),
        "controls_bad": len(ctl_bad),
        "tally": tally,
    }
    if never or bad or ctl_bad:
        log("GATE FAIL: the document is not deliverable")
        return False, summary
    log("GATE PASS: every live claim finally audited and SUPPORTED")
    return True, summary
