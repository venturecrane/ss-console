"""`medchron probe <gate>`: the registered runtime controls' planted
violations, run on the seat against the installed package (ss#2614, the
ADR 0087 acceptance: "a planted violation in each runner gate is refused on
the Machine"). Each probe builds a throwaway synthetic matter under a temp
dir, plants exactly the violation the registry names, runs the gate module
the registry points at, and prints one line: `REFUSED <gate>: <why>` on the
expected refusal, `UNEXPECTED_PASS <gate>` otherwise. Exit 0 only on the
refusal, because the refusal is the property being proven.

$0 and offline: none of the four gates needs the model or the matter to say
no. The synthetic firm config lives here (not in the tests) so the seat can
run the probes without the firm's private tables and the tests can reuse it.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import yaml

from . import config as config_mod, job as job_mod
from .gates import claim_audit, cross_client, extractive, provenance
from .stages.base import StageRun

SYNTHETIC_FIRM: dict[str, Any] = {
    "firm": {"slug": "example-firm", "display_name": "Example Firm"},
    "selection": {
        "exclude_folder_classes": [r"(?i)vendor chron", r"(?i)client correspondence", r"(?i)^photos$"],
        "shared_folder_classes": [r"(?i)^emails"],
        "root_pdfs": True,
        "doc_extensions": [".pdf", ".docx", ".doc", ".tif", ".tiff", ".jpg", ".jpeg", ".png"],
    },
    "folders": {"status_suffix_regex": r"(?i)\s*[-]\s*(have|need)\b.*$"},
    "providers": {
        "aliases": [{"match": r"example clinic|ex clinic", "label": "Example Clinic"}],
        "unresolved_label": "(unattributed)",
    },
    "coverage": {
        "exclusions": [
            {"match": r"(?i)retainer|fee agreement", "reason": "engagement document, not a chronology source"},
            {"match": r"(?i)pay stub|salary", "reason": "wage-loss documentation, not a treatment record"},
        ]
    },
    "billing": {"name_patterns": [r"(?i)\bbill", r"(?i)ledger", r"(?i)invoice"], "suspect_amount_cents": 50000000},
    "nonrecord": {"page_classes": [{"name": "INDEX", "patterns": [r"this list is computer generated"]}]},
    "units": {"exclude_name_patterns": [r"(?i)retainer"]},
    "format": {"subsections": ["Medical Diagnoses", "All Other Information"], "font": "Calibri"},
    "delivery": {
        "folder_template": "MEDICAL CHRONOLOGY - {CLIENT} {MM-DD-YY} by Example Operator",
        "chronology_name_template": "{CLIENT} - Medical Chronology {MM-DD-YY}.docx",
        "worksheet_glob": "* - Medical Billing Worksheet *.docx",
    },
    "models": {
        "tiers": {
            "transcription": "claude-sonnet-5",
            "mechanical": "claude-sonnet-5",
            "composition": "claude-opus-5",
            "audit": "claude-sonnet-5",
            "judgment": "claude-opus-5",
        }
    },
    "levers": {"batch_stages": [], "audit_mode": "image", "cache": True, "compose_max_tokens": 128000},
    "chronology": {"treatment_gap_days": 45, "pre_incident_history": "include"},
    # Invented posture, not any firm's: the four cost controls exist here so the
    # seat probes and the tests have a config that satisfies the closed key set.
    "budget": {"per_job_cap_usd": 150.0, "usd_per_million_chars": 10.0, "monthly_budget_usd": 500.0,
               "single_matter_page_threshold": 2500, "usd_per_scanned_page": 0.03,
               "usd_per_audit_claim": 0.06},
    "pipeline": {},
}

_MIN_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def _scene(tmp: Path, *, joint: bool = False) -> tuple[StageRun, Path, list[str]]:
    firm = tmp / "firm.yaml"
    firm.write_text(yaml.safe_dump(SYNTHETIC_FIRM, sort_keys=False), encoding="utf-8")
    data_root = tmp / "data"
    slug_dir = data_root / "example-matter"
    slug_dir.mkdir(parents=True)
    units = [{"unit": "alpha", "client_name": "Alpha Example", "name_token": "Alpha", "surname": "Example",
              "dob": "01/01/1970", **({"folder_prefix": "/Alpha_Example"} if joint else {})}]
    if joint:
        units.append({"unit": "beta", "client_name": "Beta Example", "name_token": "Beta", "surname": "Sample",
                      "dob": "02/02/1980", "folder_prefix": "/Beta_Sample"})
    job_dir = tmp / "job"
    job_dir.mkdir()
    (job_dir / "job.yaml").write_text(yaml.safe_dump({
        "slug": "example-matter", "matter": {"number": "0000-XX-000", "id": "probe", "title": "Probe"},
        "units": units, "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "data_root": str(data_root),
    }, sort_keys=False), encoding="utf-8")
    log: list[str] = []
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm))
    sr = StageRun(job=job, cfg=cfg, unit=job.units[0], slug_dir=slug_dir, decided={}, log=log.append,
                  seat_factory=lambda: None, date_stamp="01-01-26")
    return sr, slug_dir, log


def probe_provenance(tmp: Path) -> tuple[bool, str]:
    """A pulled file that reached no unit, is not a duplicate, not excluded,
    not a documented orphan: the coverage gate must hold."""
    sr, d, log = _scene(tmp)
    (d / "units").mkdir()
    (d / "units" / "alpha.json").write_text(json.dumps([{"id": "a", "name": "unexplained", "ext": ".pdf"}]))
    (d / "raw_manifest.jsonl").write_text(json.dumps({"id": "a", "name": "unexplained", "ext": ".pdf", "ok": True}) + "\n")
    (d / "runs" / "alpha").mkdir(parents=True)
    (d / "runs" / "alpha" / "entries_final.md").write_text("01/02/2026\nX | Medical Diagnoses\n\nY. (Exhibit 1 - p. 1)\n")
    (d / "out" / "alpha").mkdir(parents=True)
    (d / "out" / "alpha" / "page_map.json").write_text("[]")
    code = provenance.check(sr)
    return code == 1, f"exit {code}; " + " | ".join(log)[-300:]


def probe_claim_audit(tmp: Path) -> tuple[bool, str]:
    """A final document carrying a claim that was never audited in its final
    form: the audit gate must refuse delivery."""
    sr, d, _ = _scene(tmp)
    (d / "runs" / "alpha").mkdir(parents=True)
    (d / "runs" / "alpha" / "final-chronology.md").write_text(
        "## Medical Chronology\n\nA planted claim long enough to be audited. (Exhibit 1 - p. 1)\n## Exhibit List\n")
    (d / "out" / "alpha").mkdir(parents=True)
    (d / "out" / "alpha" / "Exhibit 1 - X - 01-02-2026 (Medical Records).pdf").write_bytes(_MIN_PDF)
    ok, summary = claim_audit.check(d, "alpha")
    return (not ok) and summary.get("never", 0) == 1, json.dumps(summary)


def probe_extractive(tmp: Path) -> tuple[bool, str]:
    """The strip falsifier: every cited page classified as droppable means the
    classifier would strip the evidence the document rests on; the dry run
    must refuse to apply."""
    sr, d, log = _scene(tmp)
    (d / "runs" / "alpha").mkdir(parents=True)
    (d / "runs" / "alpha" / "final-chronology.md").write_text(
        "## Medical Chronology\n\nA claim. (Exhibit 1 - p. 1)\n## Exhibit List\n")
    (d / "out" / "alpha").mkdir(parents=True)
    (d / "out" / "alpha" / "Exhibit 1 - X - 01-02-2026 (Medical Records).pdf").write_bytes(_MIN_PDF)
    (d / "nonrecord.json").write_text(json.dumps(
        {"1": {"pages": 1, "blocks": [], "drop_pages": [1], "unknown": [], "cited_collision": [1]}}))
    code = extractive.dry_run(sr)
    return code == 1, f"exit {code}; " + " | ".join(log)[-300:]


def probe_cross_client(tmp: Path) -> tuple[bool, str]:
    """A joint matter where a page filed under one client carries only the
    other client's date of birth: the identity check must flag it."""
    _, d, _ = _scene(tmp, joint=True)
    (d / "units").mkdir()
    (d / "text").mkdir()
    (d / "units.json").write_text(json.dumps({"alpha": {"surname": "Example", "dob": "01/01/1970"},
                                             "beta": {"surname": "Sample", "dob": "02/02/1980"}}))
    (d / "text" / "a.txt").write_text("Patient Example seen 01/01/1970 DOB, follow up.")
    (d / "text" / "b.txt").write_text("CMS-1500 patient DOB 02021980 claim form.")
    (d / "units" / "alpha.json").write_text(json.dumps([
        {"id": "a", "name": "note", "text_path": str(d / "text" / "a.txt")},
        {"id": "b", "name": "claim", "text_path": str(d / "text" / "b.txt")}]))
    (d / "units" / "beta.json").write_text("[]")
    found, checked, missing = cross_client.flags(d)
    flagged = [f["file"] for f in found]
    return flagged == ["claim"] and not missing, f"checked {checked}, flagged {flagged}, missing {missing}"


PROBES: dict[str, Callable[[Path], tuple[bool, str]]] = {
    "claim_audit": probe_claim_audit,
    "extractive": probe_extractive,
    "cross_client": probe_cross_client,
    "provenance": probe_provenance,
}


def run_probe(name: str) -> int:
    fn = PROBES.get(name)
    if fn is None:
        print(f"unknown probe {name!r}; one of {', '.join(sorted(PROBES))}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="medchron-probe-") as tmp:
        try:
            refused, detail = fn(Path(tmp))
        except Exception as exc:  # noqa: BLE001 - a probe fault is not a finding, and says so
            print(f"PROBE_FAULT {name}: {type(exc).__name__}: {exc}")
            return 2
    if refused:
        print(f"REFUSED {name}: {detail}")
        return 0
    print(f"UNEXPECTED_PASS {name}: {detail}")
    return 1
