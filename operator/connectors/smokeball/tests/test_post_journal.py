"""The post journal (``record_post_batch``, ``get_post_list``).

What these defend, each written so that removing the line it defends makes it
fail:

* every page of a bundle is recorded exactly once, so a held letter cannot
  drop out of the daily list by being left out of the record;
* where a letter was filed, its issue and its candidates are DERIVED from the
  tools' outcome rows (``outcome_ledger``), never typed: a letter passed with
  any of them is refused and the refusal names what they derive to (the
  derivation table itself is pinned in ``test_outcome_ledger.py``);
* a letter derived as filed must be pages this process filed, and a letter
  derived as held must not be;
* the closed vocabulary decides what is listed, and a filed letter with no
  issue is not listed unless it is a court paper;
* the daily list is authored only by an enabled skill with a recipient AND a
  cron row; anything less means the intake still replies per bundle;
* the files are 0600 and a retry of the same bundle replaces its record.
"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from smokeball_connector import letter_pages as lp
from smokeball_connector import outcome_ledger as ol
from smokeball_connector import post_journal as pj

pytestmark = pytest.mark.usefixtures("logic_enforced")

SHA = "a" * 64
MESSAGE = "msg-1"


@pytest.fixture(autouse=True)
def _journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(pj.JOURNAL_DIR_ENV, str(tmp_path / "journal"))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.setattr(pj, "NOW", lambda: datetime(2026, 10, 29, 17, 0, tzinfo=timezone.utc))
    lp.FILED_PAGES._filed.clear()
    return tmp_path / "journal"


def _letters() -> list[dict[str, Any]]:
    return [
        {"first_page": 1, "last_page": 2, "sender": "Allstate", "kind": "settlement_offer"},
        {"first_page": 3, "last_page": 3, "sender": "Radiology Associates"},
        {"first_page": 4, "last_page": 5, "sender": "Superior Court", "kind": "court_paper"},
    ]


def _row(tool: str, first: int, last: int, status: str, message_id: str = MESSAGE, **kw: Any) -> None:
    outcome = ol.Outcome(tool, SHA, message_id, first_page=first, last_page=last, status=status, **kw)
    assert ol.append(outcome)


def _outcomes() -> None:
    """What the tools recorded for the standard bundle: the offer filed and
    keyed on 2026-PI-102, page 3 resolved to one candidate, the court paper
    filed on 2026-PI-105."""
    _row(ol.FILE, 1, 2, "filed", matter_id="m-102", matter_number="2026-PI-102", returned_id="f-1")
    _row(ol.NEGOTIATION, 1, 2, "written", matter_id="m-102", matter_number="2026-PI-102")
    _row(ol.RESOLVE, 3, 3, "none", candidates=["2026-PI-104"])
    _row(ol.FILE, 4, 5, "filed", matter_id="m-105", matter_number="2026-PI-105")


def _filed(*ranges: tuple[int, int]) -> None:
    for first, last in ranges:
        assert lp.FILED_PAGES.claim(SHA, first, last) is None


def _record(letters: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "message_id": MESSAGE,
        "file_name": "Post 10-29.pdf",
        "sha256": SHA,
        "page_count": 5,
        "letters": _letters() if letters is None else letters,
        "received_at": "2026-10-29T16:14:00Z",
    }
    args.update(kw)
    return pj.record_post_batch(**args)


def _only_record(journal: Path) -> dict[str, Any]:
    files = list((journal / "batches").glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text())


def test_a_bundle_is_recorded_from_what_the_tools_did(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    _outcomes()
    out = _record()
    assert out["status"] == "recorded", out
    # The held letter and the court paper; the filed offer is not listed.
    assert out["to_list"] == 2
    assert out["daily_list"] is False
    record = _only_record(_journal)
    assert [(e["first_page"], e["placed_on"], e["issue"], e["candidates"]) for e in record["letters"]] == [
        (1, "2026-PI-102", "", []),
        (3, "", "one_candidate", ["2026-PI-104"]),
        (4, "2026-PI-105", "", []),
    ]
    assert out["letters_recorded"][1]["issue"] == "one_candidate"
    assert record["filing_unverified"] is False
    path = next((_journal / "batches").glob("*.json"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "typed",
    [{"placed_on": "2026-PI-102"}, {"issue": ""}, {"candidates": []}, {"placed_on": "2026-PI-999", "issue": ""}],
)
def test_a_letter_passed_with_a_derived_field_is_refused_naming_the_derived_values(
    typed: dict[str, Any], _journal: Path
) -> None:
    """The model learns the fields are code's: the refusal says what they
    derive to. Even the RIGHT value is refused, because a typed field is the
    channel a wrong one would ride."""
    _filed((1, 2), (4, 5))
    _outcomes()
    letters = _letters()
    letters[0] = {**letters[0], **typed}
    out = _record(letters)
    assert out["status"] == "refused"
    assert "derived from what the tools recorded" in out["reason"]
    assert "placed_on '2026-PI-102'" in out["reason"] and "issue ''" in out["reason"]
    assert not (_journal / "batches").exists()


def test_a_letter_nothing_was_recorded_for_is_listed_as_not_attempted(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    _row(ol.FILE, 1, 2, "filed", matter_id="m-102", matter_number="2026-PI-102")
    _row(ol.NEGOTIATION, 1, 2, "written", matter_id="m-102")
    _row(ol.FILE, 4, 5, "filed", matter_id="m-105", matter_number="2026-PI-105")
    out = _record()
    assert out["status"] == "recorded" and out["to_list"] == 2
    assert _only_record(_journal)["letters"][1]["issue"] == "not_attempted"


def test_rows_from_another_message_never_describe_this_bundle(_journal: Path) -> None:
    """The same PDF sent again is a new run: its letters start with nothing."""
    _outcomes()
    out = _record(message_id="msg-2")
    assert out["status"] == "recorded"
    assert {e["issue"] for e in _only_record(_journal)["letters"]} == {"not_attempted"}


def test_a_letter_recorded_on_pages_other_than_those_filed_refuses(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    _outcomes()
    letters = [
        {"first_page": 1, "last_page": 1, "sender": "Allstate", "kind": "settlement_offer"},
        {"first_page": 2, "last_page": 3, "sender": "Radiology Associates"},
        {"first_page": 4, "last_page": 5, "sender": "Superior Court", "kind": "court_paper"},
    ]
    out = _record(letters)
    assert out["status"] == "refused" and "pages 1-2 were filed as one document" in out["reason"]


def test_pages_staged_as_a_bill_cannot_be_recorded_as_a_letter(_journal: Path) -> None:
    _filed((1, 2), (3, 3), (4, 5))
    _outcomes()
    _row(ol.STAGE, 3, 3, "staged", matter_id="m-104", matter_number="2026-PI-104")
    out = _record()
    assert out["status"] == "refused" and "record their kind as vendor_bill" in out["reason"]


def test_a_letter_left_out_of_the_record_refuses(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    _outcomes()
    letters = [e for e in _letters() if e["first_page"] != 3]
    out = _record(letters)
    assert out["status"] == "refused" and "page 3" in out["reason"]
    assert not (_journal / "batches").exists()


def test_a_page_recorded_twice_refuses(_journal: Path) -> None:
    letters = _letters()
    letters[1] = {**letters[1], "first_page": 2}
    assert _record(letters)["status"] == "refused"


def test_a_held_letter_whose_pages_were_filed_refuses(_journal: Path) -> None:
    """The two code sources disagree (the in-process filed-pages ledger says
    page 3 went somewhere; no outcome row says so): the record refuses."""
    _filed((1, 2), (3, 3), (4, 5))
    _outcomes()
    out = _record()
    assert out["status"] == "refused" and "pages 3-3 were filed" in out["reason"]


def test_a_filed_letter_whose_pages_were_not_filed_refuses(_journal: Path) -> None:
    _filed((4, 5))
    _outcomes()
    out = _record()
    assert out["status"] == "refused" and "pages 1-2 were not filed" in out["reason"]


def test_after_a_restart_the_record_is_kept_and_marked_unverified(_journal: Path) -> None:
    """The in-process ledger forgot the bundle; the outcome rows on disk did
    not, so the record is still derived, and marked unverified."""
    _outcomes()
    out = _record()
    assert out["status"] == "recorded" and out["filing_unverified"] is True
    assert _only_record(_journal)["letters"][0]["placed_on"] == "2026-PI-102"


@pytest.mark.parametrize(
    "bad",
    [{"kind": "brochure"}, {"sender": " "}, {"summary": "the adjuster offers 25000"}, {"first_page": 0}],
)
def test_the_letter_shape_is_closed(bad: dict[str, Any], _journal: Path) -> None:
    _outcomes()
    letters = _letters()
    letters[1] = {**letters[1], **bad}
    assert _record(letters)["status"] == "refused"


def test_a_placed_letter_with_a_check_is_listed(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    _row(ol.FILE, 1, 2, "filed", matter_id="m-102", matter_number="2026-PI-102")
    _row(ol.NEGOTIATION, 1, 2, "possible_duplicate", matter_id="m-102", detail="the tab already shows 25000 on 10-20")
    _row(ol.RESOLVE, 3, 3, "none", candidates=["2026-PI-104"])
    _row(ol.FILE, 4, 5, "filed", matter_id="m-105", matter_number="2026-PI-105")
    out = _record()
    assert out["to_list"] == 3
    first = _only_record(_journal)["letters"][0]
    assert first["issue"] == "possible_duplicate"
    assert first["detail"] == "the tab already shows 25000 on 10-20"


def test_a_bundle_nothing_was_filed_from(_journal: Path) -> None:
    out = _record([], page_count=31, bundle_issue="unreadable", bundle_detail="the scan could not be read through")
    assert out["status"] == "recorded" and out["to_list"] == 1
    assert _record(_letters(), bundle_issue="unreadable")["status"] == "refused"
    assert _record([])["status"] == "refused", "an empty record with no bundle issue says nothing"


def test_a_retry_replaces_the_record_and_keeps_its_time(_journal: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _filed((1, 2), (4, 5))
    _outcomes()
    _record()
    monkeypatch.setattr(pj, "NOW", lambda: datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc))
    _record()
    assert _only_record(_journal)["recorded_at"] == "2026-10-29T17:00:00Z"


def _yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cron: bool, recipient: str | None) -> None:
    settings = f"\n        settings:\n          recipient: '{recipient}'" if recipient else ""
    cron_rows = "\n    cron:\n      - skill: post-intake-daily-list\n        schedule: '45 16 * * 1-5'" if cron else ""
    path = tmp_path / "customer.yaml"
    path.write_text(
        "personas:\n  - slug: operator\n    skills:\n      - name: post-intake-daily-list\n"
        "        enabled: true" + settings + cron_rows + "\n"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def test_the_daily_list_needs_a_recipient_and_a_cron_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _yaml(tmp_path, monkeypatch, cron=True, recipient="office@firm.example")
    assert pj.daily_list_authored() is True
    _yaml(tmp_path, monkeypatch, cron=False, recipient="office@firm.example")
    assert pj.daily_list_authored() is False
    _yaml(tmp_path, monkeypatch, cron=True, recipient=None)
    assert pj.daily_list_authored() is False


def test_a_sent_list_is_read_by_its_reference(_journal: Path) -> None:
    (_journal / "lists").mkdir(parents=True)
    (_journal / "lists" / "2026-10-29.json").write_text(json.dumps({"items": [{"n": 1, "message_id": "msg-1"}]}))
    assert pj.get_post_list("2026-10-29") == {
        "status": "found",
        "list_ref": "2026-10-29",
        "items": [{"n": 1, "message_id": "msg-1"}],
    }
    assert pj.get_post_list("2026-10-30")["status"] == "not_found"
    assert pj.get_post_list("../state")["status"] == "refused"
