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
"""

from __future__ import annotations

import json
from pathlib import Path

from ..update import pageid as PI, pageid_io as PG
from .base import StageRun
from .update_base import plan, update_dir
from .update_select import _candidates, delivered_read


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
    rows, _ = _candidates(sr, {e["sha256"]: e["name"] for e in read})
    delivered = []
    for e in read:
        s = _sigs(sr.slug_dir / "delivered" / e["name"], sr.log)
        if s is not None:
            delivered.append((int(e["exhibit"]), s))
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
    total = sum(len(v) for v in skip.values())
    sr.log(
        f"update_pages: {sum(len(s) for _, s in new)} new page(s) against {sum(len(s) for _, s in delivered)} "
        f"delivered; {total} pixel-equal to a page read elsewhere and not transcribed; "
        f"{len(ident.extracts)} re-filed cop(ies) or extract(s)"
    )
    return 0
