"""The final set's files: what it reads of a job, what it writes, and its
$0 report on a COPY of a job directory (``final_set``). No model call; the
report writes nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..audit import claims as CL
from . import body as B, encounter as E, entry_edits as EE, heading as HD, uncited as U
from ...matter.store import write_atomic
from .final_set import CORRECTIONS, REPORT, ROWS, Inputs, Outcome, run


def _json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default
    except (OSError, ValueError):
        return default


def _partial_pages(p: Path) -> set[int]:
    """The pages a document's paid transcription holds (``partial/<id>.jsonl``)."""
    out: set[int] = set()
    try:
        lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    except OSError:
        return out
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        n = row.get("page") if isinstance(row, dict) else None
        if isinstance(n, int) and not isinstance(n, bool):
            out.add(n)
    return out


def page_inputs(slug_dir: Path, ud: Path) -> tuple[HD.Pages, E.PageId]:
    """What the heading check and the encounter key read of the new
    exhibits: each page's text, which pages were composed and which were
    transcribed, and each page's picture identity (``update/pageid``)."""
    from . import pageid_io as PG
    from .select import pages_of_text

    rows = [r for r in _json(ud / "new_exhibits.final.json", []) if isinstance(r, dict)]
    by_ex: dict[int, dict[str, Any]] = {r["exhibit"]: r for r in rows if isinstance(r.get("exhibit"), int)}
    doc_ex = {str(r.get("id")): n for n, r in by_ex.items() if r.get("id")}
    texts: dict[int, dict[int, str]] = {}
    shown: dict[int, set[int]] = {}
    for n, r in by_ex.items():
        tp = slug_dir / "text" / f"{r.get('id')}.txt"
        texts[n] = pages_of_text(tp.read_text(encoding="utf-8", errors="replace")) if tp.is_file() else {}
        shown[n] = _partial_pages(slug_dir / "partial" / f"{r.get('id')}.jsonl")
    read = {n: {p for p in (r.get("read") or []) if isinstance(p, int)} for n, r in by_ex.items()}
    ident = PG.load(ud)

    def page_id(ex: int, p: int) -> str:
        r = by_ex.get(ex)
        got = (ident.pages.get(str(r.get("id"))) or {}).get(p) if r else None
        if got is None or not got.exact:
            return f"exhibit:{ex}:{p}"
        n = PG.delivered_exhibit(got.other)
        n = doc_ex.get(got.other) if n is None else n
        return f"exhibit:{n}:{got.page}" if n is not None else f"document:{got.other}:{got.page}"

    pages = HD.Pages(
        text=lambda ex, p: texts.get(ex, {}).get(p),
        pages_of=lambda ex: texts.get(ex, {}),
        read=lambda ex: read.get(ex, set()),
        transcribed=lambda ex, p: p in shown.get(ex, set()),
    )
    return pages, page_id


def added_entries(slug_dir: Path, unit: str) -> int:
    """How many entries the update's reviewed audit document adds: 0 when the
    update added nothing (2026-11-17: an update that added no entry has no
    ``final-chronology.md`` at all, and the report crashed on it)."""
    rd = slug_dir / "runs" / unit
    doc, base = rd / "final-chronology.md", rd / "update" / "base.md"
    if not doc.is_file() or not base.is_file():
        return 0
    try:
        return len(B.entries_of(doc.read_text(encoding="utf-8"))[1])
    except ValueError:
        return 0


def write_nothing_added(slug_dir: Path, unit: str) -> None:
    """The no-op: an empty rows file, no corrections, the counts saying so;
    the chronology is not touched."""
    ud = slug_dir / "runs" / unit / "update"
    if not ud.is_dir():
        return
    write_atomic(ud / ROWS, "")
    write_atomic(ud / CORRECTIONS, "[]")
    write_atomic(ud / REPORT, json.dumps({"counts": {"added entries": 0}, "no_added_entries": True}, indent=1))


#: The reviewed audit document as this step first read it: a rerun starts
#: from it, never from the step's own output.
PRE = "final-chronology.pre-final-set.md"


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def source(slug_dir: Path, unit: str) -> str:
    """The document this step reads, WITHOUT WRITING ANYTHING (the report
    reads saved evidence through it). The chronology as it stands; or, when
    it is this step's own output (its sha is the one the marker recorded, and
    ``PRE`` is the input the marker names), ``PRE``: so a rerun is the same
    run, and a document a later stage or a rerun of the update review rewrote
    is read afresh. Only the stage's own ``write`` saves ``PRE``."""
    rd = slug_dir / "runs" / unit
    doc, pre = rd / "final-chronology.md", rd / PRE
    current = doc.read_text(encoding="utf-8")
    mark = _json(rd / "update" / REPORT, {}) or {}
    if pre.is_file() and mark.get("out_sha") == _sha(current):
        kept = pre.read_text(encoding="utf-8")
        if mark.get("in_sha") == _sha(kept):
            return kept
    return current


