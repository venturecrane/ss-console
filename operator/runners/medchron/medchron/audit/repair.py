"""Repair the claims the audit could not support. Paid (judgment tier).

Operates on the AUDITED artifact (final-chronology.md) and keeps
entries_scoped_final.md in sync best-effort, so the document the audit read
is the document the repair edits. Edit classes, from the latest verdict per
LIVE claim: SUPPORTED_WIDENED is a citation defect (span rewritten to the
widened window, text untouched); anything else not SUPPORTED is a text
defect, rewritten by REMOVAL or WEAKENING only. Guards, each of which can
fail and says so: an edit whose claim cannot be located verbatim is skipped,
never fuzzily applied; a repair whose citation set changed or that grew past
1.25x+10 words is REJECTED; after all edits the claim count must reconcile.
`drop_residual` is the round-cap policy, fixed before the run: a claim still
failing is DROPPED (removal is always safe under the extractive invariant)
and logged for review. `final` is the last drop pass, after which nothing is
re-audited: a SUPPORTED_WIDENED claim is dropped there too, because a citation
rewritten on that pass would ship unverified.

A claim is located by its EXACT span in the body (`claims.claim_spans`), not
by re-gluing `claim + " " + cite`: a citation on the next line made the glued
form unfindable, and the claim was skipped by repair and drop alike until it
held the package at the gate (live 2026-10-07).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from .. import llm
from ..limits import LimitHold
from . import claims as CL
from .page_text import exhibit_paths
from .run import AuditPaths

CITE = re.compile(r"\(Exhibit \d+(?: - p\. [^)]*)?\)")
SYSTEM = """You correct one sentence-group in a medical chronology that an audit could not support from the cited record pages.

You will receive the CLAIM as written and the specific ASSERTIONS the auditor could not find on those pages.

Correct it by REMOVAL or WEAKENING ONLY:
- Delete an unsupported characterisation, or replace it with what the record actually states as quoted in the auditor's note.
- Delete entirely any assertion about the record set itself (for example that a duplicate copy of a note appears elsewhere). Such statements describe our processing, not the patient's care, and do not belong in the document.
- Never add a fact, a date, a dose, a finding, or a causal statement.
- Never soften a supported clinical fact; leave supported content exactly as it is.

Keep the citation EXACTLY as it appears, in the same position at the end of its paragraph. Keep the subsection headings and the paragraph structure. Do not add an em dash.

