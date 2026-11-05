"""The outcome ledger (``outcome_ledger``) and the record derived from it
(``post_outcomes``).

What these defend, each written so that removing the line it defends makes it
fail:

* each act writes its own row, with the matter number its resolution named,
  keyed by bundle AND message, 0600;
* a tool that RAISES still leaves a row, its status the exception's class
  (the ``finally`` in ``recording``: remove it and the raise tests fail);
* the derivation table: filed and keyed is ``""``; the kind's act missing is
  its issue with "no attempt recorded"; failed is the issue with the tool's
  reason; held with a verdict is that verdict's issue and candidates; nothing
  at all is ``not_attempted``.
"""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Any

import pytest
from smokeball_connector import letter_pages as lp
from smokeball_connector import letter_tools as lt
from smokeball_connector import medicals_tools as mt
from smokeball_connector import outcome_ledger as ol
from smokeball_connector import post_outcomes as po
from smokeball_connector import resolution_token
from smokeball_connector import vendor_invoice_tools as vit
from test_letter_tools import DIGITAL, _Client, _pdf

pytestmark = pytest.mark.usefixtures("logic_enforced")

SHA = "c" * 64


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(ol.JOURNAL_DIR_ENV, str(tmp_path / "journal"))
    monkeypatch.setenv("SMOKEBALL_EXTRACT_CACHE_DIR", str(tmp_path / "cache"))
    resolution_token._reset_for_tests()
    lp.FILED_PAGES._filed.clear()
    lp.FILED_DOCS._docs.clear()
    return tmp_path / "journal"


# ---- the rows, written by the acts themselves --------------------------------


def _file(monkeypatch: pytest.MonkeyPatch, client: _Client, blob: bytes, **kw: Any) -> dict[str, Any]:
    monkeypatch.setattr(lt, "_client", lambda: client)
    monkeypatch.setattr(lt, "fetch_bytes", lambda _c, _u: blob)
    args: dict[str, Any] = {
        "matter_id": "m-1",
        "matter_resolution": resolution_token.mint("m-1", "2026-PI-1", ("client_name", "date_of_loss")),
        "download_url": "spool:" + "0" * 32,
        "file_name": "2026-10-01 Allstate",
        "sha256": hashlib.sha256(blob).hexdigest(),
        "first_page": 1,
        "last_page": 2,
        "message_id": "msg-1",
    }
    args.update(kw)
    return lt.file_attachment_pages_to_matter(**args)


def test_a_filing_writes_its_own_row_with_the_number_its_resolution_named(
    monkeypatch: pytest.MonkeyPatch, _isolate: Path
) -> None:
    blob = _pdf([DIGITAL, DIGITAL])
    out = _file(monkeypatch, _Client(), blob)
    assert out["status"] == "filed"
    sha = hashlib.sha256(blob).hexdigest()
    (row,) = ol.outcomes(sha, "msg-1")
    assert {k: row[k] for k in ("tool", "first_page", "last_page", "matter_id", "matter_number", "status")} == {
        "tool": ol.FILE,
        "first_page": 1,
        "last_page": 2,
        "matter_id": "m-1",
        "matter_number": "2026-PI-1",
        "status": "filed",
    }
    assert row["returned_id"] == out["fileId"]
    path = ol.ledger_path(sha, "msg-1")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ol.outcomes(sha, "msg-2") == [], "keyed by message too: a re-sent PDF starts empty"


def test_a_refused_filing_writes_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = _pdf([DIGITAL, DIGITAL])
    out = _file(monkeypatch, _Client(), blob, matter_resolution="resolution:" + "f" * 32)
    assert out["status"] == "refused"
    (row,) = ol.outcomes(hashlib.sha256(blob).hexdigest(), "msg-1")
    assert row["status"] == "refused" and row["detail"]


