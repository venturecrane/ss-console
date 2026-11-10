"""Build a matter's record set and run the production chronology over it, $0.

The PDFs are written first (``records/``, the set a later step uploads to the
demo tenant). Then a job workdir is laid out the way ``download`` leaves one,
and the production stages run over it in the DAG's order. The paid stages are
not skipped by pretending: each is either fed its own checkpoint from the
answer key and run (vision, billing_extract, which find nothing left to read),
or replaced at its output file by the answer key (map, condense,
classify_scanned), and the stages after are the production code unchanged.
Every stage runs with a client that raises on any call, so a stage that would
spend stops the build instead of spending.

The strip runs as in production: ``classify_nonrecord`` reads the text pages
against the demo firm's page classes, ``decide_control`` picks the record
control, the answer key labels the scanned head pages, and ``strip`` (falsify,
dry, apply) and ``exhibit_merge`` take the fax covers and certifications out of
the exhibits and renumber every citation.

Not run, and why: ``audit`` and ``review`` are model reads of the finished
package with no checkpoint to feed. ``upload`` writes to a tenant and is never
run here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .. import config as config_mod, decision_rules, decisions, job as job_mod
from ..probes import SYNTHETIC_FIRM
from ...stages import (
    assemble,
    billing,
    billing_chart,
    billing_docx,
    build_doc,
    classify,
    content_dupes,
    coverage,
    dos_check,
    exhibit_merge,
    exhibits,
    extract,
    group,
    identity,
    manifest,
    merge,
    render,
    repair,
    scope,
    strip,
    summarize,
    units,
    vision,
)
from ...stages.base import StageRun
from . import answer_key, icd, pdf
from .model import Plan


class SpendAttempted(RuntimeError):
    pass


class NoSpendClient:
    """Stands where the SDK client would. Any use is a stage trying to spend."""

    def __getattr__(self, name: str) -> Any:
        raise SpendAttempted(f"a stage reached for the model client ({name}); a sample build spends nothing")


class StageFailed(RuntimeError):
    pass


@dataclass
class Build:
    plan: Plan
    root: Path
    log: list[str] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    #: Every departure of the built chronology from the answer key; empty passes.
    problems: list[str] = field(default_factory=list)

    @property
    def slug_dir(self) -> Path:
        return self.root / "data" / self.plan.matter["slug"]

    @property
    def unit(self) -> str:
        return self.plan.matter["unit"]

    @property
    def out_dir(self) -> Path:
        return self.slug_dir / "out" / self.unit


def firm_config(plan: Plan) -> dict[str, Any]:
    """The demo firm's medchron config: the package's synthetic firm, named for
    the demo tenant, with this matter's facilities as provider aliases."""
    cfg = copy.deepcopy(SYNTHETIC_FIRM)
    firm = str(plan.matter["firm"])
    cfg["firm"] = {"slug": "brannock-ferreira-demo", "display_name": firm}
    cfg["providers"]["aliases"] = [
        {"match": f"{pv.match}|{pv.facility}" if pv.match else pv.facility, "label": pv.facility}
        for pv in plan.providers.values()
    ]
    cfg["format"]["subsections"] = list(plan.headings)
    cfg["billing"]["name_patterns"] = [
        r"(?i)\bbill",
        r"(?i)ledger",
        r"(?i)statement",
        r"(?i)claim form",
        r"(?i)\bEOB\b",
        r"(?i)lien",
    ]
    # The demo firm's text page classes: the two kinds of paper a production
    # wraps its records in. Scanned covers are labelled by the answer key.
    cfg["nonrecord"]["page_classes"] = [
        *cfg["nonrecord"]["page_classes"],
        {"name": "COVER", "patterns": [r"FACSIMILE TRANSMITTAL"]},
        {"name": "CERT", "patterns": [r"CERTIFICATION OF CUSTODIAN OF RECORDS"]},
    ]
    cfg["delivery"]["folder_template"] = "MEDICAL CHRONOLOGY - {CLIENT} {MM-DD-YY}"
    return cfg


def _job_yaml(plan: Plan, root: Path) -> dict[str, Any]:
    m, pt = plan.matter, plan.patient
    return {
        "slug": m["slug"],
        "matter": {"number": m["number"], "id": m["matter_id"], "title": m["title"]},
        "units": [
            {
                "unit": m["unit"],
                "client_name": pt["name"],
                "name_token": pt["last"],
                "surname": pt["last"],
                "dob": pt["dob"],
            }
        ],
        "incident": {"date": str(m["incident_date"]), "source": "administrator_request"},
        "injuries": m["injuries"],
        "data_root": str(root / "data"),
        "install_root": str(root / "install"),
    }