def load_inputs(slug_dir: Path, unit: str, headings: set[str], canon: Callable[[str], str] = str) -> Inputs:
    """The step's inputs, read from a job's own files (no write, no model)."""
    rd = slug_dir / "runs" / unit
    ud = rd / "update"
    new = ud / "new_entries.md"
    pages, page_id = page_inputs(slug_dir, ud)
    return Inputs(
        md=source(slug_dir, unit),
        base_md=(ud / "base.md").read_text(encoding="utf-8"),
        composed=B.split_body("\n\n" + new.read_text(encoding="utf-8"))[1] if new.is_file() else [],
        headings=headings,
        audit_rows=CL.read_rows(slug_dir / "out" / unit / "audit-results.jsonl"),
        pages=pages,
        page_id=page_id,
        canon=canon,
    )


def write(slug_dir: Path, unit: str, o: Outcome, src: str) -> None:
    """The step's outputs, each written whole or not at all
    (``matter.store.write_atomic``): its rows, its corrections, the marker
    naming the input and output shas, then the document, then each carried
    verdict as an audit row (once: a rerun appends none it already holds).
    The marker goes before the document, so a crash between them leaves the
    original document and a marker that does not name it: the rerun reads
    the document afresh. `src`: the document the step read (``source``)."""
    rd = slug_dir / "runs" / unit
    ud = rd / "update"
    doc, pre = rd / "final-chronology.md", rd / PRE
    if not pre.is_file() or pre.read_text(encoding="utf-8") != src:
        write_atomic(pre, src)  # the input, kept before anything else is written
    write_atomic(ud / ROWS, "".join(json.dumps(r) + "\n" for r in o.rows))
    write_atomic(ud / CORRECTIONS, json.dumps(o.corrections, indent=1))
    mark = {"counts": dict(o.counts), "ops": o.ops, "in_sha": _sha(src), "out_sha": _sha(o.md)}
    write_atomic(ud / REPORT, json.dumps(mark, indent=1))
    if doc.read_text(encoding="utf-8") != o.md:
        write_atomic(doc, o.md)
    results = slug_dir / "out" / unit / "audit-results.jsonl"
    latest = {str(r["key"]): r for r in CL.read_rows(results) if r.get("kind") == "real" and r.get("key")}
    sha = CL.doc_sha_of(CL.body_of(o.md))
    for row in o.carries:
        last = latest.get(row["key"])
        # The LATEST row decides (the proofs read the last row per key): a
        # carry is appended unless that row already is this carry with this
        # verdict, so a verdict that changed since is never left stale.
        if not (last and last.get("carried_from") == row["carried_from"] and last.get("verdict") == row["verdict"]):
            CL.append_row(results, row | {"doc_sha": sha})


def ran(ud: Path) -> bool:
    """The final set ran for this job AND the document is its output: the
    marker (written on every run, ``write``) names the document's sha, or
    says the update added nothing. A marker left by a run interrupted before
    the document was replaced names another sha, so assemble then keeps its
    old behaviour (joins stray lines itself); so it does with no marker."""
    mark = _json(ud / REPORT, None)
    if not isinstance(mark, dict):
        return False
    if mark.get("no_added_entries") is True:
        return True
    doc = ud.parent / "final-chronology.md"
    return doc.is_file() and mark.get("out_sha") == _sha(doc.read_text(encoding="utf-8"))


def read_rows(ud: Path) -> list[dict[str, Any]]:
    """``final_rows.jsonl``, parsed (a line that does not parse is skipped)."""
    return [r for r in CL.read_rows(ud / ROWS) if isinstance(r, dict)]


# ---- the $0 report ------------------------------------------------------------------------------


def _headings_of(*mds: str) -> set[str]:
    """The subsection headings the entries' provider lines name."""
    out = {"Discharge / Discontinuation in Care"}
    for md in mds:
        for e in B.entries_of(md)[1]:
            lines = e.split("\n")
            if len(lines) > 1 and " | " in lines[1]:
                out.add(lines[1].rpartition(" | ")[2].strip())
    return out


def locate(root: Path, unit: str | None = None) -> tuple[Path, str]:
    """(the job's slug directory, its unit) under `root`: the slug directory
    itself (it holds ``runs/``), or a copy root holding ``data/<slug>/`` (or
    ``<slug>/``). Exactly one unit with an ``update/`` directory, unless
    `unit` names it."""
    found: list[tuple[Path, str]] = []
    for sd in [root, *sorted(root.glob("data/*")), *sorted(root.glob("*"))]:
        if not (sd / "runs").is_dir():
            continue
        for p in sorted((sd / "runs").glob("*")):
            if (p / "update").is_dir() and (unit is None or p.name == unit) and (sd, p.name) not in found:
                found.append((sd, p.name))
        if found:
            break
    if len(found) != 1:
        raise SystemExit(f"found {len(found)} update units under the copy; name one with --unit")
    return found[0]


