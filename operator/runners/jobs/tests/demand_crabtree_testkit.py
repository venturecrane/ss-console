"""Synthetic fixtures for the firm's CRABTREE demand layout (``layout:
crabtree``, house_crabtree.py): a content-free reference built here with
python-docx (never a firm's file), the variant, and a draft in that layout.

Every name, number and sentence is invented, and the draft's facts are the
ones ``demand_testkit.standard_docs`` carries, so the drafting gate passes it.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml

from demand_testkit import make_inputs

# kind -> (alignment, bold, italic)
PARA = {
    "date": ("center", False, False),
    "mail": ("left", True, False),
    "addr": ("left", False, False),
    "re_first": ("left", False, False),
    "re_line": ("left", False, False),
    "salute": ("left", False, False),
    "lead": ("center", True, False),
    "subhead": ("left", True, False),
    "minorhead": ("left", False, True),
    "section": ("center", True, False),
    "body": ("justify", False, False),
    "bullet": ("justify", False, False),
    "quote": ("justify", False, True),
    "note": ("left", False, True),
    "valediction": ("left", False, False),
    "signfirm": ("left", True, False),
    "signscript": ("left", False, True),
    "signname": ("left", False, False),
    "initials": ("left", False, False),
    "enclosures": ("left", False, False),
    "exh_head": ("center", True, False),
    "exh_item": ("left", False, False),
}
#: kind -> paragraphs in its one cell (style index order)
BOXES = {"banner": 4, "box999": 3}
TABLES = {"providers": 3, "specials": 2, "future": 2}
FOOTER = "CONFIDENTIAL - SETTLEMENT COMMUNICATION · Example Evid. Code §§ 1119, 1152 · Example R. Evid. 408"
LETTERHEAD = "EXAMPLE & EXAMPLE, LLP  ·  1 Main St., Exampleville  ·  {sf_office}"
SF_OFFICE = "505 Example St., Example City, CA 90001"
SIGNER, TITLE, BAR, INITIALS = "Gamma Q. Counsel", "Founding Partner", "999999", "GQC/ex"
HEADINGS = [
    "Liability",
    "Injuries and Medical Treatment",
    "Special Damages",
    "Future Damages and Permanency",
    "Pre-Existing Conditions and Prior Records",
    "General Damages \u2014 Pain and Suffering",
    "Demand",
]


def make_crabtree_reference(path: Path, banded: bool = False) -> None:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    align = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    d = docx.Document()
    for kind, (al, bold, italic) in PARA.items():
        p = d.add_paragraph(f"PROTO:{kind}")
        p.alignment = align[al]
        p.runs[0].bold, p.runs[0].italic = bold or None, italic or None
    for kind, n in BOXES.items():
        t = d.add_table(rows=1, cols=1)
        cell = t.cell(0, 0)
        cell.paragraphs[0].text = f"PROTO:{kind}"
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for i in range(1, n):
            p = cell.add_paragraph(f"style {i}")
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.runs[0].bold = True if i == n - 1 else None
    for kind, cols in TABLES.items():
        t = d.add_table(rows=4 if banded and kind == "providers" else 3, cols=cols)
        t.cell(0, 0).text = f"PROTO:{kind}"
        t.cell(0, 0).paragraphs[0].runs[0].bold = True
    sec = d.sections[0]
    sec.different_first_page_header_footer = True
    sec.first_page_header.paragraphs[0].text = LETTERHEAD
    for f in (sec.footer, sec.first_page_footer):
        f.paragraphs[0].text = FOOTER
        f.add_paragraph("Example & Example, LLP - Page ")
    d.save(str(path))


def variant(**over: Any) -> dict[str, Any]:
    return {
        "skeleton": "skeleton_crabtree",
        "house_reference": "house_reference_crabtree",
        "layout": "crabtree",
        "headings": HEADINGS,
        "re_required": ["Our Client", "Claim Number", "Date of Loss", "Your Insured"],
        "re_one_of": [],
        "mail_line": "VIA ELECTRONIC MAIL AND CERTIFIED MAIL, RETURN RECEIPT REQUESTED",
        "banner": True,
        "forbidden": [],
        "re_vs": False,
        "lead_heading": "PREAMBLE - SUMMARY OF CLAIM",
        "footer_markers": ["CONFIDENTIAL - SETTLEMENT COMMUNICATION", "1119, 1152", "408", "Page"],
        "valediction": "Very truly yours,",
        "banner_title": "TIME-LIMITED POLICY LIMITS DEMAND",
        "banner_statute": "Example Code Civ. Proc. §§ 999-999.5",
        "box_title": "TIME-LIMITED DEMAND - EXAMPLE CODE CIV. PROC. § 999.1",
        "box_lines": ["Date of Demand: {date}", "Response Period: {days} days"],
        "box_deadline": "Deadline: 5:00 P.M. Pacific Time, {expires}",
        "sf_office": SF_OFFICE,
        "sign_for": "Attorneys for Claimant {client}",
        **over,
    }


SKELETON = "---\ndate: [INSERT]\n---\n## Liability\n## Demand\n[DEMAND FIGURE]\n## Index of Exhibits\n"


def make_crabtree_inputs(root: Path, attorney: str = "Gamma Counsel", default: bool = True, **row: Any) -> Path:
    """The standard firm inputs plus the crabtree layout. ``default``: it is the
    firm's pre-suit default (the house layout kept as an unselected format);
    else a format the attorney row names."""
    make_inputs(root)
    ref = root / "house-reference-crabtree.docx"
    make_crabtree_reference(ref)
    sk = root / "skeleton-crabtree.md"
    sk.write_text(SKELETON, encoding="utf-8")
    cfg = root / "demand-firm.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data["inputs"]["house_reference_crabtree"] = {
        "path": ref.name,
        "sha256": hashlib.sha256(ref.read_bytes()).hexdigest(),
    }
    data["inputs"]["skeleton_crabtree"] = {"path": sk.name, "sha256": hashlib.sha256(sk.read_bytes()).hexdigest()}
    entry = {"signer": SIGNER, "title": TITLE, "initials": INITIALS, "bar_number": BAR, **row}
    if default:
        data["formats"] = {"house": {"pre_suit": data["variants"]["pre_suit"]}}
        data["variants"]["pre_suit"] = variant()
    else:
        data["formats"] = {"crabtree": {"pre_suit": variant()}}
        entry["format"] = "crabtree"
    data["attorneys"][attorney] = entry
    cfg.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return root


def edit_config(root: Path, fn: Any) -> None:
    cfg = root / "demand-firm.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data = fn(copy.deepcopy(data)) or data
    cfg.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


CRABTREE_DRAFT = """---
date: October 6, 2026
attn: Pat Adjuster | Example Mutual | 1 Example Way | Exampletown, CA 90000
client: Alpha Example
claim: CLM-0001
dol: January 15, 2026
place: {{NOT IN RECORD: the place of the collision; looked in the matter file, see gap audit item 1}}
insured: Beta Driver
expires: Thursday, November 5, 2026
salutation: Dear Pat Adjuster:
---