def test_a_filing_that_raises_still_leaves_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: remove the ``finally`` in ``outcome_ledger.recording`` and
    this row is never written, so the record would read "not attempted" for a
    letter the tool did try."""
    blob = _pdf([DIGITAL, DIGITAL])

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ConnectionError("tenant went away")

    monkeypatch.setattr(lt, "_file_pages", boom)
    with pytest.raises(ConnectionError):
        _file(monkeypatch, _Client(), blob)
    (row,) = ol.outcomes(hashlib.sha256(blob).hexdigest(), "msg-1")
    assert row["status"] == "ConnectionError" and "tenant went away" in row["detail"]


def test_a_medicals_row_is_keyed_by_the_bill_it_came_from(monkeypatch: pytest.MonkeyPatch) -> None:
    lp.FILED_DOCS.record(
        "m-1", "f-9", "bill.pdf", 3, 4, from_scan=False, sha256=SHA, message_id="msg-1", matter_number="2026-PI-1",
        text="[p.3]\nDOS 07/16/2026\nTOTAL $96.25",
    )  # fmt: skip

    def boom(*_a: Any, **_k: Any) -> Any:
        raise TimeoutError("layouts read timed out")

    monkeypatch.setattr(mt, "_client", lambda: object())
    monkeypatch.setattr(mt, "_select_tab", boom)
    with pytest.raises(TimeoutError):
        mt.add_medicals_row("m-1", "f-9", "Clinic", "96.25", "07/16/2026", "07/16/2026")
    (row,) = ol.outcomes(SHA, "msg-1")
    assert (row["tool"], row["first_page"], row["last_page"], row["status"]) == (ol.MEDICALS, 3, 4, "TimeoutError")
    assert row["matter_number"] == "2026-PI-1"


def test_a_resolve_given_the_group_records_its_verdict_and_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = {
        "verdict": "ambiguous",
        "reason": "2 matters match",
        "candidates": [{"matter_id": "a", "matter_number": "2026-PI-1"}, {"matter_id": "b", "matter_number": "2026-PI-2"}],
    }  # fmt: skip
    monkeypatch.setattr(vit, "_resolve", lambda _c, **_k: dict(verdict))
    monkeypatch.setattr(vit, "_client", lambda: object())
    out = vit.resolve_invoice_matter(client_name="Dana Example", sha256=SHA, first_page=3, last_page=3, message_id="m")
    assert out["verdict"] == "ambiguous"
    (row,) = ol.outcomes(SHA, "m")
    assert row["status"] == "ambiguous" and row["candidates"] == ["2026-PI-1", "2026-PI-2"]


def test_a_resolve_takes_the_group_whole_or_not_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vit, "_resolve", lambda _c, **_k: {"verdict": "none", "ignored": []})
    monkeypatch.setattr(vit, "_client", lambda: object())
    assert vit.resolve_invoice_matter(client_name="Dana", sha256=SHA, first_page=1)["verdict"] == "refused"
    assert vit.resolve_invoice_matter(client_name="Dana")["verdict"] == "none"
    assert ol.outcomes(SHA, "") == []


def test_a_printed_date_of_birth_is_read_in_code_and_a_two_digit_year_is_never_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []

    def resolve(_c: Any, **kw: Any) -> dict[str, Any]:
        seen.append(kw["date_of_birth"])
        return {"verdict": "none", "ignored": []}

    monkeypatch.setattr(vit, "_resolve", resolve)
    monkeypatch.setattr(vit, "_client", lambda: object())
    vit.resolve_invoice_matter(client_name="Dana", date_of_birth_as_printed="04/12/1988")
    out = vit.resolve_invoice_matter(client_name="Dana", date_of_birth_as_printed="04/12/88")
    assert seen == ["1988-04-12", None]
    assert "date_of_birth" in out["ignored"]


def test_a_stage_writes_its_outcome_word(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vit, "_client", lambda: object())
    monkeypatch.setattr(vit, "_stage", lambda _c, **_k: {"status": "possible_duplicate", "existing": [{"id": "e1"}]})
    vit.stage_vendor_invoice(
        "m-1", "resolution:x", "spool:x", "bill", SHA, "Acme", "1", "10/01/2026", "$9", 2, 2, message_id="msg-1"
    )
    (row,) = ol.outcomes(SHA, "msg-1")
    assert (row["tool"], row["status"], row["first_page"]) == (ol.STAGE, "possible_duplicate", 2)


@pytest.mark.parametrize(
    "out, want",
    [
        ({"status": "staged"}, "staged"),
        ({"status": "readback_mismatch", "readback_detail": "amount"}, "figure_not_saved"),
        ({"status": "duplicate", "existing": []}, "possible_duplicate"),
        ({"status": "refused", "reason": "no"}, "refused"),
    ],
)
def test_the_stage_outcome_words(out: dict[str, Any], want: str) -> None:
    assert vit.stage_outcome(out)[0] == want


# ---- the derivation table -----------------------------------------------------


def _r(tool: str, status: str, first: int = 1, last: int = 2, **kw: Any) -> dict[str, Any]:
    return {"tool": tool, "status": status, "first_page": first, "last_page": last, **kw}


FILED = _r(ol.FILE, "filed", matter_id="m-1", matter_number="2026-PI-1")


@pytest.mark.parametrize(
    "kind, rows, want",
    [
        # Filed and nothing more needed.
        ("letter", [FILED], ("2026-PI-1", "", [], "")),
        ("court_paper", [FILED], ("2026-PI-1", "", [], "")),
        # Filed, and the act the kind needs landed.
        ("medical_bill", [FILED, _r(ol.MEDICALS, "written", matter_id="m-1")], ("2026-PI-1", "", [], "")),
        ("settlement_offer", [FILED, _r(ol.NEGOTIATION, "written", matter_id="m-1")], ("2026-PI-1", "", [], "")),
        ("vendor_bill", [_r(ol.STAGE, "staged", matter_number="2026-PI-1")], ("2026-PI-1", "", [], "")),
        # Filed, the act never attempted: the kind's issue, "no attempt recorded".
        ("medical_bill", [FILED], ("2026-PI-1", "tab_not_written", [], po.NO_ATTEMPT)),
        ("settlement_offer", [FILED], ("2026-PI-1", "offer_not_written", [], po.NO_ATTEMPT)),
        ("vendor_bill", [FILED], ("2026-PI-1", "expense_not_staged", [], po.NO_ATTEMPT)),
        # Filed, the act failed: the issue with the tool's own reason.
        (
            "medical_bill",
            [FILED, _r(ol.MEDICALS, "tab_not_written", matter_id="m-1", detail="needs a contact")],
            ("2026-PI-1", "tab_not_written", [], "needs a contact"),
        ),
        (
            "medical_bill",
            [FILED, _r(ol.MEDICALS, "figure_not_saved", matter_id="m-1", detail="InitialInvoiceAmount")],
            ("2026-PI-1", "figure_not_saved", [], "InitialInvoiceAmount"),
        ),
        (
            "settlement_offer",
            [FILED, _r(ol.NEGOTIATION, "possible_duplicate", matter_id="m-1", detail="row 2")],
            ("2026-PI-1", "possible_duplicate", [], "row 2"),
        ),
        (
            "vendor_bill",
            [_r(ol.STAGE, "refused", detail="amount not printed"), FILED],
            ("2026-PI-1", "expense_not_staged", [], "amount not printed"),
        ),
        # A Medicals row on ANOTHER matter does not key this letter.
        (
            "medical_bill",
            [FILED, _r(ol.MEDICALS, "written", matter_id="m-9")],
            ("2026-PI-1", "tab_not_written", [], po.NO_ATTEMPT),
        ),
        # A retried act that landed the second time is landed.
        (
            "medical_bill",
            [FILED, _r(ol.MEDICALS, "tab_not_written", matter_id="m-1"), _r(ol.MEDICALS, "written", matter_id="m-1")],
            ("2026-PI-1", "", [], ""),
        ),
        # Held: the verdict decides the issue and the candidates.
        ("letter", [_r(ol.RESOLVE, "ambiguous", candidates=["A", "B"])], ("", "ambiguous", ["A", "B"], "")),
        ("letter", [_r(ol.RESOLVE, "none", candidates=["A"])], ("", "one_candidate", ["A"], "")),
        ("letter", [_r(ol.RESOLVE, "none", candidates=[])], ("", "no_match", [], "")),
        ("letter", [_r(ol.RESOLVE, "search_failed", detail="HTTP 503")], ("", "search_failed", [], "HTTP 503")),
        (
            "letter",
            [_r(ol.RESOLVE, "unique"), _r(ol.FILE, "refused", detail="page 1 was already filed")],
            ("", "filing_refused", [], "page 1 was already filed"),
        ),
        # Nothing recorded at all.
        ("letter", [], ("", "not_attempted", [], "")),
        # Rows for other pages say nothing about these.
        ("letter", [_r(ol.FILE, "filed", first=3, last=3, matter_number="X")], ("", "not_attempted", [], "")),
    ],
)
def test_the_derivation_table(kind: str, rows: list[dict[str, Any]], want: tuple[Any, ...]) -> None:
    got = po.derive(kind, 1, 2, rows)
    assert (got["placed_on"], got["issue"], got["candidates"], got["detail"]) == want


def test_a_filed_document_that_did_not_read_back_is_listed_for_a_check() -> None:
    got = po.derive("letter", 1, 2, [_r(ol.FILE, "readback_mismatch", matter_number="2026-PI-1")])
    assert got["placed_on"] == "2026-PI-1" and got["issue"] == "figure_not_saved"


def test_an_unwritable_ledger_never_fails_the_act(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv(ol.JOURNAL_DIR_ENV, str(blocker))
    assert ol.append(ol.Outcome(ol.FILE, SHA, "m", status="filed")) is False
    assert ol.outcomes(SHA, "m") == []