class _NoConfig:
    """A firm config with no provider aliases (``stages.group.Canon``)."""

    def get(self, *_keys: str) -> None:
        return None


def firm_config_of(root: Path, explicit: Path | None = None) -> Path | None:
    """The firm config the stage read: `explicit`, else a ``firm.yaml`` the
    copy holds (its root, ``config/``, or one level below), else None."""
    if explicit is not None:
        return explicit
    for p in [root / "firm.yaml", root / "config" / "firm.yaml", *sorted(root.glob("*/firm.yaml"))]:
        if p.is_file():
            return p
    return None


def report(job: Path, unit: str | None = None, firm_config: Path | None = None) -> dict[str, Any]:
    """The answer key's counts for a COPY of a job directory. Writes nothing.
    It computes with the firm config the stage used (``firm_config_of``):
    its provider aliases decide which entries are one encounter, so with no
    config the counts can differ from the stage's, and ``config`` says so."""
    from ...stages.group import Canon
    from ...stages.update_base import headings
    from .. import config as config_mod

    cfg_path = firm_config_of(job, firm_config)
    job, u = locate(job, unit)
    ud = job / "runs" / u / "update"
    used = str(cfg_path) if cfg_path else None
    if not added_entries(job, u):
        return {
            "config": used,
            "no_added_entries": True,
            "added_entries": 0,
            "distinct_visits_merged": 0,
            "new_claim_keys_created": 0,
        }
    md = (job / "runs" / u / "final-chronology.md").read_text(encoding="utf-8")
    base = (ud / "base.md").read_text(encoding="utf-8")
    if cfg_path is not None:
        cfg = config_mod.load(str(cfg_path))
        x = load_inputs(job, u, headings(cfg), Canon(cfg))
    else:
        x = load_inputs(job, u, _headings_of(md, base), Canon(_NoConfig()))
    o = run(x)
    c = o.counts
    return {
        "config": used,
        "no_added_entries": False,
        "stray_lines": {"before": c["stray lines before"], "after": c["stray lines after"]},
        "verdicts_carried": c["verdicts carried"],
        "repeats": {k.split(": ", 1)[1]: v for k, v in sorted(c.items()) if k.startswith("repeats: ")},
        "repeat_pairs_by_condition": {k[len("pair: ") :]: v for k, v in sorted(c.items()) if k.startswith("pair: ")},
        "distinct_visits_merged": c["distinct visits merged"],
        "headings": {
            **{w: c[f"headings {w}"] for w in (HD.CITED, HD.SAME_NOTE, HD.NO_TEXT, HD.FAILED)},
            "read on a transcript": c["headings read on a transcript"],
        },
        "new_claim_keys_created": c["new claim keys created"],
        "added_entries": c["added entries"],
        "corrections_recorded": c["corrections recorded"],
        "delivered_entries": carried(B.entries_of(base)[1], ud / "updated-chronology.md"),
        "record_only": _record_only(x, o),
        "borrowed_citations": U.measure_borrowed(
            x.composed, (_json(ud / "compose_report.json", {}) or {}).get("uncited_paragraphs") or [], x.pages.text
        ),
    }


def carried(base_ents: list[str], updated: Path) -> dict[str, Any]:
    """How many delivered entries a saved updated chronology carries byte for
    byte and in order (None when the update was not assembled)."""
    if not updated.is_file():
        return {"delivered": len(base_ents), "byte_identical_in_order": None}
    out = [EE.without_fold(e) for e in B.entries_of(updated.read_text(encoding="utf-8"))[1]]
    j = n = 0
    for d in base_ents:
        k = next((i for i in range(j, len(out)) if out[i] == d), None)
        if k is not None:
            n, j = n + 1, k + 1
    return {"delivered": len(base_ents), "byte_identical_in_order": n}


def _record_only(x: Inputs, o: Outcome) -> dict[str, int]:
    """The record-only proofs that read only the final set: 13 over its
    entries and 15 over the reviewed document as it now stands."""
    from .. import graded_proof as GP
    from . import proofs_final as PF

    got = PF.p13(
        PF.Inputs([], [], [], [], [], 0, [], {}, o.rows, B.entries_of(o.md)[1], canon=x.canon, page_id=x.page_id)
    )
    g = GP.check(CL.body_of(o.md), GP.latest_supported(x.audit_rows + o.carries), headings=x.headings)
    return dict(got) | GP.codes(g)