def write_records(plan: Plan, records: Path, log: Callable[[str], None]) -> dict[str, bytes]:
    records.mkdir(parents=True, exist_ok=True)
    blobs = {}
    for doc in plan.documents:
        data = pdf.render(plan, doc)
        (records / doc.file_name).write_bytes(data)
        blobs[doc.id] = data
        log(f"rendered {doc.file_name}: {len(doc.pages)} pp, {sum(1 for p in doc.pages if p.scan)} scanned")
    return blobs


def _lay_out(b: Build, blobs: dict[str, bytes]) -> None:
    raw = b.slug_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    rows, listing = [], []
    for doc in b.plan.documents:
        # A link, not a copy: the pulled file IS the record PDF, and no stage
        # writes under raw/ (the rehearsal links it for the same reason).
        path = raw / f"{doc.file_id}.pdf"
        path.symlink_to(b.root / "records" / doc.file_name)
        size, sha = len(blobs[doc.id]), hashlib.sha256(blobs[doc.id]).hexdigest()
        rows.append({"id": doc.file_id, "name": doc.name, "ext": ".pdf", "folder": "/Medical Records",
                     "size_expected": size, "ok": True, "path": str(path), "size_got": size, "sha256": sha})  # fmt: skip
        listing.append({"id": doc.file_id, "name": doc.name, "size": size, "ext": ".pdf", "folderId": "f-medical",
                        "created": None, "modified": None, "deleted": False})  # fmt: skip
    (b.slug_dir / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (b.slug_dir / "manifest.json").write_text(json.dumps({"documents": listing}), encoding="utf-8")
    (b.slug_dir / "folders.json").write_text(
        json.dumps([{"id": "f-medical", "path": "/Medical Records"}]), encoding="utf-8"
    )


def _stage_run(b: Build) -> StageRun:
    job = job_mod.load(b.root / "job")
    cfg = config_mod.load(str(b.root / "job" / "firm.yaml"))
    return StageRun(
        job=job,
        cfg=cfg,
        unit=job.units[0],
        slug_dir=b.slug_dir,
        decided={},
        log=b.log.append,
        seat_factory=_no_seat,
        client_factory=NoSpendClient,
        date_stamp=str(b.plan.matter["date_stamp"]),
    )


def _no_seat() -> Any:
    raise SpendAttempted("a stage reached for the seat; a sample build touches no tenant")


def _run(b: Build, sr: StageRun, name: str, fn: Callable[[StageRun], int], *, gate: bool = False) -> int:
    b.log.append(f"--- {name}")
    t = time.monotonic()
    rc = fn(sr)
    b.seconds[name] = round(time.monotonic() - t, 2)
    b.verdicts[name] = rc
    if rc != 0 and not gate:
        raise StageFailed(f"{name} exited {rc}: " + " | ".join(b.log[-6:]))
    return rc


def _seed_front(b: Build, sr: StageRun) -> None:
    _run(b, sr, "extract", extract.run)
    _run(b, sr, "content_dupes", content_dupes.run)
    answer_key.seed_vision(b.plan, b.slug_dir)
    _run(b, sr, "vision (fed its checkpoints)", vision.run)
    picked = decisions.billing_docs(sr.job, sr.cfg, b.slug_dir, dry_run=False)
    docs = picked.payload.get("docs") if isinstance(picked.payload, dict) else picked.payload
    answer_key.seed_billing(b.plan, b.slug_dir, [str(r["name"]) for r in docs or []])
    _run(b, sr, "billing_extract (fed its records)", billing.run)
    decisions.units(sr.job, sr.cfg, b.slug_dir, dry_run=False)
    _run(b, sr, "build_units", units.run)
    _run(b, sr, "identity", identity.run)


def _back_half(b: Build, sr: StageRun) -> None:
    rd = b.slug_dir / "runs" / b.unit
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "map-01.md").write_text(answer_key.map_text(b.plan), encoding="utf-8")
    for name, fn in (("repair_truncated", repair.run), ("assemble", assemble.run), ("merge", merge.run),
                     ("group", group.run), ("filter", scope.run), ("exhibits", exhibits.run)):  # fmt: skip
        _run(b, sr, name, fn)
    # condense, answered: the authored entries are already the short form.
    (rd / "entries_condensed.md").write_text((rd / "entries_final.md").read_text(encoding="utf-8"), encoding="utf-8")
    _run(b, sr, "summarize", summarize.run)
    icd.vendor(b.plan, b.root / "install")
    _run(b, sr, "build_doc", build_doc.run)
    _strip(b, sr)
    _run(b, sr, "coverage_gate", coverage.run, gate=True)
    _run(b, sr, "dos_check", dos_check.run, gate=True)
    _run(b, sr, "billing_chart", billing_chart.run)
    _run(b, sr, "billing_docx", billing_docx.run)
    _run(b, sr, "render", render.run)
    _run(b, sr, "manifest", manifest.run)


