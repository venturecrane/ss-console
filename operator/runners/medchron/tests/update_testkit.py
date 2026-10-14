"""Synthetic fixtures for the update-into-the-delivered-chronology tests.

Every value is invented. The delivered chronology is authored here as
build_doc-shaped markdown and rendered with the seat's own renderer, so the
tests hold the same contract a real delivery does: the delivered .docx is
what the firm holds, and nothing else of the delivery survives.
"""

from __future__ import annotations

from pathlib import Path

from medchron import config as config_mod
from medchron_testkit import FIRM_CONFIG

INCIDENT = "2026-01-15"

ENTRY_PRE = (
    "12/01/2025\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Routine visit; blood pressure 120/80 recorded at the visit. (Exhibit 1 - p. 1)"
)
ENTRY_POST = (
    "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Cervical strain after the collision, pain rated 6 of 10 at intake. (Exhibit 1 - p. 2)\n\n"
    "All Other Information\nReturn to clinic in four weeks for a recheck of the neck. (Exhibit 1 - p. 2)"
)
PMH = "Prior Medical History\nA routine visit recorded blood pressure 120/80 before the incident. (Exhibit 1 - p. 1)"


def base_md(entries: tuple[str, ...] = (ENTRY_PRE, ENTRY_POST), pmh: str = PMH, limits: bool = True) -> str:
    o = [
        "Alpha Example - Medical Chronology\n",
        "## Treatment Timeline\n",
        "| Medical Provider | Treatment Period | Visits | Reference |",
        "| Example Clinic | 12/01/2025 - 01/20/2026 | 2 | Exhibit 1 - p. 1 |",
        "",
        "## Diagnostic Highlights\n",
        "| ICD Code | Description | First Diagnosed | Reference |",
        "| S13.4XXA | Sprain of ligaments of cervical spine, initial encounter | 01/20/2026 | Exhibit 1 - p. 2 |",
        "",
        "## Medical Chronology\n",
        pmh + "\n",
        *[e + "\n" for e in entries],
        "## Exhibit List\n",
        "| Exhibit No. | Description |",
        "| 1 | Example Clinic - 12-01-2025 - 01-20-2026 (Medical Records) |",
    ]
    if limits:
        o += [
            "",
            "## Records Reviewed and Limitations\n",
            "This chronology was prepared from 1 document on the matter.",
            "* One record set was reviewed in full.",
        ]
    return "\n".join(o)


def cfg() -> config_mod.FirmConfig:
    return config_mod.FirmConfig(path=Path("<test>"), data=dict(FIRM_CONFIG))


def render(md: str, dst: Path, incident: str = INCIDENT) -> Path:
    from medchron.stages.render import render_text

    assert render_text(md, dst, cfg=cfg(), incident_date=incident, log=lambda _m: None) == 0
    return dst


HEADINGS = {"Medical Diagnoses", "All Other Information", "Discharge / Discontinuation in Care"}
