"""`update_pages`: page identity by picture, BEFORE the vision pass. $0.

Renders every page of every delivered exhibit the delivery read and of every
candidate new document (``update_select``'s own candidates, in pull order)
and settles, by picture alone (``update/pageid``), which new pages are
already read in a delivered exhibit, which repeat a page of an earlier new
document, and which new documents are re-filed copies or extracts.

Writes ``update/page_identity.json`` for ``update_select`` and
``vision_skip.json`` for the vision pass: a page pixel-equal to one already
read, and every page of a re-filed copy, is never transcribed (a near match
is transcribed and confirmed by its words in ``update_select``). A re-filed scan was once
transcribed, composed and filed as five new exhibits (2026-10-14).

A file this cannot open settles nothing: its pages go to vision as before.

It writes the page record's first rows (``runs/<unit>/pages.jsonl``,
``medchron/pages_record``): each candidate page's picture finding and
whether it carries ink. First, a file the other-person RULE withheld on an
earlier run is decided again (``identity_redecide``), before any candidate
picture is computed, so a file is either a candidate or not one, exactly as
on main (2026-11-17: 24 of a withheld file's 34 pages were the client's
delivered exhibit).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..medchron import pages_record as PR
from ..medchron.update import pageid as PI, pageid_io as PG
from . import identity_pages, page_rows
from .identity_redecide import redecide
from .base import StageRun, read_jsonl
from .update_base import plan, update_dir
from .update_select import REDECIDED_FILE, _candidates, delivered_read


def _sigs(path: Path, log) -> list[PI.Sig] | None:
    try:
        return PI.signatures(path)
    except Exception as exc:  # noqa: BLE001 - an unopenable file settles nothing; vision reports it
        log(f"update_pages: {path.name[:40]} could not be rendered ({str(exc)[:120]}); its pages go to vision")
        return None


def run(sr: StageRun) -> int:
    p = plan(sr)
    if not p:
        sr.log("update_pages: no update plan (update_base has not run)")
        return 1
    ud = update_dir(sr)
    read = delivered_read(sr, p)
    listed = {e["exhibit"] for e in read}
    firm_sha = {e["sha256"] for e in p["exhibits"] if e["exhibit"] not in listed}
    delivered = []
    for e in read:
        s = _sigs(sr.slug_dir / "delivered" / e["name"], sr.log)
        if s is not None:
            delivered.append((int(e["exhibit"]), s))
    # A file the other-person RULE withheld on an earlier run is decided again
    # BEFORE any candidate's picture is computed: cleared, it is a candidate
    # like any other; kept, it is not one, exactly as on main.
    (ud / REDECIDED_FILE).unlink(missing_ok=True)  # an earlier attempt's record never outlives a failed one
    try:
        redecide(sr, delivered, lambda path: _sigs(path, sr.log), REDECIDED_FILE)
    except Exception as exc:  # noqa: BLE001 - a re-decision that fails leaves every withhold standing, as on main
        sr.log(f"update_pages: withholds not re-decided ({type(exc).__name__}: {str(exc)[:160]}); they stand")
    rows, _ = _candidates(sr, {e["sha256"]: e["name"] for e in read})
    new = []
    for r in rows:
        s = _sigs(Path(r["path"]), sr.log)
        if s is not None:
            new.append((str(r["id"]), s))
    firm = {str(r["id"]) for r in rows if (r.get("sha256") or "") in firm_sha}
    ident = PI.identify(delivered, new, firm)
    (ud / PG.NAME).write_text(json.dumps(PI.to_json(ident), indent=1), encoding="utf-8")
    skip = ident.skipped({d: len(s) for d, s in new})
    (sr.slug_dir / "vision_skip.json").write_text(json.dumps(skip, indent=1), encoding="utf-8")
    # The page record's first rows: each candidate page's picture finding and ink (``medchron/pages_record``);
    # a file that could not be rendered is counted whole as to be read.
    sha = {str(r["id"]): str(r.get("sha256") or "") for r in rows}
    rendered = {d for d, _ in new}
    pages = {str(x.get("id")): int(x.get("pages") or 0) for x in read_jsonl(sr.slug_dir / "extracted.jsonl")}
    unrendered = {str(r["id"]): pages.get(str(r["id"]), 0) for r in rows if str(r["id"]) not in rendered}
    try:  # a record: it never decides or stops anything
        PR.write(
            sr.slug_dir / "runs" / sr.unit.unit,
            page_rows.candidate_rows([(d, len(s), [x.inked for x in s]) for d, s in new], PG.load(ud), sha, unrendered),
        )
    except Exception as exc:  # noqa: BLE001 - the page record is evidence; its failure is logged, never the run's
        sr.log(f"update_pages: page record not written ({type(exc).__name__}: {str(exc)[:160]})")
    # An other-person finding on a scan's OCR layer is read on the page before it withholds.
    identity_pages.queue(sr, rows, ud)
    total = sum(len(v) for v in skip.values())
    sr.log(
        f"update_pages: {sum(len(s) for _, s in new)} new page(s) against {sum(len(s) for _, s in delivered)} "
        f"delivered; {total} pixel-equal to a page read elsewhere and not transcribed; "
        f"{len(ident.extracts)} re-filed cop(ies) or extract(s)"
    )
    return 0
