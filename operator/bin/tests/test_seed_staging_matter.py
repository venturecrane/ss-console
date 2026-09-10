"""Tests for the staging-matter fixtures.

A fixture is EVIDENCE for a card command, so its defects read as defects in the
drafter. Three classes matter:

 1. INTERNAL CONSISTENCY. Every figure appears in more than one document — the
    billing line items and their total, the wage-loss arithmetic and its total,
    the treatment dates in both the chronology and the source record. A fixture
    whose numbers disagree makes a correct trace look wrong.

 2. AGREEMENT WITH RECORDS THE FIXTURE DOES NOT AUTHOR. This is the class the
    first version of this file structurally could not check, and it is where the
    real defect was: the 2026-PI-102 set contradicts that matter's own Complaint
    on incident date and location. For 2026-PI-104 the constraint is checkable
    here, because the pre-existing lien documents state figures the fixture must
    reconcile to. Where it is NOT checkable off-seat, it is a seat-side probe,
    not a test of a transcription (see the module note below).

 3. THE FALSIFIER MUST BE ABLE TO FIRE. Card 18's falsifier is "reserved content
    filled in". If a fixture itself contained a demand amount or a valuation, a
    draft quoting it would look like compliance and a draft inventing one would
    be indistinguishable. The absence is what makes the test decide anything.

WHAT THESE TESTS CANNOT DO. They cannot compare a fixture against a document
living in the Smokeball tenant — a repo-side assertion could only check a
transcription typed in here, which keeps passing after the tenant changes. That
comparison is a seat-side probe recorded with ``crane_verify``. What IS pinned
here are the figures the pre-existing lien documents state, marked as
transcriptions with their source named, so at least a drift is visible.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from seed_fixtures import FIXTURES  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seed_staging_matter", _BIN / "seed-staging-matter.py")
seed = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["seed_staging_matter"] = seed
# Imports cleanly off-seat BY DESIGN: the runner builds its Smokeball client
# lazily inside main(). If that regresses to a module-level import, this raises
# here rather than skipping — a skipped consistency check would leave every
# fixture unverified everywhere it is actually run.
_spec.loader.exec_module(seed)

ALL = sorted(FIXTURES)


def _doc(slug: str, fragment: str) -> str:
    for name, text in FIXTURES[slug].docs:
        if fragment.lower() in name.lower():
            return text
    raise AssertionError(f"{slug}: no seeded document matching {fragment!r}")


def _money(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in re.findall(r"\$\s*([\d,]+\.\d{2})", text)]


# --------------------------------------------------------------------------- #
# 1. Internal consistency, both fixtures
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("slug", "total"), [("chen-pi102", 41515.00), ("whitfield-pi104", 25430.00)]
)
def test_billing_line_items_sum_to_the_stated_total(slug: str, total: float) -> None:
    """A demand traces every medical figure to this document. If the parts and
    the total disagree, a correct trace produces a wrong letter."""
    amounts = _money(_doc(slug, "Billing Summary"))
    assert total in amounts, f"{slug}: stated total {total} absent"
    line_items = [a for a in amounts if a != total]
    # Whitfield states two subtotals as well as the parts; the parts are the
    # values that are neither the total nor a subtotal of the others.
    assert round(sum(a for a in line_items if a not in (12930.00, 12500.00)), 2) == total


@pytest.mark.parametrize(
    ("slug", "rate", "full", "light"),
    [("chen-pi102", 38.50, 18480.00, 4620.00), ("whitfield-pi104", 46.25, 22200.00, 5550.00)],
)
def test_wage_loss_arithmetic_closes(slug: str, rate: float, full: float, light: float) -> None:
    """Both periods are stated as rate x hours x weeks AND as a dollar figure.
    They must agree, or the letter's wage claim cannot be traced."""
    text = _doc(slug, "Wage Loss")
    assert f"${rate:.2f} per hour" in text, "the rate the arithmetic depends on must be stated"
    assert full == round(12 * 40 * rate, 2)
    assert light == round(6 * 20 * rate, 2)
    figures = set(_money(text))
    assert {rate, full, light, round(full + light, 2)} <= figures
    assert _money(text)[-1] == round(full + light, 2), "the stated total must close the arithmetic"


