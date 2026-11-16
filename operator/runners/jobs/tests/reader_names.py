"""Every place a file's name and extension are joined, and the string each makes.

A pulled row keeps a document's name and its extension apart; nineteen places
in the jobs bundle join them back, some to SHOW a name (to the model, to a
firm), some to KEY on it (the coverage gate, the exhibit builder). An email
attachment's row keeps its extension in its name AND in ``ext``, so the join
reads ``x.pdf.pdf``; the same name filed reads ``x.pdf``. Every cell here runs
one join site over ``ROWS`` (both of those, a plain file, a file with no
extension, and a ``.msg`` container) and records the strings it produced.

Each site is driven through the function that holds the join. Where that is a
whole stage, the stage runs over the smallest artifacts it reads, and the join's
string is captured at the call it is handed to (``Captured``): the stage's own
collaborator, named in ``test_reader_goldens.RULES``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

#: The rows every join site is given (``id``, ``name``, ``ext`` as pulled).
ROWS: list[dict[str, Any]] = [
    {"id": "n-emailed", "name": "x.pdf", "ext": ".pdf", "folder": "/MEDICAL", "folderId": "fold-med"},
    {"id": "n-filed", "name": "x", "ext": ".pdf", "folder": "/MEDICAL", "folderId": "fold-med"},
    {"id": "n-plain", "name": "Clinic Note 01-20-26", "ext": ".docx", "folder": "/MEDICAL", "folderId": "fold-med"},
    {"id": "n-bare", "name": "README", "ext": "", "folder": "/MEDICAL", "folderId": "fold-med"},
    {"id": "n-upper", "name": "Scan.PDF", "ext": ".PDF", "folder": "/MEDICAL", "folderId": "fold-med"},
]


class _Stop(Exception):
    pass


class Captured:
    """A collaborator replaced by a recorder: its arguments are kept, and it
    returns ``value`` or (``stop``) ends the stage there."""

    def __init__(self, value: Any = None, stop: bool = False) -> None:
        self.args: list[Any] = []
        self.value, self.stop = value, stop

    def __call__(self, *a: Any, **k: Any) -> Any:
        self.args.append({"args": list(a), "kwargs": k})
        if self.stop:
            raise _Stop()
        return self.value


class KeyLog(dict):
    """A dict that records every key it is asked for."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def get(self, key: Any, default: Any = None) -> Any:
        self.log.append(key)
        return super().get(key, default)


def _rows() -> list[dict[str, Any]]:
    return [dict(r) for r in ROWS]


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _sr(tmp: Path) -> Any:
    import reader_kit as rk
    from reader_lanes import _stage_run

    return _stage_run(tmp / "mc", rk.Model(), rk.Seat([], {}))


def _unit_files(sr: Any, rows: list[dict[str, Any]]) -> None:
    p = sr.slug_dir / "units" / f"{sr.unit.unit}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows), encoding="utf-8")
    (sr.slug_dir / "runs" / sr.unit.unit).mkdir(parents=True, exist_ok=True)


# ---- one function, one join ---------------------------------------------------------------
def coverage_file_key(tmp: Path, mp: Any) -> Any:
    from jobs.stages import coverage

    return [coverage.file_key(r) for r in _rows()]


def content_dupes_key(tmp: Path, mp: Any) -> Any:
    from jobs.stages import content_dupes

    return [content_dupes._key(r) for r in _rows()]


def units_orphan_named(tmp: Path, mp: Any) -> Any:
    from jobs.stages import units

    return [units._orphan_named(r) for r in _rows()]


def units_unique_names(tmp: Path, mp: Any) -> Any:
    from jobs.stages import units

    rows = _rows() + [dict(ROWS[1], id="n-filed-2")]
    renamed = units.unique_names(rows)
    return {"renamed": renamed, "rows": rows}


def chunking_headers(tmp: Path, mp: Any) -> Any:
    from jobs.stages import chunking

    rows = _rows()
    for r in rows:
        p = tmp / "text" / f"{r['id']}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"[p.1]\nthe text of {r['id']}", encoding="utf-8")
        r["text_path"] = str(p)
    return [c.splitlines()[0] for c in chunking.build_chunks(rows, 10_000)] + chunking.build_chunks(rows, 60)