Output ONLY the corrected text, no commentary. If removing the unsupported part leaves a paragraph with no content, output the single word DROP."""


def compress(pages: list[int]) -> str:
    out: list[list[int]] = []
    run = [pages[0]]
    for p in pages[1:]:
        if p == run[-1] + 1:
            run.append(p)
        else:
            out.append(run)
            run = [p]
    out.append(run)
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in out)


def widen_cite(old_cite: str, widened: list[int]) -> str:
    """Rewrite the page span inside a citation, preserving a trailing
    'machine transcription' marker (page lists may contain commas)."""
    if "p." in old_cite:
        return re.sub(
            r"p\.\s*(?:[^,)]|,(?!\s*machine))*(?=\)|,\s*machine)", f"p. {compress(widened)}", old_cite, count=1
        )
    return old_cite[:-1] + f" - p. {compress(widened)})"


def replace_in(text: str, anchor: str, replacement: str) -> tuple[str, bool]:
    if anchor not in text:
        return text, False
    if replacement == "" and anchor + "\n" in text:
        return text.replace(anchor + "\n", "", 1), True
    return text.replace(anchor, replacement, 1), True


def ask_repair(doorway: llm.Doorway, model: str, r: dict[str, Any], c: dict[str, Any]) -> tuple[str | None, str]:
    """(corrected text, "") or (None, why) for one failing claim. A limit hold
    is not one claim's failure: it leaves, so the stage holds and nothing the
    hold stopped is later dropped as residual (review 2026-10-06, N10)."""
    problems = (r.get("unsupported_assertions") or []) + (r.get("contradictions") or [])
    if r["verdict"] == "PAGE_OUT_OF_RANGE":
        problems = problems or [f"cited pages {r.get('bad_pages')} do not exist in that exhibit"]
    if not problems:
        problems = [r.get("note") or "assertion not found on cited pages"]
    payload = (
        "CLAIM:\n"
        + c["claim"]
        + "\n\nASSERTIONS NOT FOUND ON THE CITED PAGES:\n"
        + "\n".join(f"- {p}" for p in problems)
    )
    try:
        return doorway.call(
            "repair",
            model=model,
            max_tokens=2000,
            system=SYSTEM,
            timeout=180.0,
            messages=[{"role": "user", "content": payload}],
            custom_id=f"repair-{r['key']}",
        ).text.strip(), ""
    except LimitHold:
        raise
    except Exception as exc:  # noqa: BLE001 - one claim's failure is one log row
        return None, str(exc)[:150]


def _rejection(new: str, anchor: str, claim: str) -> str | None:
    """Why a repaired sentence may not replace the claim, or None. A repair
    weakens or removes; it never moves a citation or grows the claim."""
    cites_in_new = CITE.findall(new)
    if cites_in_new and sorted(cites_in_new) != sorted(CITE.findall(anchor)):
        return "citations changed"
    if len(new.split()) > len(claim.split()) * 1.25 + 10:
        return "expanded"
    return None


def _residual_drop_row(r: dict[str, Any], c: dict[str, Any], contested: bool) -> dict[str, Any]:
    """The edit-log row for a claim dropped at the round cap (the dropped-claims
    record reads it). `contested` marks a claim the repair tier, shown the same
    findings, judged to need no change: the audit and the repair disagreed and
    the drop settled it, which a reviewer must be able to see."""
    return {
        "key": r["key"],
        "action": "drop-residual",
        "verdict": r["verdict"],
        "exhibit": c["exhibit"],
        "page_spec": c["page_spec"],
        "note": str(r.get("note") or "")[:300],
        "assertions": [str(a)[:200] for a in (r.get("unsupported_assertions") or []) + (r.get("contradictions") or [])][
            :5
        ],
        "contested": contested,
        "old": c["claim"],
    }


def _unchanged(new: str, anchor: str, claim: str) -> bool:
    """The repair returned the claim as written (with or without its citation)."""
    flat = " ".join(new.split())
    return flat in (" ".join(claim.split()), " ".join(anchor.split()))


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _noop_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {r["key"] for r in rows if str(r.get("result", "")).startswith("NOOP") and "key" in r}


@dataclass
class _Ctx:
    """What settling the failing claims needs from `run`: the model, the
    document's edit and lookup closures, and the policy for this pass."""

    doorway: llm.Doorway
    model: str
    paths: AuditPaths
    live: dict[str, dict[str, Any]]
    locate: Callable[[dict[str, Any]], str | None]
    apply: Callable[[str, str], None]
    logrow: Callable[..., None]
    log: Callable[[str], None]
    drop_residual: bool
    stale: set[str]
    contested: set[str]
    pause: float


def _mark_reaudit(paths: AuditPaths, key: str) -> None:
    """The repair tier found nothing to remove. That is a second reading
    disagreeing with the verdict, not a repair: the key does not move, so
    without the marker the next round resumes the same verdict and the cap
    deletes a claim nobody re-read (2026-10-07)."""
    CL.append_row(
        paths.results, {"key": key, "kind": CL.REAUDIT, "why": "repair returned the claim unchanged", "ts": _ts()}
    )


def _settle_failing(failing: list[dict[str, Any]], x: _Ctx) -> dict[str, int]:
    """Repair, or (past the cap) drop, every claim not finally SUPPORTED."""
    n: dict[str, int] = {k: 0 for k in ("repaired", "dropped", "rejected", "skipped", "noop")}
    for i, r in enumerate(failing, 1):
        c = x.live[r["key"]]
        anchor = x.locate(c)
        if anchor is None:
            x.logrow(key=r["key"], action="repair", result="SKIP: claim not located")
            n["skipped"] += 1
            continue
        if x.drop_residual and r["key"] in x.stale:
            x.logrow(key=r["key"], action="drop-residual", result="DEFER: re-audit pending")
            n["skipped"] += 1
            continue
        if x.drop_residual:
            x.apply(anchor, "")
            x.logrow(**_residual_drop_row(r, c, r["key"] in x.contested))
            n["dropped"] += 1
            continue
        new, err = ask_repair(x.doorway, x.model, r, c)
        if new is None:
            x.logrow(key=r["key"], action="repair", result=f"ERROR: {err}")
            n["skipped"] += 1
        elif new == "DROP":
            x.apply(anchor, "")
            x.logrow(key=r["key"], action="repair", result="DROP", old=c["claim"][:300])
            n["dropped"] += 1
        elif _unchanged(new, anchor, c["claim"]):
            x.logrow(key=r["key"], action="repair", result="NOOP: unchanged; re-audit")
            _mark_reaudit(x.paths, r["key"])
            n["noop"] += 1
        elif why := _rejection(new, anchor, c["claim"]):
            x.logrow(key=r["key"], action="repair", result=f"REJECT: {why}")
            n["rejected"] += 1
        else:
            x.apply(anchor, new if CITE.findall(new) else new + anchor[len(c["claim"]) :])
            x.logrow(key=r["key"], action="repair", old=c["claim"][:300], new=new[:300])
            n["repaired"] += 1
            x.log(f"  [{i}/{len(failing)}] repaired Ex{r['exhibit']} p.{r.get('page_spec')}")
            if x.pause:
                time.sleep(x.pause)
    return n


