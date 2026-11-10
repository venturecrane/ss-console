"""Staging-matter document fixtures, one module per matter.

WHY A PACKAGE. The fixture documents are prose, and prose is long. Holding two
matters' records in the runner put it over the 500-line file ceiling and made
the runner's actual logic hard to find. One module per matter keeps each set
readable next to its own rationale, which is where the rationale belongs: the
2026-PI-102 set exists to prove judgment under conflict and the 2026-PI-104 set
exists to prove the happy path, and a reader who does not know that will
"repair" the first one.

Each module exports ``MATTER_ID`` and ``DOCS: list[tuple[str, str]]`` of
``(document_name, text)``. Names carry no periods (Smokeball reads the tail
after a "." as a file extension and drops it) and every body carries a [SEED]
marker so nothing here can be mistaken for a real client record.

THE MEDICAL RECORDS ARE NOT HERE. Each matter's records are a generated PDF
set built from a committed case spec (``Fixture.spec``, under
``operator/customers/pilot-smokeball/seed/records/``) by
``python -m jobs.medchron.sample``; the PDFs are build output, never committed.
These modules keep the documents a firm writes or receives around the records
(incident report, wage letter, claim correspondence, billing summary).
``record_names`` reads the spec, so the uploader can refuse a records folder
that does not hold exactly the spec's set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from . import chen_pi102, whitfield_pi104

#: The committed case specs the record PDFs are generated from.
SPECS = Path(__file__).resolve().parents[3] / "customers" / "pilot-smokeball" / "seed" / "records"


@dataclass(frozen=True)
class Fixture:
    """One staging matter's authored record, and what it is FOR.

    ``purpose`` is not decoration. 2026-PI-102 contradicts its own Complaint on
    purpose, and a future reader who does not know that will tidy the
    contradiction away along with the only evidence we have that the drafter
    catches one.
    """

    slug: str
    matter_id: str
    purpose: str
    docs: list[tuple[str, str]]
    #: The case spec the matter's medical record PDFs are generated from.
    spec: Path


FIXTURES: dict[str, Fixture] = {
    "chen-pi102": Fixture(
        slug="chen-pi102",
        matter_id=chen_pi102.MATTER_ID,
        purpose=(
            "Judgment under conflict. Post-suit, and its documents contradict "
            "the matter's own Complaint on incident date and location. The "
            "contradiction is DELIBERATELY PRESERVED: it is the only live "
            "evidence that the drafter detects a conflict, chooses, says why, "
            "and reserves rather than averaging it away. Cannot produce a "
            "cleanly green card 18 — the demand skeleton is a pre-suit CCP 999 "
            "mechanism and suit is filed here."
        ),
        docs=chen_pi102.DOCS,
        spec=SPECS / "chen-pi102.yaml",
    ),
    "whitfield-pi104": Fixture(
        slug="whitfield-pi104",
        matter_id=whitfield_pi104.MATTER_ID,
        purpose=(
            "The happy path. Pre-suit MVA against a commercial carrier, "
            "approaching settlement, and every figure reconciles to the three "
            "lien documents that were already on the matter — the MRI and "
            "orthopedic consultation total exactly the $12,500.00 MedFin "
            "advanced against them. Billed charges only, with three lienholders "
            "asserting, so a draft that totals billed as the loss is wrong and "
            "the record says so."
        ),
        docs=whitfield_pi104.DOCS,
        spec=SPECS / "whitfield-pi104.yaml",
    ),
    "ramirez-pi103": Fixture(
        slug="ramirez-pi103",
        #: 2026-PI-103, ``minor-ramirez`` in seed/manifest.json.
        matter_id="65108bdf-ae94-4f85-990f-152d6c8d6fa2",
        purpose=(
            "A small record set for a minor's dog bite: two treating providers and a "
            "63-day gap between the pediatric follow-up and the plastic surgery "
            "consult, so a chronology shows a treatment gap. Its other documents "
            "(the records summary and the carrier's offer) are seeded by "
            "seed/seed_data.py, so this fixture adds records only."
        ),
        docs=[],
        spec=SPECS / "ramirez-pi103.yaml",
    ),
}


def record_names(fixture: Fixture) -> list[str]:
    """The PDF file names the spec generates, in the order it lists them:
    every document, then every re-sent copy."""
    raw = yaml.safe_load(fixture.spec.read_text(encoding="utf-8"))
    return [f"{d['name']}.pdf" for d in (raw.get("documents") or []) + (raw.get("resends") or [])]


__all__ = ["FIXTURES", "SPECS", "Fixture", "chen_pi102", "record_names", "whitfield_pi104"]
