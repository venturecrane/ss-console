"""Synthetic fixtures for the drafting job: a firm-inputs directory written to
``drafting-firm.yaml``'s schema, an envelope, a seat with matter fields and a
caption record, and a scripted model.

Every name, number and sentence is invented (Example / Exampletown / CV-0001).
Nothing here is a firm's, a client's, or a matter's.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from demand_testkit import LIBRARY, MATTER, DemandSeat, ScriptedClient as _DemandClient, _Stream
from medchron_testkit import make_pdf

CLASSES = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")
SECTIONS = [
    "I. INTRODUCTION",
    "II. PARTIES AND COUNSEL",
    "III. DAMAGES SUMMARY",
    "IV. STATEMENT OF FACTS",
    "V. LIABILITY",
    "VI. DAMAGES",
    "VII. CAUSATION",
    "VIII. STATEMENT OF INSURANCE COVERAGE",
    "IX. STATEMENT OF PRIOR NEGOTIATIONS",
    "X. SUMMARY OF CASE VALUE",
    "XI. CONCLUSION",
]
DECL = """<!-- authoring comment that must never reach the document -->

# DECLARATION FOR ADDITIONAL DISCOVERY (CODE CIV. PROC. § 2030.050)

4. I have previously propounded a total of `{{FILL: number of interrogatories previously propounded to this party, form and special | prior sets}}` interrogatories to this party, of which `{{FILL: number of those that were not official form interrogatories | prior sets}}` were not official form interrogatories.

