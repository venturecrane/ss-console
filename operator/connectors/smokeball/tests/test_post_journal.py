"""The post journal (``record_post_batch``, ``get_post_list``).

What these defend, each written so that removing the line it defends makes it
fail:

* every page of a bundle is recorded exactly once, so a held letter cannot
  drop out of the daily list by being left out of the record;
* a letter recorded as filed must be pages this process filed, and a letter
  recorded as held must not be;
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
from smokeball_connector import post_journal as pj

SHA = "a" * 64


@pytest.fixture(autouse=True)
def _journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(pj.JOURNAL_DIR_ENV, str(tmp_path / "journal"))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.setattr(pj, "NOW", lambda: datetime(2026, 10, 29, 17, 0, tzinfo=timezone.utc))
    lp.FILED_PAGES._filed.clear()
    return tmp_path / "journal"


def _letters() -> list[dict[str, Any]]:
    return [
        {"first_page": 1, "last_page": 2, "sender": "Allstate", "kind": "settlement_offer", "placed_on": "2026-PI-102"},
        {"first_page": 3, "last_page": 3, "sender": "Radiology Associates", "issue": "one_candidate",
         "candidates": ["2026-PI-104"]},
        {"first_page": 4, "last_page": 5, "sender": "Superior Court", "kind": "court_paper", "placed_on": "2026-PI-105"},
    ]  # fmt: skip


def _filed(*ranges: tuple[int, int]) -> None:
    for first, last in ranges:
        assert lp.FILED_PAGES.claim(SHA, first, last) is None


def _record(letters: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "message_id": "msg-1",
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


def test_a_bundle_is_recorded_and_counts_what_will_be_listed(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    out = _record()
    assert out["status"] == "recorded", out
    # The held letter and the court paper; the filed offer is not listed.
    assert out["to_list"] == 2
    assert out["daily_list"] is False
    record = _only_record(_journal)
    assert [e["first_page"] for e in record["letters"]] == [1, 3, 4]
    assert record["filing_unverified"] is False
    path = next((_journal / "batches").glob("*.json"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_letter_left_out_of_the_record_refuses(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    letters = [e for e in _letters() if e["first_page"] != 3]
    out = _record(letters)
    assert out["status"] == "refused" and "page 3" in out["reason"]
    assert not (_journal / "batches").exists()


def test_a_page_recorded_twice_refuses(_journal: Path) -> None:
    letters = _letters()
    letters[1] = {**letters[1], "first_page": 2}
    assert _record(letters)["status"] == "refused"


def test_a_held_letter_whose_pages_were_filed_refuses(_journal: Path) -> None:
    _filed((1, 2), (3, 3), (4, 5))
    out = _record()
    assert out["status"] == "refused" and "pages 3-3 were filed" in out["reason"]


def test_a_filed_letter_whose_pages_were_not_filed_refuses(_journal: Path) -> None:
    _filed((4, 5))
    out = _record()
    assert out["status"] == "refused" and "pages 1-2 were not filed" in out["reason"]


def test_after_a_restart_the_record_is_kept_and_marked_unverified(_journal: Path) -> None:
    out = _record()
    assert out["status"] == "recorded" and out["filing_unverified"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"issue": "one_candidate", "candidates": []},
        {"issue": "ambiguous", "candidates": ["2026-PI-104"]},
        {"issue": "no_match", "candidates": ["2026-PI-104"]},
        {"issue": "no_match", "candidates": [], "placed_on": "2026-PI-104"},
        {"issue": "something_else", "candidates": []},
        {"kind": "brochure"},
        {"sender": " "},
        {"summary": "the adjuster offers 25000"},
    ],
)
def test_the_vocabulary_is_closed(bad: dict[str, Any], _journal: Path) -> None:
    letters = _letters()
    letters[1] = {**letters[1], **bad}
    assert _record(letters)["status"] == "refused"


def test_a_filed_letter_must_say_where(_journal: Path) -> None:
    letters = _letters()
    letters[0] = {k: v for k, v in letters[0].items() if k != "placed_on"}
    assert _record(letters)["status"] == "refused"


def test_a_placed_letter_with_a_check_is_listed(_journal: Path) -> None:
    _filed((1, 2), (4, 5))
    letters = _letters()
    letters[0] = {**letters[0], "issue": "possible_duplicate", "detail": "the tab already shows 25000 — on 10-20"}
    out = _record(letters)
    assert out["to_list"] == 3
    assert _only_record(_journal)["letters"][0]["detail"] == "the tab already shows 25000 , on 10-20"


def test_a_bundle_nothing_was_filed_from(_journal: Path) -> None:
    out = _record([], page_count=31, bundle_issue="unreadable", bundle_detail="the scan could not be read through")
    assert out["status"] == "recorded" and out["to_list"] == 1
    assert _record(_letters(), bundle_issue="unreadable")["status"] == "refused"
    assert _record([])["status"] == "refused", "an empty record with no bundle issue says nothing"


def test_a_retry_replaces_the_record_and_keeps_its_time(_journal: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _filed((1, 2), (4, 5))
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
