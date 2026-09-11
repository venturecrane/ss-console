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
and logged for review.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from .. import llm
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


def run(
    doorway: llm.Doorway,
    model: str,
    paths: AuditPaths,
    log: Callable[[str], None],
    *,
    drop_residual: bool = False,
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
    n_orig = len(live)
    latest = CL.latest_real(CL.read_rows(paths.results), set(live))
    cite_fix = [r for r in latest.values() if r["verdict"] == "SUPPORTED_WIDENED"]
    # Anything not finally SUPPORTED is failing; enumerating failure verdicts
    # once left an unlisted one neither repaired nor dropped.
    failing = [r for r in latest.values() if r["verdict"] not in ("SUPPORTED", "SUPPORTED_WIDENED")]
    log(
        f"{paths.unit}: {len(latest)}/{n_orig} live claims with verdicts; cite-fix {len(cite_fix)}, "
        f"{'DROP' if drop_residual else 'repair'} {len(failing)}"
    )
    edits_log = paths.out / "repair-edits.jsonl"
    fixed = repaired = dropped = rejected = skipped = 0

    def logrow(**kw: Any) -> None:
        CL.append_row(edits_log, kw)

    def apply(anchor: str, replacement: str) -> None:
        nonlocal body, entries
        body, _ = replace_in(body, anchor, replacement)
        if entries is not None:
            entries, _ = replace_in(entries, anchor, replacement)

    def locate(c: dict[str, Any]) -> str | None:
        for cand in (c["claim"] + " " + c["cite"], c["claim"] + c["cite"]):
            if cand in body:
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

    for i, r in enumerate(failing, 1):
        c = live[r["key"]]
        anchor = locate(c)
        if anchor is None:
            logrow(key=r["key"], action="repair", result="SKIP: claim not located")
            skipped += 1
            continue
        if drop_residual:
            apply(anchor, "")
            logrow(key=r["key"], action="drop-residual", verdict=r["verdict"], old=c["claim"][:300])
            dropped += 1
            continue
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
            new = doorway.call(
                "repair",
                model=model,
                max_tokens=2000,
                system=SYSTEM,
                timeout=180.0,
                messages=[{"role": "user", "content": payload}],
                custom_id=f"repair-{r['key']}",
            ).text.strip()
        except Exception as exc:  # noqa: BLE001 - one claim's failure is one log row
            logrow(key=r["key"], action="repair", result=f"ERROR: {str(exc)[:150]}")
            skipped += 1
            continue
        if new == "DROP":
            apply(anchor, "")
            logrow(key=r["key"], action="repair", result="DROP", old=c["claim"][:300])
            dropped += 1
            continue
        cites_in_new = CITE.findall(new)
        if cites_in_new and sorted(cites_in_new) != sorted(CITE.findall(anchor)):
            logrow(key=r["key"], action="repair", result="REJECT: citations changed")
            rejected += 1
            continue
        if len(new.split()) > len(c["claim"].split()) * 1.25 + 10:
            logrow(key=r["key"], action="repair", result="REJECT: expanded")
            rejected += 1
            continue
        apply(anchor, new if cites_in_new else new + " " + c["cite"])
        logrow(key=r["key"], action="repair", old=c["claim"][:300], new=new[:300])
        repaired += 1
        log(f"  [{i}/{len(failing)}] repaired Ex{r['exhibit']} p.{r.get('page_spec')}")
        if pause:
            time.sleep(pause)

    body = re.sub(r"\n{3,}", "\n\n", body)
    doc_path.write_text(head + CL.BODY_START + body + CL.BODY_END + tail, encoding="utf-8")
    if entries is not None:
        entries_path.write_text(re.sub(r"\n{3,}", "\n\n", entries), encoding="utf-8")
    n_new = len(CL.extract_claims(body, pdfs))
    ok = n_new == n_orig - dropped
    log(f"APPLIED: cite-fix {fixed}, repaired {repaired}, dropped {dropped}, rejected {rejected}, skipped {skipped}")
    log(
        f"claims before {n_orig}, after {n_new} (expected {n_orig - dropped}) -> {'RECONCILES' if ok else '!! MISMATCH'}"
    )
    return ok