5. This set contains `{{FILL: number of special interrogatories in this set | the count of labeled requests}}` specially prepared interrogatories.
"""
POS = "<!-- authoring comment -->\n\n# PROOF OF SERVICE\n\nOn `{{FILL: date of service | at service}}`, I served the foregoing document.\n"


#: A fictional firm's authored layout, the drafting-firm.yaml format.layout block.
LAYOUT: dict[str, Any] = {
    "mediation_brief": {
        "line_spacing_pt": 24,
        "justify": True,
        "first_line_indent_in": 0.5,
        "heading_indent_in": [0, 0.5, 1.0],
        "heading_underline": [False, True, True],
        "centered_court_lines": True,
        "bold_italic_heading_indent_in": 1.0,
        "page_numbers_always": True,
        "footer_title": "Plaintiff's Mediation Brief",
    },
    "discovery_set": {"line_spacing": 2.0, "item_line_spacing": 2.0, "item_space_after_pt": 0},
    "discovery_response": {"line_spacing": 2.0, "item_line_spacing": 2.0, "item_space_after_pt": 0},
    "memo": {},
    "depo_outline": {"page_numbers_always": True},
}


def firm_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "firm": {"slug": "example", "display_name": "Example & Example, LLP"},
        "models": {k: "claude-sonnet-5" for k in ("transcription", "digest", "compose", "audit", "repair")},
        "budget": {
            "per_job_cap_usd": 100.0,
            "monthly_budget_usd": 750.0,
            "usd_per_million_chars": 3.0,
            "usd_per_scanned_page": 0.012,
            "usd_drafting_fixed": 8.0,
        },
        "privilege": {"firm_domains": ["firm.example"], "consumer_domains": ["mail.example"]},
        "selection": {
            "doc_extensions": [".pdf", ".docx", ".msg"],
            "exclude_folder_patterns": ["(?i)chronology", r"(?i)\(operator"],
            "exclude_name_patterns": [r"(?i)\bmemo\b"],
        },
        "style": "house-style.md",
        "format": {
            "font": "Times New Roman",
            "size_pt": 12,
            "mediation_brief_sections": list(SECTIONS),
            "discovery_label_style": "all caps, bold, underlined",
            "depo_outline_page_numbers": True,
            "plain_letter_paper": True,
            "layout": LAYOUT,
        },
        "classes": {
            c: {
                "skeleton": f"skeleton-{c}.md",
                "prompts": {s: f"prompts/{s}-{c}.md" for s in ("digest", "compose", "audit", "repair")},
                "exemplars": [],
            }
            for c in CLASSES
        },
        "attachments": {"decl_2030_050": "decl-2030-050.md", "pos": "pos.md"},
        "delivery": {"rehearsal_matters": ["OPS-LIBRARY"]},
    }
    for k, v in over.items():
        data[k] = v
    return data


def make_inputs(root: Path, **over: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data = firm_data(**over)
    files = {
        "house-style.md": "Times New Roman 12. Roman numerals centered and bold only.",
        "decl-2030-050.md": DECL,
        "pos.md": POS,
    }
    for c in CLASSES:
        files[f"skeleton-{c}.md"] = f"# SKELETON {c}\n"
        files[f"prompts/digest-{c}.md"] = "DIGEST-PROMPT. Digest with cites. End with ## FILES-SEEN."
        files[f"prompts/compose-{c}.md"] = f"COMPOSE-PROMPT for {c}."
        files[f"prompts/audit-{c}.md"] = "AUDIT-PROMPT. Audit the section."
        files[f"prompts/repair-{c}.md"] = "REPAIR-PROMPT. Repair."
    inputs = {}
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        inputs[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    data["inputs"] = inputs
    (root / "drafting-firm.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def envelope(job_id: str = "01DRAFTJ0B000000000000000A", cls: str = "mediation_brief", file_to: bool = False) -> dict:
    return {
        "kind": "drafting",
        "job_id": job_id,
        "matter_id": MATTER,
        "matter_number": "100001",
        "document_class": cls,
        "requester": "admin@firm.example",
        "message_ref": "<req-1@firm.example>",
        "request_text": f"Please draft the {cls.replace('_', ' ')}.",
        "file_to_matter_id": LIBRARY if file_to else None,
        "file_to_matter_number": "OPS-LIBRARY" if file_to else None,
    }


def make_job(job_dir: Path, cents: int = 0, **kw: Any) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    doc = {**envelope(**kw), "slug": "example", "month_cents_used": cents, "allowance_remaining": 5}
    (job_dir / "job.json").write_text(json.dumps(doc), encoding="utf-8")
    return job_dir


COMPLAINT = (
    "ALPHA EXAMPLE (SBN 000001)\nalpha@firm.example\nAttorneys for Plaintiff\n"
    "SUPERIOR COURT OF THE STATE OF CALIFORNIA\nFOR THE COUNTY OF EXAMPLETOWN\n"
    "GAMMA EXAMPLE,\nPlaintiff,\nv.\nDELTA EXAMPLE,\nDefendant.\nCase No. CV-0001\n"
    "COMPLAINT FOR DAMAGES"
)


class DraftingSeat(DemandSeat):
    def __init__(self, *a: Any, record: dict[str, Any] | None = None, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.facts = {**self.facts, "client_name": "Gamma Example", "client_names": ["Gamma Example"]}
        self.record = record or {
            "case_number": "CV-0010",
            "court": "SUPERIOR COURT OF CALIFORNIA, COUNTY OF EXAMPLETOWN",
            "plaintiffs": ["Gamma Exampel"],
            "defendants": ["Delta Example"],
            "attorney_email": "",  # read, and empty
        }

    def caption_record(self, matter_id: str) -> dict[str, Any]:
        return self.record


def seat_with(docs: list[tuple[str, str, bytes, str]], **kw: Any) -> DraftingSeat:
    from medchron_testkit import doc_row

    folders = [
        {"id": "f-med", "name": "Medical", "parentId": None, "path": "/Medical"},
        {"id": "f-plead", "name": "Pleadings", "parentId": None, "path": "/Pleadings"},
    ]
    rows = [doc_row(fid, name, folder, len(blob)) for fid, name, blob, folder in docs]
    return DraftingSeat(rows, folders, {fid: blob for fid, _n, blob, _f in docs}, **kw)


def standard_docs() -> list[tuple[str, str, bytes, str]]:
    return [
        ("d1", "ER record 1-15-26.pdf", make_pdf(["Exampletown ER. Patient seen 01/15/2026 for neck pain."]), "f-med"),
        (
            "d2",
            "ER bill 1-15-26.pdf",
            make_pdf(["Exampletown ER itemized statement 01/15/2026 total charges $1,200.00"]),
            "f-med",
        ),
        ("d3", "Complaint 2-1-26.pdf", make_pdf([COMPLAINT]), "f-plead"),
    ]


COURT = (
    "Alpha Example, Esq.\nAttorneys for Plaintiff\n\n**SUPERIOR COURT OF THE STATE OF CALIFORNIA**\n"
    "**COUNTY OF EXAMPLETOWN**\n\n| GAMMA EXAMPLE, Plaintiff, v. DELTA EXAMPLE, Defendant. | Case No. CV-0001 |\n"
    "| --- | --- |\n\n"
)
BRIEF = (
    COURT
    + "\n\n".join(f"# {s}\n\nThe record supports this section (ER record 1-15-26, p. 1)." for s in SECTIONS)
    + "\n\n=== ATTORNEY NOTES ===\n\n## NOT IN RECORD\n\nNone.\n"
)
HOWELL_ROWS = (
    '[{"provider": "Exampletown ER", "kind": "bill", "row_type": "stated_total", "payer": null, '
    '"date_of_service": "01/15/2026", "billed": "$1,200.00", "paid": null, "outstanding": null}]'
)


class ScriptedClient(_DemandClient):
    """Answers by the prompt the system block carries; the billing reader is
    recognized by its own in-code prompt."""

    def __init__(self, draft: str = BRIEF, **kw: Any) -> None:
        super().__init__(draft=draft, **kw)

    def _answer(self, params: dict[str, Any]) -> str:
        if "You read ONE billing document" in json.dumps(params.get("system")):
            return HOWELL_ROWS
        return super()._answer(params)


__all__ = ["SimpleNamespace", "_Stream", "LIBRARY", "MATTER"]
