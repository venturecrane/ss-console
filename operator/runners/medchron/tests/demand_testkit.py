"""Synthetic fixtures for the demand job: a firm-inputs directory (with a
content-free house reference document built here, never a firm's file), a
scripted model client, and a seat with matter fields.

Every name, number and sentence is invented (Example / Exampletown / CLM-0001).
Nothing here is a firm's, a client's, or a matter's.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from medchron_testkit import FakeSeat, doc_row, make_pdf

PARA_KINDS = (
    "date",
    "blank",
    "attn",
    "re_first",
    "re_line",
    "salute",
    "body",
    "heading",
    "subhead",
    "bullet",
    "quote",
    "totalhead",
    "sign",
    "signfirm",
    "exh_head",
    "exh_intro",
    "exh_item",
)
SIGNATURE = "EXAMPLE & EXAMPLE, LLP"
FOOTER = "Confidential Settlement Communication under Evidence Code sections 1119, 1152"


def make_reference(path: Path) -> None:
    """A content-free house reference: one PROTO of each kind, a footer."""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    d = docx.Document()
    for kind in PARA_KINDS:
        p = d.add_paragraph(f"PROTO:{kind}")
        if kind in ("heading", "date", "exh_head"):
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if kind == "heading":
            p.runs[0].bold = True
    for kind, cols, rows in (("banner", 1, 2), ("icd", 2, 3), ("specials", 2, 3), ("demand", 3, 3)):
        t = d.add_table(rows=rows, cols=cols)
        t.cell(0, 0).text = f"PROTO:{kind}"
    d.sections[0].footer.paragraphs[0].text = FOOTER
    d.save(str(path))


PROMPTS = {
    "prompt_digest": "DIGEST-PROMPT. Digest the chunk with cites. End with ## FILES-SEEN.",
    "prompt_condense": "CONDENSE-PROMPT {{WINDOW}}",
    "prompt_gap_audit": "GAP-PROMPT. Produce the records and billing gap audit the brief asks for.",
    "prompt_compose": "COMPOSE-PROMPT. Draft the demand in the house format.",
    "prompt_audit": "AUDIT-PROMPT. Audit the section.",
    "prompt_repair": "REPAIR-PROMPT. Repair the flagged sections.",
}
TEXT_INPUTS = {
    "voice_profile": "Voice: formal, no contractions.",
    "voice_fixed_strings": "Fixed: Demand is hereby made for the full available policy limits.",
    "voice_adjustments": "Preferences only.",
    "skeleton_house": "---\ndate: [INSERT]\n---\n## Summary of Injuries\n## Liability\n## Damages\n## Demand\n## Exhibits\n",
    "drafting_discipline": "## Part I\nRules one to eight.\n## Part II\nGates.\n",
    **PROMPTS,
}


def firm_data(**over: Any) -> dict[str, Any]:
    data = {
        "firm": {"slug": "example", "display_name": "Example & Example, LLP"},
        "models": {
            k: "claude-sonnet-5" for k in ("transcription", "digest", "gap_audit", "compose", "audit", "repair")
        },
        "budget": {
            "per_job_cap_usd": 20.0,
            "monthly_budget_usd": 200.0,
            "usd_per_million_chars": 6.0,
            "usd_per_scanned_page": 0.01,
            "usd_drafting_fixed": 4.0,
            "usd_per_million_condense_chars": 3.3,
        },
        "privilege": {"firm_domains": ["firm.example"], "consumer_domains": ["mail.example"]},
        "selection": {
            "doc_extensions": [".pdf", ".docx", ".msg", ".jpg", ".png"],
            "exclude_folder_patterns": ["(?i)chronology", r"(?i)\(operator\)"],
            "exclude_name_patterns": ["(?i)^chronology", r"(?i)\bmemo\b", r"(?i)\bintake\b", r"(?i)\bnotes?\b"],
        },
        "premise": {
            "scan": {
                "acceptance": ["(?i)accept"],
                "release": ["(?i)release"],
                "prior_demand": ["(?i)demand"],
                "lawsuit": ["(?i)complaint for damages|lawsuit"],
                "funding": ["(?i)funding"],
            },
            "fail_on": ["acceptance", "release"],
            "denial_phrases": ["coverage is denied", "deny coverage"],
            "carrier_phrases": ["claim number", "policy number"],
            "settled_phrases": ["accept the policy limits", "timely acceptance"],
            "litigation_phrases": ["complaint for damages", "superior court of california"],
        },
        "format": {"firm_signature": SIGNATURE, "footer_markers": ["Settlement Communication", "1119, 1152"]},
        "variants": {
            "pre_suit": {
                "skeleton": "skeleton_house",
                "house_reference": "house_reference_house",
                "headings": ["Summary of Injuries", "Liability", "Damages", "Demand"],
                "re_required": ["Claim Number:", "Date of Loss:"],
                "re_one_of": ["Your Insured:", "Our Client:"],
                "mail_line": "CERTIFIED MAIL",
                "banner": True,
                "forbidden": [],
                "re_vs": False,
            }
        },
        "attorneys": {
            "Example Lawyer": {"signer": "Example A. Lawyer", "title": "Attorney at Law", "initials": "EAL/dm"}
        },
        "delivery": {
            "folder_template": "Demand Prep {date} (Operator {job})",
            "gap_audit_name_template": "Gap Audit - {matter_number} - {date}.docx",
            "coverage_report_name_template": "Coverage Posture Report - {matter_number} - {date}.docx",
            "rehearsal_matters": ["OPS-LIBRARY"],
        },
        "levers": {
            "chunk_chars": 120_000,
            "concurrency": 2,
            "digest_max_tokens": 64_000,
            "compose_max_tokens": 64_000,
            "digest_budget_chars": 2_000_000,
            "vendor_lookup_cap": 3,
        },
    }
    for k, v in over.items():
        data[k] = {**data[k], **v}
    return data


def make_inputs(root: Path, **over: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    inputs = {}
    for key, text in TEXT_INPUTS.items():
        p = root / f"{key}.md"
        p.write_text(text, encoding="utf-8")
        inputs[key] = {"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    ref = root / "house-reference.docx"
    make_reference(ref)
    inputs["house_reference_house"] = {"path": ref.name, "sha256": hashlib.sha256(ref.read_bytes()).hexdigest()}
    data = firm_data(**over)
    data["inputs"] = inputs
    (root / "demand-firm.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


MATTER = "0f0f0f0f-0000-4000-8000-000000000001"
LIBRARY = "0f0f0f0f-0000-4000-8000-000000000002"


def job_doc(
    job_id: str = "01DEMANDJOB0000000000000001", file_to: bool = False, cents: int = 0, deliverables=None
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "kind": "demand",
        "slug": "example",
        "matter": {"id": MATTER, "number": "100001"},
        "file_to": {"id": LIBRARY, "number": "OPS-LIBRARY"} if file_to else None,
        "requested_by": "admin@firm.example",
        "request_ref": "<req-1@firm.example>",
        "request_text": "DELIVERABLE 1: gap audit sections A-F. DELIVERABLE 2: draft demand, rules 1-10.",
        "deliverables": deliverables or ["gap_audit", "demand"],
        "submitted_at": "2026-10-06T17:00:00.000Z",
        "month_cents_used": cents,
    }


def make_job(job_dir: Path, **kw: Any) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(job_doc(**kw)), encoding="utf-8")
    return job_dir


class DemandSeat(FakeSeat):
    def __init__(self, *a: Any, facts: dict[str, Any] | None = None, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.numbers = {MATTER: "100001", LIBRARY: "OPS-LIBRARY"}
        self.facts = facts or {
            "matter_number": "100001",
            "client_name": "Alpha Example",
            "responsible_attorney": "Example Lawyer",
            "defense_counsel": [],
            "client_emails": ["client@mail.example"],
            "insurer": "Example Mutual",
            "date_of_loss": "01/15/2026",
            "signer": "Example Attorney",
            "medicals": [{"provider": "Exampletown ER", "charges": [{"amount": "1200.00"}]}],
            "errors": [],
        }

    def matter_facts(self, matter_id: str) -> dict[str, Any]:
        return self.facts

    def matter_number(self, matter_id: str) -> str | None:
        return self.numbers.get(matter_id)


def seat_with(docs: list[tuple[str, str, bytes, str]], **kw: Any) -> DemandSeat:
    """``docs`` are (id, file name, bytes, folder id)."""
    folders = [
        {"id": "f-med", "name": "Medical", "parentId": None, "path": "/Medical"},
        {"id": "f-corr", "name": "Correspondence", "parentId": None, "path": "/Correspondence"},
        {"id": "f-chron", "name": "Medical Chronology", "parentId": None, "path": "/Medical Chronology"},
    ]
    rows = [doc_row(fid, name, folder, len(blob)) for fid, name, blob, folder in docs]
    return DemandSeat(rows, folders, {fid: blob for fid, _n, blob, _f in docs}, **kw)


def standard_docs() -> list[tuple[str, str, bytes, str]]:
    return [
        (
            "d1",
            "ER record 1-15-26.pdf",
            make_pdf(
                [
                    "Exampletown ER. Patient Alpha Example seen 01/15/2026 for neck pain after a collision. Diagnosis cervical strain."
                ]
            ),
            "f-med",
        ),
        (
            "d2",
            "ER bill 1-15-26.pdf",
            make_pdf(["Exampletown ER itemized statement. Total charges $1,200.00."]),
            "f-med",
        ),
        (
            "d3",
            "Carrier letter of acknowledgment.pdf",
            make_pdf(["Example Mutual. Your claim number CLM-0001. Our insured Beta Driver."]),
            "f-corr",
        ),
        ("d4", "Chronology summary.pdf", make_pdf(["A vendor summary that must never be read."]), "f-chron"),
    ]


# ---- the scripted model ------------------------------------------------------------
DRAFT = """---
date: October 6, 2026
attn: Attn: Pat Adjuster | Example Mutual | 1 Example Way | Exampletown, CA 90000
client: Alpha Example
insured: Beta Driver
claim: CLM-0001
dol: January 15, 2026
banner: Time-Limited Policy Limits Demand
expires: This Demand Expires at 5:00 P.M. Pacific Time on Friday, November 6, 2026
salutation: Dear Pat Adjuster:
signer: Example Attorney
---

