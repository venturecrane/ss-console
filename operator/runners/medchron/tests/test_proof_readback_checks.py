"""Proven delivery's shared instruments: the read-back every lane files through
(``proof/readback.py``) and the code checks every lane's review runs
(``proof/checks.py``).

Each test is a falsifier: a planted defect is caught (a file never listed, a
file at our size whose bytes are someone else's, a figure or a date the
sources never state, system vocabulary in firm-facing text) and the clean case
passes, so neither a missing guard nor a false hold goes unnoticed.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from medchron.proof import checks, readback

BYTES = b"the chronology as built\n" * 40
SHA = hashlib.sha256(BYTES).hexdigest()


class FakeSeat:
    """The seat calls read-back makes: a folder listing that may lag, a minted
    download URL, and the fetch."""

    def __init__(self, *, appears_after: int = 0, served: bytes = BYTES, size: int | None = None) -> None:
        self.listings = 0
        self.appears_after = appears_after
        self.served = served
        self.size = len(BYTES) if size is None else size

    def list_files(self, matter_id):
        self.listings += 1
        rows = [{"id": "other", "name": "Unrelated", "ext": ".pdf", "folderId": "folder-1", "size": 9}]
        if self.listings > self.appears_after:
            rows.append({"id": "up-7", "name": "Chronology", "ext": ".docx", "folderId": "folder-1", "size": self.size})
        return rows

    def mint(self, matter_id, ids):
        return [{"id": ids[0], "url": f"https://files.example.test/{ids[0]}"}]

    def fetch(self, url, dest, _progress):
        Path(dest).write_bytes(self.served)


EXPECTED = [{"name": "Chronology.docx", "sha256": SHA, "bytes": len(BYTES)}]


def _confirm(seat: FakeSeat, tmp_path: Path, tries: int = 4):
    slept: list[float] = []
    out = readback.confirm_files(
        seat, "m-1", "folder-1", EXPECTED, tmp_path / "scratch", tries=tries, pause=5.0, sleep=slept.append
    )
    return out, slept


def test_a_file_that_appears_late_at_its_size_and_hash_is_confirmed(tmp_path):
    out, slept = _confirm(FakeSeat(appears_after=2), tmp_path)
    assert out["readback"] == "confirmed" and out["folder_id"] == "folder-1"
    assert out["files"] == [{"name": "Chronology.docx", "file_id": "up-7", "size": len(BYTES), "sha256": SHA}]
    assert slept == [5.0, 5.0]  # it waited for the vendor's index, then stopped
    assert not list((tmp_path / "scratch").iterdir())  # the download is not kept


def test_a_file_at_our_size_whose_bytes_differ_is_a_mismatch(tmp_path):
    other = b"someone else's file, same length\n"
    other = (other * 40)[: len(BYTES)]
    with pytest.raises(readback.ReadbackMismatch) as exc:
        _confirm(FakeSeat(served=other), tmp_path)
    assert exc.value.name == "Chronology.docx"


def test_a_file_never_listed_is_short_after_every_try(tmp_path):
    seat = FakeSeat(appears_after=99)
    with pytest.raises(readback.ReadbackShort) as exc:
        _confirm(seat, tmp_path, tries=3)
    assert exc.value.names == ["Chronology.docx"] and seat.listings == 3


def test_a_file_listed_at_the_wrong_size_is_short(tmp_path):
    with pytest.raises(readback.ReadbackShort):
        _confirm(FakeSeat(size=len(BYTES) - 1), tmp_path, tries=2)


def test_an_empty_delivery_is_never_confirmed(tmp_path):
    with pytest.raises(readback.ReadbackShort):
        readback.confirm_files(FakeSeat(), "m-1", "folder-1", [], tmp_path, sleep=lambda _s: None)


# ---- checks ----------------------------------------------------------------------


def test_money_reads_dollar_figures_in_cents():
    assert checks.money("A balance of $12,500.00, then $300 and $1,234.56.") == [1_250_000, 30_000, 123_456]


@pytest.mark.parametrize(
    "sources",
    ["Total billed: 12500", "Total billed: $12,500", "billed 12,500.00 in all", "the bill was 12,500, plus costs"],
)
def test_a_figure_the_sources_state_in_any_spelling_is_known(sources):
    assert checks.money_not_in("The specials total $12,500.00.", sources) == []


def test_a_figure_the_sources_never_state_is_named():
    text = "The specials total $12,500.00 and the policy limit is $15,000."
    assert checks.money_not_in(text, "Total billed: 12500") == [1_500_000]
    assert checks.money_not_in(text, "Total billed: 12500", also=[1_500_000]) == []
    assert checks.money_not_in("$500 was paid.", "the bill was 12,500, plus costs") == [50_000]


def test_dates_read_every_written_form():
    got = checks.dates("Seen 2026-01-15, again 2/3/2026 and on March 4, 2026; then Sept. 9, 2025.")
    assert got == [date(2026, 1, 15), date(2026, 2, 3), date(2026, 3, 4), date(2025, 9, 9)]


def test_a_date_absent_from_the_sources_and_a_future_date_are_named():
    text = "Visit on 01/15/2026; follow-up on February 2, 2026; next on 2027-05-01."
    sources = "Office note dated 2026-01-15. Follow-up 2/2/2026."
    assert checks.dates_not_in(text, sources) == [date(2027, 5, 1)]
    assert checks.future(checks.dates(text), date(2026, 10, 13)) == [date(2027, 5, 1)]
    assert checks.future(checks.dates(sources), date(2026, 10, 13)) == []


@pytest.mark.parametrize(
    "planted",
    [
        "Provider not identified in this chunk.",
        "The auditor flagged this visit.",
        "Counted by code from the record.",
        "As drafted by the model, the total is final.",
        "Per the job's rule, this page is omitted.",
    ],
)
def test_system_vocabulary_in_firm_facing_text_is_caught(planted):
    assert checks.system_vocabulary(planted)


@pytest.mark.parametrize(
    "clean",
    [
        "Responding party answers as required by Code of Civil Procedure section 2030.050.",
        "The vehicle was the model year 2019 sedan; the make and the model of the vehicle are not disputed.",
        "Plaintiff could not perform the job's physical duties for six weeks.",
    ],
)
def test_a_firms_own_english_is_not_system_vocabulary(clean):
    assert checks.system_vocabulary(clean) == []


def test_a_sentinel_line_is_caught_and_prose_is_not():
    assert checks.sentinels("Body\n=== ATTORNEY NOTES ===\nmore") == ["=== ATTORNEY NOTES ==="]
    assert checks.sentinels("Equal signs == in prose are fine.") == []


def test_fmt_cents():
    assert checks.fmt_cents(1_250_000) == "$12,500.00" and checks.fmt_cents(5) == "$0.05"