def run(
    doorway: llm.Doorway,
    model: str,
    paths: AuditPaths,
    log: Callable[[str], None],
    *,
    drop_residual: bool = False,
    final: bool = False,
    pause: float = 0.2,
) -> bool:
    """False when the claim count fails to reconcile after the edits."""
    doc_path = paths.doc
    entries_path = paths.slug_dir / "runs" / paths.unit / "entries_scoped_final.md"
    full = doc_path.read_text(encoding="utf-8")
    head, rest = full.split(CL.BODY_START, 1)
    body, tail = rest.split(CL.BODY_END, 1)
    entries = entries_path.read_text(encoding="utf-8") if entries_path.is_file() else None
    pdfs = set(exhibit_paths(paths.out))
    live = {c["key"]: c for c in CL.extract_claims(body, pdfs)}
    spans = CL.claim_spans(body, pdfs)
    n_orig = len(live)
    rows = CL.read_rows(paths.results)
    latest = CL.latest_real(rows, set(live))
    # A stale verdict is not a reason to delete a claim: one the last repair
    # left unchanged is graded again first (the post-drop audit does it), and
    # only the final pass, after which nothing is re-audited, drops it anyway.
    stale = set() if final else CL.pending_reaudit(rows)
    widened_ok = not (drop_residual and final)
    cite_fix = [r for r in latest.values() if r["verdict"] == "SUPPORTED_WIDENED" and widened_ok]
    # Anything not finally SUPPORTED is failing; enumerating failure verdicts
    # once left an unlisted one neither repaired nor dropped.
    ok_verdicts = ("SUPPORTED", "SUPPORTED_WIDENED") if widened_ok else ("SUPPORTED",)
    failing = [r for r in latest.values() if r["verdict"] not in ok_verdicts]
    log(
        f"{paths.unit}: {len(latest)}/{n_orig} live claims with verdicts; cite-fix {len(cite_fix)}, "
        f"{'DROP' if drop_residual else 'repair'} {len(failing)}"
    )
    edits_log = paths.out / "repair-edits.jsonl"
    contested = _noop_keys(CL.read_rows(edits_log))
    fixed = skipped = 0

    def logrow(**kw: Any) -> None:
        CL.append_row(edits_log, kw)

    def apply(anchor: str, replacement: str) -> None:
        nonlocal body, entries
        body, _ = replace_in(body, anchor, replacement)
        if entries is not None:
            entries, _ = replace_in(entries, anchor, replacement)

    def locate(c: dict[str, Any]) -> str | None:
        for cand in (spans.get(c["key"]), c["claim"] + " " + c["cite"], c["claim"] + c["cite"]):
            if cand and cand in body:
                return cand
        return None

    for r in cite_fix:
        c = live[r["key"]]
        widened = r.get("widened") or []
        anchor = locate(c)
        if not widened or anchor is None:
            logrow(
                key=r["key"],
                action="cite-fix",
                result="SKIP: " + ("no widened pages recorded" if not widened else "anchor not found"),
            )
            skipped += 1
            continue
        new_cite = widen_cite(c["cite"], widened)
        apply(anchor, c["claim"] + " " + new_cite)
        logrow(key=r["key"], action="cite-fix", old=c["cite"], new=new_cite)
        fixed += 1

    n = _settle_failing(
        failing,
        _Ctx(doorway, model, paths, live, locate, apply, logrow, log, drop_residual, stale, contested, pause),
    )
    dropped, skipped = n["dropped"], skipped + n["skipped"]

    body = re.sub(r"\n{3,}", "\n\n", body)
    doc_path.write_text(head + CL.BODY_START + body + CL.BODY_END + tail, encoding="utf-8")
    if entries is not None:
        entries_path.write_text(re.sub(r"\n{3,}", "\n\n", entries), encoding="utf-8")
    n_new = len(CL.extract_claims(body, pdfs))
    ok = n_new == n_orig - dropped
    log(
        f"APPLIED: cite-fix {fixed}, repaired {n['repaired']}, unchanged->re-audit {n['noop']}, "
        f"dropped {dropped}, rejected {n['rejected']}, skipped {skipped}"
    )
    log(
        f"claims before {n_orig}, after {n_new} (expected {n_orig - dropped}) -> {'RECONCILES' if ok else '!! MISMATCH'}"
    )
    return ok
