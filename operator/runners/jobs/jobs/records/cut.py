"""The records job's cut stage: each located range becomes one attachment,
and a file the record named goes whole and exactly as the matter holds it
(``named``, 2026-11-24)."""

from __future__ import annotations

import hashlib
from typing import Any

from ..litigation.tools import dump
from . import assemble, named
from .outcome import RecordsHold


def cut(run: Any) -> None:
    from records_contract import batches as batches_mod
    from smokeball_connector import matter_files as mf

    files = run._json("files.json")
    results, owners = assemble.merge(run.located())
    rows: list[dict[str, Any]] = []
    taken: set[str] = set()
    (run.data / "cuts").mkdir(exist_ok=True)
    counter: dict[int, int] = {}
    for fid, result in results.items():
        for pages, n in assemble.ranges_to_cut(result, fid, owners):
            counter[n] = k = counter.get(n, 0) + 1
            source = run.data / "files" / f"{fid}.pdf"
            total = int(result.get("pageCount") or 0)
            whole = result.get("namedFile") and pages in ("", f"1-{total}", "1" if total == 1 else None)
            blob = source.read_bytes() if whole else b""
            if whole and named.as_stored(blob) and hashlib.sha256(blob).hexdigest() == files[fid]["sha256"]:
                # The record named this file: it goes whole, exactly as downloaded
                # from the matter (2026-11-24). Another record's range on the same
                # file is still cut; a named file that cannot go as stored is cut
                # whole by the usual path below.
                span = None
            else:
                try:
                    span = mf.parse_pages(pages) if pages else None
                    blob, _removed = mf.prepare_local(source, span)
                except mf.StageRefused as exc:
                    raise RecordsHold(f"cut_refused: {exc}") from exc
            if len(blob) > mf.MAX_FILE_BYTES:
                raise RecordsHold("cut_refused: a cut is over the size one email carries")
            out = run.data / "cuts" / f"{n}-{k}.pdf"
            out.write_bytes(blob)
            count = span[1] - span[0] + 1 if span else int(files[fid]["page_count"])
            rows.append(
                {
                    "n": n,
                    "k": k,
                    "file_id": fid,
                    "pages": pages,
                    "page_count": count,
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "size": len(blob),
                    "name": mf.attachment_name(files[fid].get("name"), span, taken),
                    "path": str(out.relative_to(run.data)),
                }
            )
    try:
        batches_mod.batches(rows)
    except batches_mod.BatchesRefused as exc:
        raise RecordsHold(f"cut_refused: {exc}") from exc
    dump(run.data / "cuts.json", rows)


__all__ = ["cut"]
