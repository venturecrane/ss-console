"""`audit`: audit -> repair rounds to convergence, then the final gate. Paid
(audit tier for verdicts, judgment tier for repairs).

Repairs change claim text, changed text means a new audit key, and without a
forced re-audit the repaired claims ship unverified (181 of 658 once did).
Every round re-runs the audit; resumable keys make later rounds cheap, only
changed claims reach the API. The round cap's exhaustion policy is fixed
BEFORE the run: residual failing claims are DROPPED (removal is always safe
under the extractive invariant) and logged for review. Before the first
round, verdicts are carried across a strip's page remap where the claim
text is unchanged (the double-sweep the frozen tree re-billed).

THE DROP CONVERGES (2026-10-07). The residual drop once ran straight after
the last round's REPAIR, so every claim that round rewrote carried a new key
and no verdict, escaped a drop that only removes failing verdicts, and was
graded PARTIAL by the post-drop audit: the gate held a package on exactly the
claims the policy exists to remove (a client matter, SUPPORTED=300 PARTIAL=2).
Past the cap the loop now alternates drop and audit until the audit is clean,
at most DROP_PASSES times; the last pass drops a widened citation too, since
nothing re-audits after it. Every dropped claim is listed in
`out/<unit>/audit-dropped-claims.json`, `contested` when the repair tier had
judged it needed no change.

A STALE VERDICT DELETES NOTHING (2026-10-07, later the same day). Three
record-true facts left a delivered package because the repair returned each
unchanged, its key did not move, and every round resumed the same PARTIAL until
the cap dropped it. An unchanged repair now marks the claim for a fresh grading
(`claims.pending_reaudit`), and a non-final drop pass defers it until that
grading lands. The gate can then fail only on what
the loop cannot resolve: a claim it could not find in the document, a
wrongly SUPPORTED control, or a claim with no verdict at all.

THE GATE DROPS, IT DOES NOT HOLD (2026-10-23). A live claim still not
SUPPORTED at the final gate (no verdict, or a last verdict that is not
SUPPORTED) used to hold the job for SMD, who could only remove it by hand:
the extractive invariant already says removal is the safe answer. The gate
now removes each such claim in code (no model: the claim span out of the
document, as the round-cap drop does), records it in the dropped-claims file
as ``dropped at the audit gate``, and the review stage lists every one in the
Review Notes file for the attorney (``stages/review_policy.gate_drops``):
nothing leaves silently. What still holds is what removal cannot answer: a
control wrongly SUPPORTED, or a failing claim the gate cannot find in the
document to remove.

Exit 0 only when the final audit reports zero problems AND the coverage gate
passes; 1 when the gate fails (the driver holds); 2 on an INVALID audit
(control failure, no exhibit bytes) or a reconciliation mismatch; 3 at the
double-sweep guard.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from .. import llm
from ..audit import claims as CL, coverage, diag, repair
from ..audit.page_text import exhibit_paths
from ..audit.run import AuditPaths, Round
from ..config import DEFAULT_AUDIT_WORKERS
from .base import StageRun

# Rounds converge steeply and each one is cheap: later rounds re-audit only the
# claims whose text changed, because a repaired claim gets a new key. Live
# 2026-09-23 on a 1,061-claim matter the loop ran 276 flags -> 1, fixing 73
# citations and 167 claims, and then hit the cap with a SINGLE claim still
# carrying a widened citation - which fails the gate, because a live
# SUPPORTED_WIDENED means the citation rewrite was skipped. Three rounds was
# one short of a finished document; the residual after round 3 was 10 claims,
# after round 4 it would have been ~1, so the cap was set just inside the knee
# of the curve rather than past it.
ROUNDS = 5
# Past the cap: drop what is not SUPPORTED, re-audit, repeat. A drop removes a
# failing claim outright, so a later pass only meets a citation the previous
# pass widened (a new span, a new key); the last pass drops those too.
DROP_PASSES = 3
DROPPED_RECORD = "audit-dropped-claims.json"
#: The edit-log action and the dropped-record ``how`` of a claim removed at
#: the final gate (``stages/review_policy.GATE_DROP`` reads the latter).
GATE_ACTION = "drop-gate"
GATE_HOW = "dropped at the audit gate"


def _rekey(sr: StageRun, paths: AuditPaths) -> None:
    """Carry verdicts across the strip's page remap before auditing."""
    remap = diag.page_remap(sr.slug_dir, sr.unit.unit)
    if not remap or not paths.results.is_file():
        return
    from pypdf import PdfReader

    pdfs = exhibit_paths(paths.out)
    npages = {k: len(PdfReader(str(p)).pages) for k, p in pdfs.items()}
    body = CL.body_of(paths.doc.read_text(encoding="utf-8"))
    live = []
    for c in CL.extract_claims(body, set(npages)):
        c["pages"] = CL.parse_pages(c["page_spec"]) or [1]
        live.append(c)
    rows = CL.read_rows(paths.results)
    carried = diag.rekey_rows(paths.results, rows, live, remap, doc_sha=CL.doc_sha_of(body))
    sr.log(f"rekey: carried {carried} verdict(s) across the strip's page remap")


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half, for `medchron rehearse`: the claim count the audit would
    call on, times the firm's per-claim rate. Counted with the audit's OWN
    extractor. From the built chronology when build_doc has run; otherwise an
    UPPER BOUND from the composed entries (pre-filter, pre-strip), labelled as
    such, keeping every exhibit the text cites. This is the cap answer, at $0,
    before a single paid stage past composition has run.
    """
    import re

    from ..audit import claims as claims_mod
    from ..audit.page_text import exhibit_paths

    rd = sr.slug_dir / "runs" / sr.unit.unit
    doc = rd / "final-chronology.md"
    keep = set(exhibit_paths(sr.slug_dir / "out" / sr.unit.unit))
    if doc.is_file():
        body, label = claims_mod.body_of(doc.read_text(encoding="utf-8")), "from the built chronology"
    else:
        names = [
            n
            for n in ("entries_scoped_final.md", "entries_scoped.md", "entries_final.md", "merged.md")
            if (rd / n).is_file()
        ]
        maps = sorted(rd.glob("map-*.md"))
        if names:
            body, label = (rd / names[0]).read_text(encoding="utf-8"), f"UPPER BOUND from {names[0]} (pre-strip)"
        elif maps:
            body = "\n\n".join(m.read_text(encoding="utf-8") for m in maps)
            label = f"UPPER BOUND from {len(maps)} composed chunk(s) (pre-merge, pre-filter, pre-strip)"
        else:
            return ["no composed entries yet (map has not run); audit cost unprojectable"]
        keep = keep or {int(m) for m in re.findall(r"\(Exhibit (\d+)", body)}
    n = len(claims_mod.extract_claims(body, keep))
    rate = float(sr.cfg.usd_per_audit_claim)
    return [f"{n} claims {label} x {rate:.2f} USD/claim = ~{n * rate:.2f} USD"]


def run(sr: StageRun) -> int:
    paths = AuditPaths(sr.slug_dir, sr.unit.unit)
    audit_model = llm.model_for(sr.cfg, "audit")
    repair_model = llm.model_for(sr.cfg, "judgment")
    mode = str(sr.cfg.get("levers", "audit_mode", "image"))
    if mode not in ("image", "text"):
        sr.log(f"levers.audit_mode must be image or text, not {mode!r}")
        return 2
    _rekey(sr, paths)
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)
    sr.log(f"audit: {workers} claim(s) checked at once")

    def audit_round() -> int:
        return Round(sr.doorway, audit_model, paths, sr.log, mode=mode, workers=workers).execute()

    for rnd in range(1, ROUNDS + 1):
        sr.log(f"===== ROUND {rnd}/{ROUNDS}: audit =====")
        rc = audit_round()
        if rc == 0:
            sr.log(f"round {rnd}: audit clean")
            break
        if rc == 2:
            sr.log("audit reported INVALID (control failure or no bytes); not repairing on an invalid audit")
            return 2
        if rc == 3:
            sr.log(
                "audit REFUSED at the double-sweep guard: prior rows for this exact body carry keys no longer produced"
            )
            return 3
        sr.log(f"===== ROUND {rnd}/{ROUNDS}: repair =====")
        if not repair.run(sr.doorway, repair_model, paths, sr.log):
            sr.log("repair reconciliation MISMATCH")
            return 2
    else:
        rc = _converge(sr, paths, repair_model, audit_round)
        if rc is not None:
            return rc
    sr.log("===== final gate =====")
    ok, summary = coverage.check(paths, sr.log)
    if not ok and not summary.get("controls_bad"):
        if _drop_at_gate(sr, paths):
            sr.log("===== final gate, after the gate drop =====")
            ok, summary = coverage.check(paths, sr.log)
    _record_drops(sr, paths)
    return 0 if ok else 1


def _drop_at_gate(sr: StageRun, paths: AuditPaths) -> bool:
    """Every live claim the gate would fail on (no verdict, or a last verdict
    not SUPPORTED) removed from the document in code, each logged to the edit
    log for the dropped-claims record. False, removing nothing, when one of
    them cannot be found in the document (the gate then holds as before) or
    the claim count does not reconcile."""
    full = paths.doc.read_text(encoding="utf-8")
    head, rest = full.split(CL.BODY_START, 1)
    body, tail = rest.split(CL.BODY_END, 1)
    pdfs = set(exhibit_paths(paths.out))
    every = CL.extract_claims(body, pdfs)
    live = {c["key"]: c for c in every}
    latest = CL.latest_real(CL.read_rows(paths.results), set(live))
    failing = [c for k, c in live.items() if (latest.get(k) or {}).get("verdict") != "SUPPORTED"]
    if not failing:
        return False
    spans = CL.claim_spans(body, pdfs)
    found: list[tuple[dict, str]] = []
    for c in failing:
        at = next((s for s in (spans.get(c["key"]), c["claim"] + " " + c["cite"]) if s and s in body), None)
        if at is None:
            sr.log(f"gate drop: a failing claim on Exhibit {c['exhibit']} p.{c['page_spec']} is not in the document")
            return False
        found.append((c, at))
    removed = [0]
    new = body
    for c, at in found:
        new = repair._edit(new, at, "", pdfs, removed)
    if len(CL.extract_claims(new, pdfs)) != len(every) - removed[0]:
        sr.log("gate drop: the claim count does not reconcile; nothing removed")
        return False
    paths.doc.write_text(head + CL.BODY_START + re.sub(r"\n{3,}", "\n\n", new) + CL.BODY_END + tail, encoding="utf-8")
    entries = paths.slug_dir / "runs" / paths.unit / "entries_scoped_final.md"
    if entries.is_file():
        text = entries.read_text(encoding="utf-8")
        for _c, at in found:
            text, _ = repair.replace_in(text, at, "")
        entries.write_text(re.sub(r"\n{3,}", "\n\n", text), encoding="utf-8")
    log = paths.out / "repair-edits.jsonl"
    for c, _at in found:
        r = latest.get(c["key"]) or {}
        CL.append_row(
            log,
            {
                "key": c["key"],
                "action": GATE_ACTION,
                "verdict": r.get("verdict") or "NOT AUDITED",
                "exhibit": c["exhibit"],
                "page_spec": c["page_spec"],
                "note": str(r.get("note") or "")[:300],
                "old": c["claim"],
            },
        )
    sr.log(f"gate drop: {len(found)} claim(s) not finally SUPPORTED removed; each is listed in the review notes")
    return True


def _converge(sr: StageRun, paths: AuditPaths, repair_model: str, audit_round: Callable[[], int]) -> int | None:
    """The round cap's exhaustion policy: drop the residual, re-audit, until
    the audit is clean. None to go on to the gate; 2 on an invalid audit or a
    drop that does not reconcile."""
    for n in range(1, DROP_PASSES + 1):
        final = n == DROP_PASSES
        sr.log(f"===== round cap reached: drop pass {n}/{DROP_PASSES}{' (final)' if final else ''} =====")
        if not repair.run(sr.doorway, repair_model, paths, sr.log, drop_residual=True, final=final):
            sr.log("residual drop reconciliation MISMATCH")
            return 2
        sr.log(f"===== post-drop audit {n}/{DROP_PASSES} =====")
        rc = audit_round()
        if rc in (2, 3):
            sr.log("post-drop audit INVALID or REFUSED at the guard")
            return 2
        if rc == 0:
            sr.log(f"post-drop audit {n}: clean")
            return None
    return None


def _record_drops(sr: StageRun, paths: AuditPaths) -> None:
    """Every claim the loop removed, from the repair edit log, as one record
    kept with the staged package (internal: never uploaded to the matter).
    Rewritten whole each run; the edit log is append-only across resumes, so
    this is always the complete list."""
    rows = CL.read_rows(paths.out / "repair-edits.jsonl")
    drops = [
        {
            "key": r.get("key"),
            "how": {"drop-residual": "dropped at the round cap", GATE_ACTION: GATE_HOW}.get(
                str(r.get("action")), "removed by repair"
            ),
            "verdict": r.get("verdict"),
            "exhibit": r.get("exhibit"),
            "page_spec": r.get("page_spec"),
            "note": r.get("note"),
            "assertions": r.get("assertions"),
            "contested": bool(r.get("contested")),
            "claim": r.get("old"),
        }
        for r in rows
        if (r.get("action") in ("drop-residual", GATE_ACTION) and "old" in r) or r.get("result") == "DROP"
    ]
    paths.out.mkdir(parents=True, exist_ok=True)
    (paths.out / DROPPED_RECORD).write_text(
        json.dumps({"unit": sr.unit.unit, "count": len(drops), "claims": drops}, indent=1), encoding="utf-8"
    )
    contested = sum(1 for d in drops if d["contested"])
    sr.log(
        f"dropped claims recorded: {len(drops)} -> {DROPPED_RECORD}"
        + (f"; {contested} CONTESTED (repair found nothing to remove; review before filing)" if contested else "")
    )
