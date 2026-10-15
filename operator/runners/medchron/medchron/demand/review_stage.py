"""The ``review`` stage of a demand job: after the render, before the filing.

Split from ``run.py`` at the module-size ceiling; the checks and prompts live
in ``review.py``. The stage reviews the files exactly as the manifest would
file them, records ``review`` in the run's state file only once it passed or
was released (so a resume after a hold never pays for an earlier stage, and a
resume after delivery never pays for the review), then writes the review's
findings into the deliverable a person reads first (the attorney notes, else
the coverage report, else the gap audit) and refreshes the manifest. Nothing
has been sent at that point: the upload starts only after this stage is done.

``proof`` is the other end: after the upload, the read-back by file id, size
and sha256 and the block the daemon requires on a delivered verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..proof import checks, readback, review as R
from . import deliver, review as rv
from .outcome import DemandFailed

FINAL = "draft-final.md"
DELIVERED_MD = "delivered-md.json"
SHORT = "the read-back is short after its retries; the files may still be materializing"


def proof(run: Any, rec: dict[str, Any], manifest: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    """The proof block for a delivered verdict: the read-back proven, and what
    the review decided (its cents are added once the run's spend is final).
    A mismatch is ``readback_mismatch`` (failed; SMD paged); a lag is short
    (failed, resumable; the resume reads again, never resends)."""
    try:
        out = deliver.prove(run.data, run.seat, run.job.file_to_id, rec, manifest, run.log, **kw)
    except readback.ReadbackMismatch as exc:
        raise DemandFailed(f"readback_mismatch: {exc.name}: {exc.why}") from None
    except readback.ReadbackShort:
        raise RuntimeError(SHORT) from None
    st = run._state().get("review") or {}
    out.update(review=st.get("review"), review_model=st.get("model"))
    return out


def keep_md(data: Path, role: str, md: str) -> None:
    """The markdown each deliverable was rendered from, so the review's
    findings can be added to it and the file rendered again before filing."""
    p = data / DELIVERED_MD
    cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    cur[role] = md
    tmp = data / f".{DELIVERED_MD}.tmp"
    tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    tmp.replace(p)


def _manifest(data: Path) -> list[dict[str, Any]]:
    return json.loads((deliver.out_dir(data) / "upload_manifest.json").read_text(encoding="utf-8"))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _coverage(run: Any, texts: dict[str, str], model: str, key: str) -> list[R.Finding]:
    code = rv.wording(texts)
    report = texts.get("coverage_report", "")
    p = run.data / "premise.json"
    premise = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    ex = run.data / "extracted.jsonl"
    extracted = [json.loads(ln) for ln in _read(ex).splitlines() if ln.strip()]
    user = rv.coverage_user(report, premise, rv.cited_sources(premise, extracted, report), code)
    return code + rv.model_review(run.doorway, model, rv.COVERAGE_PROMPT, user, key, rv.HOLD_COVERAGE)


def _demand(run: Any, texts: dict[str, str], model: str, key: str) -> list[R.Finding]:
    facts = run._json("facts.json")
    letter_md = _read(run.data / FINAL) if run.job.wants("demand") else None
    digest = _read(run.data / "digest.md")
    premise = run._json("premise.json") if (run.data / "premise.json").is_file() else {}
    premise_facts = [str(x) for x in premise.get("premise_facts") or []]
    sources = "\n".join([run._corpus_text(), digest, json.dumps(facts), run.job.request_text, "\n".join(premise_facts)])
    computed = rv.computed_figures(facts, letter_md or "")
    drafted = next(iter(checks.dates(run.long_date)), None)
    wanted = [d for d in ("demand", "gap_audit") if run.job.wants(d)]
    code = rv.demand_code(
        texts,
        wanted=wanted,
        sources=sources,
        computed=computed,
        facts=facts,
        letter_md=letter_md or None,
        drafted=drafted,
        front_keys=deliver.front_keys(run.firm),
    )
    system = rv.DEMAND_PROMPT + "\n\n---\n\n## THE RECORD DIGEST (every cite in it is the record)\n\n" + digest
    user = rv.demand_user(
        run.job.request_text,
        run._matter_fields(),
        premise_facts,
        computed,
        texts,
        code,
        signature=rv.signature_block(run.firm),
    )
    return code + rv.model_review(run.doorway, model, system, user, key, rv.HOLD_DEMAND)


def _fresh(run: Any, coverage: bool) -> tuple[list[R.Finding], str]:
    texts = rv.delivered(_manifest(run.data))
    model = rv.model_for(run.firm)
    key = run.job.job_id[-6:]
    return (_coverage if coverage else _demand)(run, texts, model, key), model


def _with_findings(run: Any, rec: dict[str, Any]) -> None:
    """The review's findings into the deliverable a person reads first,
    rendered again from its own markdown, and the manifest refreshed."""
    section = R.notes_section(rec)
    p = run.data / DELIVERED_MD
    if not section or not p.is_file():
        return
    mds = json.loads(p.read_text(encoding="utf-8"))
    manifest = _manifest(run.data)
    by_role = {str(m.get("role")): m for m in manifest}
    role = next((r for r in rv.NOTES_ROLES if r in mds and r in by_role), None)
    if role is None:
        return
    m = by_role[role]
    md = str(mds[role]).rstrip("\n") + "\n" + "\n".join(section) + "\n"
    out = deliver.out_dir(run.data)
    deliver.render_plain(md, out, str(m["name"]), run._author)
    deliver.write_manifest(out, str(m["folder"]), [(str(e["role"]), Path(e["local_path"])) for e in manifest])


def run_stage(run: Any) -> None:
    """Review, hold or pass; never after anything was sent (the stage is
    recorded done before the upload starts)."""
    if run._is_done("review"):
        return
    coverage = bool(run._state().get("render", {}).get("coverage"))
    status, rec = rv.decide(run.data, run.job.job_dir, lambda: _fresh(run, coverage), lane="demand")
    run.log(f"  review ({'coverage report' if coverage else 'demand package'}): {rv.summary(rec)}: {status}")
    if status == "held":
        raise rv.ReviewHeld(R.held_reason(rec))
    _with_findings(run, rec)
    run._done(
        "review",
        review=status,
        model=str(rec.get("model") or ""),
        must_fix=rec["must_fix"],
        should_fix=rec["should_fix"],
        notes=rec["notes"],
    )


__all__ = ["DELIVERED_MD", "SHORT", "keep_md", "proof", "run_stage"]