### Medical Providers and Billings to Date

| Provider | Treatment and Dates of Service | Billed |
|---|---|---|
| Exampletown ER | Emergency care, January 15, 2026 | $1,200.00 |
| TOTAL | | $1,200.00 |

^ Charges to date; treatment is ongoing.

### Liability in Brief

- Beta Driver is named as the insured under claim CLM-0001 (Carrier letter of acknowledgment, p. 1).

Alpha Example was seen at Exampletown ER on January 15, 2026 for neck pain after a collision (ER record 1-15-26, p. 1).

## Liability

### The Insured

Your insured, Beta Driver, is named in the carrier's letter under claim CLM-0001 (Carrier letter of acknowledgment, p. 1).

> A statement of the law, in italics.

## Injuries and Medical Treatment

**Exampletown ER - Emergency Department, Exampletown (January 15, 2026).** The emergency department diagnosed a cervical strain (ER record 1-15-26, p. 1).

#### Cervical Spine

- A cervical strain (ER record 1-15-26, p. 1).

## Special Damages

| Description | Amount |
|---|---|
| Exampletown ER (billed) | $1,200.00 |
| TOTAL | $1,200.00 |

## Future Damages and Permanency

{{NOT IN RECORD: a physician's estimate of future care; looked in the matter file, see gap audit item 2}}

| Anticipated Future Care | Estimated Cost |
|---|---|
| {{FILL: future care}} | {{FILL: cost}} |

## Pre-Existing Conditions and Prior Records

{{NOT IN RECORD: prior records; looked in the matter file, see gap audit item 3}}

## General Damages - Pain and Suffering

> An epigraph, in italics.

Alpha Example was seen at Exampletown ER on January 15, 2026 for neck pain (ER record 1-15-26, p. 1).

## Demand

Demand is hereby made for **[DEMAND FIGURE]**, contingent on the declarations page, an asset declaration and a declaration of no other coverage, to be accepted in writing by Thursday, November 5, 2026.

## Index of Exhibits

Exhibit 1: ER record 1-15-26, Exampletown ER.

Exhibit 2: ER bill 1-15-26, $1,200.00.

=== ATTORNEY NOTES ===

## HELD OUT PENDING ATTORNEY PRIVILEGE REVIEW

None.
"""