This letter is submitted on behalf of our client, Alpha Example, who was seen at Exampletown ER on January 15, 2026 for neck pain after a collision (ER record 1-15-26, p. 1).

## Summary of Injuries

| ICD-10 Code | Diagnosis |
|---|---|
| S13.4XXA | Cervical strain (Exampletown ER) |

## Liability

Your insured, Beta Driver, is named in the carrier's letter under claim CLM-0001 (Carrier letter of acknowledgment, p. 1).

## Damages

### Exampletown ER (January 15, 2026)

The emergency department diagnosed a cervical strain (ER record 1-15-26, p. 1). The itemized statement lists total charges of $1,200.00 (ER bill 1-15-26, p. 1).

### Total Medical Specials for Alpha Example

| Provider | Amount |
|---|---|
| Exampletown ER | $1,200.00 |
| Total | $1,200.00 |

{{NOT IN RECORD: wage documents; looked in the matter file, see gap audit item 1}}

## Demand

| Category | Description | Amount |
|---|---|---|
| Past Medical Expenses | Exampletown ER | $1,200.00 |

Demand is hereby made for the full available policy limits.

## Exhibits

The following exhibits are enclosed in support of this demand:

**Exhibit 1 -** ER record 1-15-26

=== ATTORNEY NOTES ===