def email_parents(tmp: Path, mp: Any) -> Any:
    from jobs.stages import email_parents as ep

    sd = tmp / "slug"
    _jsonl(sd / "msg_manifest.jsonl", [{"id": f"mail-{r['id']}", "name": r["name"], "ext": r["ext"]} for r in ROWS])
    raw = [{"id": f"msgatt-{r['id']}", "container": r["name"]} for r in ROWS]
    raw += [{"id": f"msgatt-{r['id']}-joined", "container": f"{r['name']}{r['ext']}"} for r in ROWS]
    _jsonl(sd / "raw_manifest.jsonl", raw)
    return ep._parents_in(sd, {r["id"] for r in raw})


def download_why_not(tmp: Path, mp: Any) -> Any:
    import re

    from jobs.stages import download

    rule = {"prefixes": ["/MEDICAL"], "excludes": [], "root_pdfs": True, "doc_exts": {".pdf", ".docx", ".msg"}}
    names = [(re.compile(r"x\.pdf\.pdf$"), "a doubled name"), (re.compile(r"^x\.pdf$"), "the filed name")]
    return [download._why_not(r, "/MEDICAL", rule, names, set()) for r in _rows()]


def download_named_excluded(tmp: Path, mp: Any) -> Any:
    from jobs.stages import download

    sr = _sr(tmp)
    (sr.slug_dir / "manifest.json").write_text(json.dumps({"documents": _rows()}), encoding="utf-8")
    (sr.slug_dir / "folders.json").write_text(json.dumps([]), encoding="utf-8")
    rule = {"prefixes": ["/OTHER"], "excludes": [], "root_pdfs": False, "doc_exts": {".pdf"}}
    got = download._named_targets(sr, {"include_file_ids": [r["id"] for r in ROWS]}, {}, rule)
    return {"returned": got, "named_excluded": json.loads((sr.slug_dir / "named_excluded.json").read_text())}


def decisions_orphans(tmp: Path, mp: Any) -> Any:
    from jobs.medchron import decisions

    sr = _sr(tmp)
    _jsonl(sr.slug_dir / "raw_manifest.jsonl", [{**r, "ok": True} for r in ROWS])
    _jsonl(
        sr.slug_dir / "extracted.jsonl", [{"id": r["id"], "name": r["name"], "error": "unhandled ext"} for r in ROWS]
    )
    d = decisions.orphans(sr.job, sr.cfg, sr.slug_dir, sr.unit, dry_run=True)
    return {"payload": d.payload, "holds": d.holds}


def limitations_withheld(tmp: Path, mp: Any) -> Any:
    from jobs.medchron.gates import foreign_identity as FI
    from jobs.stages import limitations

    d = tmp / "slug"
    d.mkdir(parents=True)
    mp.setattr(FI, "withheld", lambda _d: {r["id"] for r in ROWS})
    byid = {r["id"]: r for r in ROWS}
    return limitations._withheld_names(d, byid, set())


def limitations_orphan_reason(tmp: Path, mp: Any) -> Any:
    from jobs.stages import limitations

    import re

    rules = [(re.compile(r"^x$"), "the bare filed name"), (re.compile(r"^x\.pdf$"), "the bare emailed name")]

    class Rules:
        """The firm config's ``compiled`` exclusion rules, two that tell the bare names apart."""

        def compiled(self, *_a: Any) -> Any:
            return rules

    byid = {r["id"]: r for r in ROWS}
    asked = ["x.pdf.pdf", "x.pdf", "Clinic Note 01-20-26.docx", "README", "Scan.PDF.PDF"]
    return {n: limitations._orphan_reason(n, "the stored reason", byid, Rules()) for n in asked}


def limitations_shown(tmp: Path, mp: Any) -> Any:
    from jobs.stages import limitations_rules

    return [limitations_rules._shown(f"{r['name']}{r['ext']}") for r in ROWS]


def msg_container_names(tmp: Path, mp: Any) -> Any:
    from reader_fixtures import MSG
    from reader_lanes import _fake_msg

    from jobs.stages import msg

    _fake_msg(mp)
    sr = _sr(tmp)
    pulled = []
    for r in ROWS:
        p = sr.slug_dir / "msg_raw" / f"{r['id']}.msg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(MSG)
        pulled.append({"id": r["id"], "name": r["name"], "ext": ".msg", "ok": True, "path": str(p)})
    ix, _n = msg.index_containers(sr, pulled, {})
    return {"by_hash": list(ix.by_hash.values()), "errors": ix.errors}