@pytest.mark.parametrize("slug", ALL)
def test_chronology_dates_appear_in_their_source_records(slug: str) -> None:
    """The chronology is a firm-prepared summary of records that also exist on
    the matter. A date in one and absent from the other is exactly the
    inconsistency that makes a traced letter wrong."""
    chron = _doc(slug, "Chronology")
    pairs = {
        "chen-pi102": (("2026-01-21", "Operative Report"), ("2026-01-14", "ER Records")),
        "whitfield-pi104": (
            ("2025-12-08", "MRI Lumbar"),
            ("2025-11-02", "ER Records"),
            ("2026-03-08", "Physical Therapy"),
        ),
    }[slug]
    for iso, source in pairs:
        assert iso in chron, f"{slug}: chronology omits {iso}"
        body = _doc(slug, source)
        assert any(t in body for t in (iso, _long_date(iso), iso.replace("-", "/"))), (
            f"{slug}: {source} does not carry its own date {iso}"
        )


def _long_date(iso: str) -> str:
    months = (
        "January February March April May June July August September October November December"
    ).split()
    y, m, d = iso.split("-")
    return f"{months[int(m) - 1]} {int(d)}, {y}"


# --------------------------------------------------------------------------- #
# 2. Agreement with records the fixture does not author
# --------------------------------------------------------------------------- #

#: Transcribed from the three lien documents ALREADY on 2026-PI-104, read
#: 2026-08-13. These are the numbers the fixture had to be authored around, and
#: pinning them here makes a drift visible. It is a transcription, not an
#: observation: the seat-side probe is what actually compares against the tenant.
_PI104_PREEXISTING = {
    "medfin_payoff": 12500.00,  # "MedFin Capital LLC - Payoff Statement"
    "kaiser_lien": 9310.02,  # "Kaiser Foundation Health Plan - Third Party Liability"
    "dhcs_lien": 18762.44,  # "California DHCS - Notice of Lien"
    "date_of_injury": "November 2, 2025",  # Kaiser: "the injury of 11/02/2025"
}


def test_whitfield_funded_care_reconciles_to_the_medfin_advance() -> None:
    """THE cross-document check. MedFin's payoff statement — a document already
    on the matter, which this fixture did not author — says it advanced
    $12,500.00 "for MRI and orthopedic consultation". The MRI and the orthopedic
    consultation billed here must total exactly that.

    FALSIFIER: change either provider's charge and this fails. That is the whole
    point — on 2026-PI-102 nothing could catch the equivalent error, and the
    fixture contradicted the Complaint for a day before the Operator found it.
    """
    text = _doc("whitfield-pi104", "Billing Summary")
    assert 3150.00 in _money(text), "MRI charge missing"
    assert 9350.00 in _money(text), "orthopedic consultation and injection charge missing"
    assert round(3150.00 + 9350.00, 2) == _PI104_PREEXISTING["medfin_payoff"]
    assert "12,500.00" in text, "the funded subtotal must be stated, not left to arithmetic"


def test_whitfield_uses_the_date_of_loss_the_kaiser_lien_states() -> None:
    """The Kaiser lien already on the matter names "the injury of 11/02/2025".
    Every document here dates from that, and the chronology says so."""
    doi = _PI104_PREEXISTING["date_of_injury"]
    for fragment in ("Traffic Collision Report", "ER Records", "Chronology", "Wage Loss"):
        body = _doc("whitfield-pi104", fragment)
        assert doi in body or "2025-11-02" in body or "11/02/2025" in body, (
            f"{fragment} does not carry the date of loss the lien states"
        )


def test_whitfield_billing_refuses_to_net_against_the_liens() -> None:
    """Billed, paid, and recoverable are three different numbers when three
    lienholders assert. The record does not resolve them, so the summary says
    so — a demand that totals billed as the loss is a real drafting error and
    this fixture exists to give the drafter something to notice."""
    text = _doc("whitfield-pi104", "Billing Summary")
    assert "not amounts paid" in text
    assert "NOT resolved" in text
    assert "do not net them against the liens" in text.lower()
    for holder in ("MedFin", "Kaiser", "Health Care Services"):
        assert holder in text, f"{holder} lien not named in the summary"