## HELD OUT PENDING ATTORNEY PRIVILEGE REVIEW

None.
"""
GAP = (
    "# Records and Billing Gap Audit\n\n## A. Referral and order trail\n\n| Provider | Item | Status |\n|---|---|---|\n"
    "| Exampletown ER | follow-up | Missing |\n\n## E. Items\n\n"
    "| Provider | What's missing | Where the file points to it | Basis | Suggested request type | Priority |\n"
    "|---|---|---|---|---|---|\n"
    "| Exampletown ER | radiology bill | ER record 1-15-26, p. 1 | Referenced in record | billing | Blocks demand |\n"
    "| Northfield Imaging | MRI report | ER record 1-15-26, p. 2 | Referenced in record | records | Strengthens demand |\n"
    "| Ridgeview PT | visit notes | ER record 1-15-26, p. 2 | Referenced in record | records | Housekeeping |\n"
    "| Exampletown ER | ED physician bill | ER bill 1-15-26, p. 1 | Billing mismatch | billing | Blocks demand |\n"
    "\n## F. Demand Readiness\n\n- Blocks: items 1 and 4\n"
)
DIGEST = "## MEDICAL | ER record 1-15-26 | 01/15/2026 | (/Medical)\nCervical strain (FILE: ER record 1-15-26, p. 1)\n\n## FILES-SEEN\n- ER record 1-15-26 digested\n"


def _echo_sections(user: str) -> str:
    """A repair that changes nothing: every section it was given, back."""
    out = []
    for part in user.split("\n\n---\n\n"):
        if "## SECTION TO REPAIR: " in part:
            part = part[part.index("## SECTION TO REPAIR: ") :]
            head, _, body = part.split("\n\n### AUDIT FINDINGS", 1)[0].partition("\n\n")
            # the model writes each section under its own heading line; the
            # preamble has none, so it echoes the request's line (draft_run.py)
            out.append(f"{head}\n\n{body}" if head.endswith(": PREAMBLE") else body)
    return "\n\n".join(out)


class _Stream:
    def __init__(self, msg: Any) -> None:
        self.msg = msg

    def __enter__(self) -> "_Stream":
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.msg


class ScriptedClient:
    """Answers by which prompt the system block carries. Records every call."""

    def __init__(self, draft: str = DRAFT, truncate_digest_once: bool = False, audit: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.draft, self.truncate_once = draft, truncate_digest_once
        self.audit = audit or "- claim | SUPPORTED | cite\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0"
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)

    def _answer(self, params: dict[str, Any]) -> str:
        system = json.dumps(params.get("system"))
        if "DIGEST-PROMPT" in system:
            if self.truncate_once:
                self.truncate_once = False
                return "## MEDICAL | partial"
            return DIGEST
        if "AUDIT-PROMPT" in system:
            return self.audit
        if "REPAIR-PROMPT" in system:
            return _echo_sections(params["messages"][-1]["content"])
        if "COMPOSE-PROMPT" in system:
            return self.draft
        if "GAP-PROMPT" in system:
            return GAP
        return "transcribed page text"

    def _msg(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        text = self._answer(params)
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage)

    def _stream(self, **params: Any) -> _Stream:
        return _Stream(self._msg(params))

    def _create(self, **params: Any) -> Any:
        return self._msg(params)

    def stages(self) -> list[str]:
        out = []
        for c in self.calls:
            s = json.dumps(c.get("system"))
            out.append(
                next((k for k in ("DIGEST", "GAP", "REPAIR", "COMPOSE", "AUDIT") if f"{k}-PROMPT" in s), "VISION")
            )
        return out
