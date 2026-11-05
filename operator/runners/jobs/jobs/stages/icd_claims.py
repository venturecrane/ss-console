"""The full chronology's Diagnostic Highlights, audited like any claim. $0 here;
the audit stage does the grading.

Before (ADR 0104, audit finding B2): a code went from the compose pass straight
into the table, never checked against a page. The update lane closed this for
itself long ago (``update/icd.py``); this is the same machinery on the full
build, not a second implementation:

* ``build`` (at ``build_doc``) turns each code into the claim
  "Diagnosis code <code> recorded on MM/DD/YYYY. (Exhibit N - p. P)", cited to
  the page of the entry that records it, for the audit to read at the head of
  the body, and renders the table the document carries until the audit is done.
  ``icd_claims.json`` records that claims are pending.
* ``settle`` (when the audit stage passes) takes the claims back out of the body
  and writes the table ONLY from the claims the audit kept, in the code the
  audited claim states. A claim the audit dropped is a row the firm does not
  get, and it is on the Review Notes like every other dropped claim. A code with
  no description reads ``icd.NOT_LEGIBLE``, never blank.

A job whose document was built before this file existed has no marker, so
``settle`` leaves its table exactly as it was built.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..medchron import icd_tables
from ..medchron.audit import claims as CL
from ..medchron.audit.page_text import exhibit_paths
from ..medchron.audit.run import AuditPaths
from ..medchron.update import body as B, icd
from . import assemble
from .base import StageRun, read_json

MARKER = "icd_claims.json"
_FIRST_ENTRY = re.compile(r"(?m)^\d{2}/\d{2}/\d{4}\s*$")


def _resolver(tables: dict[str, Any]) -> icd.Resolve:
    return lambda code, on, text: icd_tables.resolve(code, tables, on, text)


def build(sr: StageRun, rd: Path, texts: list[str]) -> tuple[list[str], list[str]]:
    """(the table rows, header first; the claims for the audit). Writes the
    marker when there are claims, removes it when there are none. Raises
    ``icd_tables.TablesMissing`` only when a code needs the tables."""
    (rd / MARKER).unlink(missing_ok=True)
    rows = assemble.code_rows(rd)
    if not icd.index_codes(rows, {iso for iso in map(B.iso, texts) if iso}):
        return [icd.HEAD], []
    resolve = _resolver(icd_tables.load(sr.job.install_root))
    claims, log = icd.claims(rows, texts, [icd.HEAD], resolve)
    _, audited = icd.split_claims("\n\n".join(claims))
    table, _changed, illegible = icd.rows([icd.HEAD], audited, texts, resolve)
    sr.log(f"  diagnosis codes: {len(claims)} claim(s) for the audit, {len(illegible)} with no description")
    if claims:
        (rd / MARKER).write_text(
            json.dumps({"pending": True, "claims": claims, "log": log}, indent=1), encoding="utf-8"
        )
    return table, claims


def _write_table(lines: list[str], rows: list[str]) -> None:
    from .review_tables import HIGHLIGHTS, _section

    sec = _section(lines, HIGHLIGHTS)
    if sec is not None:
        first, end = sec
        lines[first:end] = rows[1:]


def _carry_verdicts(paths: AuditPaths, was: str, now: str) -> None:
    """The live claims' verdicts, carried to the settled body: taking the claims
    out changes the body's hash and no live claim's key, and a later projection
    counts a verdict only for the body it was graded on. A removed claim's row
    stays on the old hash, or the double-sweep guard would read it as a key the
    extractor no longer produces."""
    old, new = CL.doc_sha_of(was), CL.doc_sha_of(now)
    live = {c["key"] for c in CL.extract_claims(now, set(exhibit_paths(paths.out)))}
    rows = CL.read_rows(paths.results)
    for r in rows:
        if r.get("doc_sha") == old and r.get("key") in live:
            r["doc_sha"] = new
    if rows:
        tmp = paths.results.with_name(paths.results.name + ".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(paths.results)


def settle(sr: StageRun) -> None:
    """The audited claims out of the body and into the table. A document with
    no pending marker is left as it stands: nothing was added to it, or a
    re-audit of a document already settled has nothing to take out."""
    from .build_doc import parse_entries

    rd = sr.slug_dir / "runs" / sr.unit.unit
    marker = read_json(rd / MARKER, None)
    if not isinstance(marker, dict) or not marker.get("pending"):
        return
    doc = rd / "final-chronology.md"
    md = doc.read_text(encoding="utf-8")
    head, rest = md.split(CL.BODY_START, 1)
    body, tail = rest.split(CL.BODY_END, 1)
    m = _FIRST_ENTRY.search(body)
    pre, entries = (body[: m.start()], body[m.start() :]) if m else (body, "")
    kept, audited = icd.split_claims(pre)
    resolve = _resolver(icd_tables.load(sr.job.install_root))
    table, _changed, illegible = icd.rows([icd.HEAD], audited, [e["text"] for e in parse_entries(entries)], resolve)
    new_body = "\n\n" + (kept.strip("\n") + "\n\n" if kept.strip() else "") + entries
    lines = (head + CL.BODY_START + new_body + CL.BODY_END + tail).split("\n")
    _write_table(lines, table)
    doc.write_text("\n".join(lines), encoding="utf-8")
    _carry_verdicts(AuditPaths(sr.slug_dir, sr.unit.unit), body, new_body)
    (rd / MARKER).write_text(json.dumps({**marker, "pending": False}, indent=1), encoding="utf-8")
    gone = len(marker.get("claims") or []) - len(audited)
    sr.log(
        f"diagnosis codes settled: {len(audited)} row(s) from audited claims, {gone} claim(s) the audit removed, "
        f"{len(illegible)} with no description"
    )