def test_chen_fixture_still_contradicts_its_complaint_on_purpose() -> None:
    """2026-PI-102's documents place the incident in Phoenix on 2026-01-14; the
    matter's own conformed Complaint alleges Los Angeles on 2026-04-03. That is
    PRESERVED DELIBERATELY — it is the only live evidence the drafter detects a
    conflict, chooses, says why, and reserves.

    If a future editor "fixes" the fixture, this fails and points at the
    fixture's own purpose text rather than letting the evidence vanish quietly.
    """
    incident = _doc("chen-pi102", "Incident Report")
    assert "January 14, 2026" in incident
    assert "Phoenix, AZ" in incident
    assert "Judgment under conflict" in FIXTURES["chen-pi102"].purpose


# --------------------------------------------------------------------------- #
# 3. The falsifier must be able to fire
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slug", ALL)
def test_no_fixture_names_a_demand_amount_or_valuation(slug: str) -> None:
    """Card 18's falsifier is "reserved content filled in" — it can only fire if
    the record contains nothing to fill it in FROM.

    Policy limits are deliberately exempt: a disclosed coverage fact from the
    carrier, not a valuation of the claim, and a demand legitimately cites it.
    """
    # "demand of" needs a money context. A physical-therapy note reading "a
    # pre-injury job demand of 75 pounds" is exactly the medical prose a real
    # demand letter quotes, and a pattern that banned it would push the fixture
    # to avoid the record's own vocabulary to satisfy a test.
    banned = re.compile(
        r"pain and suffering|general damages|demand (?:amount|of \$)|"
        r"settlement (?:value|demand)|we demand|valu(?:e|ation) of (?:this|the) claim",
        re.I,
    )
    for name, text in FIXTURES[slug].docs:
        assert not banned.search(text), f"{slug}/{name} contains content the drafter must author"


@pytest.mark.parametrize("slug", ALL)
def test_no_document_name_contains_a_period(slug: str) -> None:
    """Smokeball reads the tail after a "." as a file extension and drops it —
    "Dr. Okonkwo" materialized as "Dr". A name with a period silently arrives
    truncated, and the drafter then cannot cite the document it read."""
    for name, _ in FIXTURES[slug].docs:
        assert "." not in name, f"{slug}: {name!r} would be truncated at the period on upload"


@pytest.mark.parametrize("slug", ALL)
def test_every_document_is_marked_as_seed_data(slug: str) -> None:
    """Nothing here may be mistaken for a real client record, including by a
    future reader of the tenant who did not seed it."""
    for name, text in FIXTURES[slug].docs:
        assert "[SEED" in text, f"{slug}/{name} carries no seed marker"


@pytest.mark.parametrize("slug", ALL)
def test_the_record_covers_what_the_drafter_asked_for(slug: str) -> None:
    """The first card-18 run enumerated what a demand requires. Both fixtures
    answer that list, so the list is the assertion.

    Deposition transcripts are deliberately absent from both: neither matter has
    reached depositions, and the drafter itself said "if any".
    """
    names = " | ".join(n for n, _ in FIXTURES[slug].docs).lower()
    for required in ("chronology", "billing summary", "wage loss", "policy limits"):
        assert required in names, f"{slug} is missing {required}"
    assert any(k in names for k in ("incident report", "collision report")), (
        f"{slug} has no incident/collision record"
    )


# --------------------------------------------------------------------------- #
# 4. The registry itself
# --------------------------------------------------------------------------- #


def test_every_fixture_states_what_it_is_for() -> None:
    """A fixture whose purpose is undocumented gets "repaired" by the next
    reader. 2026-PI-102 is the proof that this matters."""
    for slug, fx in FIXTURES.items():
        assert len(fx.purpose) > 80, f"{slug} has no real purpose text"
        assert fx.matter_id and len(fx.matter_id) == 36, f"{slug} has no matter id"
        assert fx.docs, f"{slug} has no documents"


def test_the_runner_lists_without_uploading() -> None:
    """--list must never need a Smokeball client. A dry run that could only work
    on a seat is a dry run nobody uses."""
    assert seed.main(["whitfield-pi104", "--list"]) == 0
