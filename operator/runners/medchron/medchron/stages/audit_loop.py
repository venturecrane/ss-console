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

Exit 0 only when the final audit reports zero problems AND the coverage gate
passes; 1 when the gate fails (the driver holds); 2 on an INVALID audit
(control failure, no exhibit bytes) or a reconciliation mismatch; 3 at the
double-sweep guard.
"""

from __future__ import annotations

from .. import llm
from ..audit import claims as CL, coverage, diag, repair
from ..audit.page_text import exhibit_paths
from ..audit.run import AuditPaths, Round
from .base import StageRun

ROUNDS = 3


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

    def audit_round() -> int:
        return Round(sr.doorway, audit_model, paths, sr.log, mode=mode).execute()

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
        sr.log("===== round cap reached: dropping residual =====")
        if not repair.run(sr.doorway, repair_model, paths, sr.log, drop_residual=True):
            sr.log("residual drop reconciliation MISMATCH")
            return 2
        sr.log("===== post-drop audit =====")
        if audit_round() in (2, 3):
            sr.log("post-drop audit INVALID or REFUSED at the guard")
            return 2
    sr.log("===== final gate =====")
    ok, _summary = coverage.check(paths, sr.log)
    return 0 if ok else 1