def _strip(b: Build, sr: StageRun) -> None:
    """classify_nonrecord, decide_control, classify_scanned (answered), the
    three strip passes and exhibit_merge, in the DAG's order."""
    _run(b, sr, "classify_nonrecord", classify.run_nonrecord)
    decision_rules.control(sr.job, sr.cfg, b.slug_dir, sr.unit, dry_run=False)
    labels = answer_key.seed_classify(b.plan, b.slug_dir, b.unit)
    b.log.append(
        f"--- classify_scanned (answered): controls_ok={labels['controls_ok']}, {len(labels['labels'])} labels"
    )
    if not labels["controls_ok"]:
        raise StageFailed("the record control decide_control chose is not a record page in the answer key")
    _run(b, sr, "strip_falsify", strip.run_falsify)
    _run(b, sr, "strip_dry", strip.run_dry)
    _run(b, sr, "strip_apply", strip.run_apply)
    _run(b, sr, "exhibit_merge", exhibit_merge.run)


def exhibit_pages(b: Build) -> dict[str, list[int]]:
    """{exhibit: [pages before the strip, pages after]}."""
    pm = json.loads((b.out_dir / "page_map.json").read_text(encoding="utf-8"))
    after = json.loads(_strip_result(b)).get("new_page_counts", {})
    return {str(e["exhibit"]): [int(e["total_pages"]), int(after.get(str(e["exhibit"]), e["total_pages"]))] for e in pm}


def _strip_result(b: Build) -> str:
    p = b.slug_dir / "strip_result.json"
    return p.read_text(encoding="utf-8") if p.is_file() else "{}"


def strip_drops(b: Build) -> dict[str, list[int]]:
    return json.loads(_strip_result(b)).get("drops", {})


def build(plan: Plan, out: Path) -> Build:
    """Records under ``out/records``, the job under ``out/job`` and ``out/data``,
    the deliverables under ``out/data/<slug>/out/<unit>``, the answer key at
    ``out/answer-key.json`` and the build report at ``out/build.json``."""
    if out.exists():
        shutil.rmtree(out)
    b = Build(plan, out)
    t0 = time.monotonic()
    blobs = write_records(plan, out / "records", b.log.append)
    b.seconds["records"] = round(time.monotonic() - t0, 2)
    (out / "job").mkdir(parents=True)
    (out / "job" / "job.yaml").write_text(yaml.safe_dump(_job_yaml(plan, out), sort_keys=False), encoding="utf-8")
    (out / "job" / "firm.yaml").write_text(yaml.safe_dump(firm_config(plan), sort_keys=False), encoding="utf-8")
    _lay_out(b, blobs)
    sr = _stage_run(b)
    try:
        _seed_front(b, sr)
        _back_half(b, sr)
    finally:
        (out / "build.log").write_text("\n".join(b.log) + "\n", encoding="utf-8")
    answer_key.write(plan, out / "answer-key.json")
    final = (b.slug_dir / "runs" / b.unit / "final-chronology.md").read_text(encoding="utf-8")
    page_map = json.loads((b.out_dir / "page_map.json").read_text(encoding="utf-8"))
    b.problems = answer_key.score(plan, final, page_map, strip_drops(b))
    report = {
        "matter": plan.matter["slug"],
        "pages": plan.total_pages(),
        "scanned_pages": sum(1 for d in plan.documents for p in d.pages if p.scan),
        "documents": len(plan.documents),
        "records_bytes": sum(len(x) for x in blobs.values()),
        "seconds": b.seconds,
        "verdicts": b.verdicts,
        "deliverables": sorted(p.name for p in b.out_dir.iterdir() if p.is_file()),
        "exhibit_pages_before_after_strip": exhibit_pages(b),
        "answer_key_problems": b.problems,
    }
    (out / "build.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return b
