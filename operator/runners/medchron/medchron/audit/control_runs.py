"""The audit's controls, run: one control per control_every-th claim against
a page of a different institution, and the once-only re-test of a SUPPORTED
control against yet another institution (``controls`` decides what the rows
mean). Split from ``run`` (2026-10-24) when the re-test was added.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..limits import LimitHold
from . import anchors as AN, claims as CL, controls, retired, verify as VF
from .page_text import PageIndex
from .render import img_block, render

ERROR = {"verdict": "ERROR", "unsupported_assertions": [], "contradictions": []}
EXHIBIT_PROVIDER = re.compile(r"^Exhibit \d+ - (.+?) - \d{2}-\d{2}-\d{4}")


def institution(path: Path) -> str:
    """The first two words of an exhibit's provider, from its file name: the
    part two exhibits of one institution share ("example va" for the
    medical center, its dental clinic and its pharmacy)."""
    m = EXHIBIT_PROVIDER.match(path.name)
    return " ".join((m.group(1) if m else path.stem).lower().split()[:2])


def control_exhibit(claim_exhibit: int, pdfs: dict[int, Path]) -> int | None:
    """The exhibit a control is verified against. A control is a control only
    when its page plausibly cannot carry the claim: a VA pharmacy claim shown
    the VA medical center exhibit's first pages met the same VA medication list
    and was SUPPORTED, and that one accepted control invalidated a round of
    1,998 verdicts that had discriminated 278 times (live 2026-09-16). So the
    other exhibit is the first from a DIFFERENT institution; only a matter
    with a single institution falls back to any other exhibit."""
    if claim_exhibit not in pdfs:
        return next((o for o in sorted(pdfs) if o != claim_exhibit), None)
    mine = institution(pdfs[claim_exhibit])
    others = [o for o in sorted(pdfs) if o != claim_exhibit]
    return next((o for o in others if institution(pdfs[o]) != mine), others[0] if others else None)


def retest_exhibit(claim_exhibit: int, used: int, pdfs: dict[int, Path]) -> int | None:
    """The exhibit a SUPPORTED control is re-tested against: the first, in
    exhibit order, of an institution that is neither the claim's nor the
    first control's. None when the matter has no third institution."""
    seen = {institution(pdfs[e]) for e in (claim_exhibit, used) if e in pdfs}
    return next((o for o in sorted(pdfs) if o not in (claim_exhibit, used) and institution(pdfs[o]) not in seen), None)


def control_for(
    rnd: Any, c: dict[str, Any], pdfs: dict[int, Path], index: PageIndex | None, doc_sha: str, ts: str
) -> None:
    with rnd._lock:
        rnd.counter["n"] += 1
        do_ctl = rnd.counter["n"] % rnd.control_every == 0
    if not do_ctl or (other := control_exhibit(int(c["exhibit"]), pdfs)) is None:
        return
    cv, kind, extra = verify_control(rnd, c, other, pdfs, index if c.get("window") else None)
    CL.append_row(
        rnd.paths.results,
        {
            "key": c["key"] + f"-ctl{other}",
            "kind": kind,
            "exhibit": c["exhibit"],
            "page_spec": "1",
            "pages": [1],
            "claim": c["claim"][:200],
            **cv,
            **extra,
            "doc_sha": doc_sha,
            "ts": ts,
        },
    )
    rnd.log(f"  -- CONTROL Ex{c['exhibit']} claim vs Ex{other}: {cv.get('verdict')}")