def units_billing_stem(tmp: Path, mp: Any) -> Any:
    from jobs.stages import units

    d = tmp / "slug"
    d.mkdir(parents=True)
    (d / "billing_docs.json").write_text("[]", encoding="utf-8")
    _jsonl(d / "billing_extract.jsonl", [])
    asked: list[str] = []
    stem = units.billing_stem
    mp.setattr(units, "billing_stem", lambda name: (asked.append(name), stem(name))[1])
    units.mark_compose_skips(d, {"alpha": _rows()}, lambda _m: None)
    return asked


def assemble_resolver(tmp: Path, mp: Any) -> Any:
    from jobs.stages import assemble

    sr = _sr(tmp)
    _unit_files(sr, _rows())
    cap = Captured(stop=True)
    mp.setattr(assemble, "Resolver", cap)
    try:
        assemble.run(sr)
    except _Stop:
        pass
    return cap.args


def group_names(tmp: Path, mp: Any) -> Any:
    from jobs.stages import group

    sr = _sr(tmp)
    _unit_files(sr, _rows())
    asked: list[str] = []
    cap = Captured(value=(KeyLog(asked), KeyLog(asked)))
    mp.setattr(group, "index_rows", cap)
    group.run(sr)
    return {"index_rows": [a["args"][1] for a in cap.args], "looked_up": asked}


def exhibits_byname(tmp: Path, mp: Any) -> Any:
    from jobs.stages import exhibits

    sr = _sr(tmp)
    _unit_files(sr, _rows())
    cap = Captured(stop=True)
    mp.setattr(exhibits, "index_rows", cap)
    try:
        exhibits.run(sr)
    except _Stop:
        pass
    return [a["args"][1] for a in cap.args]


def revise_same_file(tmp: Path, mp: Any) -> Any:
    from jobs.drafting.revise import versions

    out = []
    for r in ROWS:
        m = {"isDeleted": False, "folder": {"id": "fold-med"}, "name": r["name"], "fileExtension": r["ext"]}
        out.append(
            {
                shown: versions._same_file(m, {"name": shown}, "fold-med")
                for shown in ("x.pdf", "x.pdf.pdf", f"{r['name']}{r['ext']}")
            }
        )
    return out


def negotiation_prompt(tmp: Path, mp: Any) -> Any:
    from jobs.negotiation import extract, offer_check

    out = []
    for r in ROWS:
        doc = {"name": r["name"], "ext": r["ext"], "saved": "2026-10-07"}
        out.append(
            {
                "read": extract.render(doc, "the text", [], []).split("===", 1)[1].split("===")[0],
                "check": offer_check.render(doc, "the text", []).split("===", 1)[1].split("===")[0],
            }
        )
    return out


SITES: dict[str, Callable[[Path, Any], Any]] = {
    "coverage_file_key": coverage_file_key,
    "content_dupes_key": content_dupes_key,
    "units_orphan_named": units_orphan_named,
    "units_unique_names": units_unique_names,
    "units_billing_stem": units_billing_stem,
    "chunking_headers": chunking_headers,
    "email_parents": email_parents,
    "download_why_not": download_why_not,
    "download_named_excluded": download_named_excluded,
    "decisions_orphans": decisions_orphans,
    "limitations_withheld": limitations_withheld,
    "limitations_orphan_reason": limitations_orphan_reason,
    "limitations_shown": limitations_shown,
    "msg_container_names": msg_container_names,
    "assemble_resolver": assemble_resolver,
    "group_names": group_names,
    "exhibits_byname": exhibits_byname,
    "revise_same_file": revise_same_file,
    "negotiation_prompt": negotiation_prompt,
}


def _wrap(fn: Callable[[Path, Any], Any]) -> Callable[[Path, Any], Any]:
    def run(tmp: Path, mp: Any) -> Any:
        import reader_kit as rk

        return rk.scrub(fn(tmp, mp), tmp)

    return run


SITES = {k: _wrap(v) for k, v in SITES.items()}

__all__ = ["ROWS", "SITES"]
