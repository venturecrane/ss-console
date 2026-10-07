"""Without a firm house style, rendering is EXACTLY what it was before the
override existed: every part of the .docx, byte for byte.

``fixtures/render_product_defaults.json`` holds the sha256 of every part of
the rendered package for each fixture below,
produced by the renderer as it stood on main before the drafting job's
house-style override (the zip container itself carries write times, so the
parts are compared, not the zip). Every seat and every in-turn skill renders
through this path; a change to it is a product change and must be made on
purpose: regenerate the hashes in the same PR and say why.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from smokeball_connector.docx_format import render_document

# Plain shapes only: the two inline shapes the grammar now reads differently
# (``***bold italic***`` and a code span inside emphasis) rendered wrongly
# before and are not part of the pin.
DISCOVERY = """| PLAINTIFF JANE DOE, | Case No. {{FILL: case number | operative pleading}} |
| v. |  |
| DEFENDANT ACME CORP. |  |

# PLAINTIFF'S SPECIAL INTERROGATORIES TO DEFENDANT, SET ONE

## DEFINITIONS
"INCIDENT" means the collision described in the complaint.

**SPECIAL INTERROGATORY NO. 1:**
Identify each person who witnessed the INCIDENT.

**SPECIAL INTERROGATORY NO. 2:**
State all facts supporting your denial. {{NOT IN RECORD: prior denial}}

| Provider | Balance |
| --- | --- |
| Dr. A | $1,200.00 |
"""

BRIEF = """**SUPERIOR COURT OF THE STATE OF CALIFORNIA**

| PLAINTIFF, | Case No. 1 |
| --- | --- |
| v. | **BRIEF** |

# I. INTRODUCTION

Body text with **bold** and *italic*.

## A. Parties

### 1. Plaintiff

- a bullet

1. A numbered point.

{{ATTORNEY: settlement authority}}
"""

MEMO = """# Question

A memo body.

## Short answer

Text.
"""

FIXTURES = {
    "discovery_set": (DISCOVERY, "discovery_set"),
    "discovery_response": (DISCOVERY, "discovery_response"),
    "mediation_brief": (BRIEF, "mediation_brief"),
    "memo": (MEMO, "memo"),
}


GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "render_product_defaults.json").read_text())


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_render_without_an_override_is_the_pre_override_render(name):
    md, cls = FIXTURES[name]
    blob, _ = render_document(md, cls, None)
    z = zipfile.ZipFile(io.BytesIO(blob))
    got = {n: hashlib.sha256(z.read(n)).hexdigest() for n in sorted(z.namelist())}
    assert got == GOLDEN[name]


def test_the_pin_can_fail():
    from smokeball_connector.docx_classes import HouseStyle

    md, cls = FIXTURES["memo"]
    blob, _ = render_document(md, cls, None, house=HouseStyle(font="Times New Roman", size_pt=12))
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert hashlib.sha256(z.read("word/document.xml")).hexdigest() != GOLDEN["memo"]["word/document.xml"]