def verify_control(
    rnd: Any, c: dict[str, Any], other: int, pdfs: dict[int, Path], index: PageIndex | None, kind: str = "control"
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """(verdict, row kind, extra row fields) for claim `c` against
    exhibit `other`'s first page(s): its native text window when `index`
    is given and the pages are eligible, else page 1's image. A SUPPORTED
    verdict whose page carries the claim (its text, or its vision
    transcript when it is a scan) is retired by rule."""
    text_ctl = None
    if index is not None:
        pages = [p for p in (1, 2, 3) if p <= (index.npages(other) or 0) and index.eligible(other, p)]
        if pages:
            text_ctl = (pages, index.window_text(other, pages))
    try:
        if text_ctl:
            pages, block = text_ctl
            cv = VF.verify_text(
                rnd.doorway,
                rnd.model,
                c["claim"],
                block,
                pages,
                c.get("anchors", []),
                f"Exhibit {other} p.1-3",
                custom_id=c["key"] + f"-ctl{other}",
            )
            kind, extra = f"{kind}-text(vs Ex{other})", {"mode": "text", "window": pages}
        else:
            ci = render(pdfs[other], 1, rnd.paths.pages, f"ctl{other}")
            cv = VF.verify_image(
                rnd.doorway,
                rnd.model,
                c["claim"],
                [img_block(ci)] if ci else [],
                f"Exhibit {other} p.1",
                custom_id=c["key"] + f"-ctl{other}",
            )
            kind, extra = f"{kind}(vs Ex{other})", {"mode": "image"}
    except LimitHold:
        raise  # a limit stops the round; an ERROR row here would read as audited (N10)
    except Exception as exc:  # noqa: BLE001 - a control verification raising is recorded as an ERROR verdict for that claim; the audit continues
        cv, kind, extra = {**ERROR, "note": str(exc)[:200]}, f"{kind}(vs Ex{other})", {"mode": rnd.mode}
    cv.pop("supporting_pages", None)
    if cv.get("verdict") == "SUPPORTED":
        block = text_ctl[1] if text_ctl else None
        slug_dir, unit = rnd.paths.slug_dir, rnd.paths.unit
        extra = {
            **extra,
            **retired.retire_if_carried(
                rnd.paths.out,
                c,
                other,
                block,
                pdfs[other],
                rnd.log,
                transcript=lambda: retired.transcript_text(slug_dir, unit, other, 1),
            ),
        }
    return cv, kind, extra


def retest_controls(
    rnd: Any, claims: dict[str, dict[str, Any]], pdfs: dict[int, Path], index: PageIndex | None, doc_sha: str, ts: str
) -> None:
    """Every SUPPORTED control not retired, re-tested ONCE against the
    next exhibit of yet another institution (``controls``). An unsupported
    re-test (or one whose page carries the claim) shows the verifier
    discriminates: the first control was badly chosen and is retired by
    rule. A re-test SUPPORTED again leaves the control standing, counted
    against the round's threshold. Resumable: a control with a re-test row
    on file is never re-tested."""
    rows = CL.read_rows(rnd.paths.results)
    gone = retired.keys(rnd.paths.out)
    done = {str(r["retest_of"]) for r in rows if r.get("retest_of")}
    for r in controls.originals(rows):
        key = str(r.get("key") or "")
        if r.get("verdict") != "SUPPORTED" or key in gone or key in done or "-ctl" not in key:
            continue
        base, _, used = key.rpartition("-ctl")
        nxt = retest_exhibit(int(r["exhibit"]), int(used) if used.isdigit() else -1, pdfs)
        if nxt is None:
            rnd.log(f"  -- CONTROL {key} SUPPORTED: no other institution to re-test against; it stands")
            continue
        c = claims.get(base) or {"key": base, "claim": str(r.get("claim") or ""), "exhibit": r["exhibit"]}
        c.setdefault("anchors", AN.find_anchors(c["claim"]))
        cv, kind, extra = verify_control(rnd, c, nxt, pdfs, index, kind="control-retest")
        CL.append_row(
            rnd.paths.results,
            {
                "key": base + f"-ctl{nxt}",
                "kind": kind,
                "retest_of": key,
                "exhibit": r["exhibit"],
                "page_spec": "1",
                "pages": [1],
                "claim": c["claim"][:200],
                **cv,
                **extra,
                "doc_sha": doc_sha,
                "ts": ts,
            },
        )
        verdict = cv.get("verdict")
        if verdict in controls.CLEARS or extra.get("retired"):
            retired.retire(
                rnd.paths.out,
                key,
                f"re-tested against Exhibit {nxt}, another institution: {verdict}"
                + (" and that page carries the claim" if extra.get("retired") else "")
                + "; the verifier discriminates, the first control page was badly chosen: retired by rule",
                source=f"control re-test vs Exhibit {nxt}",
            )
            rnd.log(f"  -- CONTROL {key} re-tested vs Ex{nxt}: {verdict}; the first control retired by rule")
        else:
            rnd.log(f"  !! CONTROL {key} re-tested vs Ex{nxt}: {verdict}; SUPPORTED twice, it stands")
