"""post-intake-daily-list pre_run: one email a day naming only the post that needs a person.

What these pin, each with the harm it prevents:

* only the lines that need a person are listed: a letter it could not place,
  a filing whose keying did not land, a whole bundle nothing was filed from,
  and a court paper to calendar; a letter that simply filed is never listed;
* a day with nothing to list sends nothing (no envelope, no wake);
* an unauthored recipient sends nothing, to anyone (no imposed default);
* one list covers every bundle since the last one, and the cursor means a
  bundle is never listed twice and never skipped;
* the numbered list a reply is answered from is written beside the journal;
* every matter number the body prints is handed to the send gate as read;
* the vocabulary is the connector's, exactly.

Run::

    cd operator && python3 -m pytest skills/post-intake-daily-list/tests -q
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SKILL_DIR = Path(__file__).resolve().parents[1]
_CONNECTOR = _SKILL_DIR.parents[1] / "connectors" / "smokeball" / "smokeball_connector" / "post_journal.py"
EVENING = datetime(2026, 10, 30, 0, 45, tzinfo=timezone.utc)  # 17:45 on 10-29 in Los Angeles
_CFG = {
    "business_hours": {"timezone": "America/Los_Angeles"},
    "personas": [
        {
            "slug": "operator",
            "skills": [
                {"name": "post-intake-daily-list", "enabled": True, "settings": {"recipient": "office@firm.example"}}
            ],
            "cron": [{"skill": "post-intake-daily-list", "schedule": "45 17 * * 1-5"}],
        }
    ],
}


@pytest.fixture
def pr(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("post_intake_daily_list_pre_run", _SKILL_DIR / "pre_run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SMD_POST_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    return module


def _batch(tmp_path: Path, name: str, letters: list[dict], *, at: str = "2026-10-29T17:14:00Z", **kw) -> None:
    record = {
        "version": 1,
        "recorded_at": at,
        "received_at": at,
        "message_id": f"msg-{name}",
        "file_name": f"{name}.pdf",
        "sha256": "a" * 64,
        "page_count": max([e["last_page"] for e in letters] or [0]),
        "letters": letters,
        "bundle_issue": "",
        "bundle_detail": "",
        "filing_unverified": False,
    }
    record.update(kw)
    path = tmp_path / "journal" / "batches" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))


def _letter(first, last, sender, *, kind="letter", placed_on="", issue="", candidates=(), detail=""):
    return {
        "first_page": first,
        "last_page": last,
        "sender": sender,
        "kind": kind,
        "placed_on": placed_on,
        "issue": issue,
        "candidates": list(candidates),
        "detail": detail,
    }


def _the_day(tmp_path: Path) -> None:
    """The rehearsal's bundle: an offer and a medical bill that keyed, one unplaceable letter."""
    _batch(
        tmp_path,
        "post",
        [
            _letter(1, 2, "Allstate", kind="settlement_offer", placed_on="2026-PI-102"),
            _letter(3, 4, "Northside Imaging Center", kind="medical_bill", placed_on="2026-PI-105"),
            _letter(5, 5, "Coastal Records Copy", issue="no_match"),
        ],
    )


def _run(pr, cfg=_CFG, *, at=EVENING, dry=False) -> dict:
    out = io.StringIO()
    with redirect_stdout(out):
        assert pr.run(cfg, clock=lambda: at, dry=dry) == 0
    return json.loads(out.getvalue().strip().splitlines()[-1])


def _envelope(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "home" / ".smd" / "pre_run" / "post-intake-daily-list.dispatch.json").read_text())


def test_only_the_unplaceable_letter_is_listed(pr, tmp_path):
    _the_day(tmp_path)
    verdict = _run(pr)
    assert verdict["wakeAgent"] is True and verdict["dispatch_expected"] is True
    assert verdict["asks"] == 1 and verdict["calendar"] == 0
    dispatch = _envelope(tmp_path)["dispatches"][0]
    assert dispatch["recipients"] == ["office@firm.example"]
    assert dispatch["subject"] == "Post list 2026-10-29: 1 need a word from you"
    body = dispatch["full_body"]
    assert '1. Scan of 10:14 AM ("post.pdf"), pages 5-5, letter from Coastal Records Copy: no matter matched' in body
    assert "Allstate" not in body and "Northside" not in body, "a letter that filed and keyed is never listed"
    assert "1 scan, 3 letters, 2 filed on their matters." in body
    assert "—" not in body
    # stdout carries counts, never a sender or a matter number
    assert "Coastal" not in json.dumps(verdict)


def test_a_day_with_nothing_to_list_sends_nothing(pr, tmp_path):
    _batch(tmp_path, "post", [_letter(1, 2, "Allstate", placed_on="2026-PI-102")])
    verdict = _run(pr)
    assert verdict == {"wakeAgent": False, "status": "nothing_to_list", "dry_run": False, "scans": 1, "letters": 1,
                       "asks": 0, "calendar": 0}  # fmt: skip
    assert not (tmp_path / "home" / ".smd" / "pre_run" / "post-intake-daily-list.dispatch.json").exists()
    assert not (tmp_path / "journal" / "lists").exists()


def test_no_recipient_authored_sends_nothing(pr, tmp_path):
    _the_day(tmp_path)
    cfg = json.loads(json.dumps(_CFG))
    cfg["personas"][0]["skills"][0]["settings"] = {}
    verdict = _run(pr, cfg)
    assert verdict["wakeAgent"] is False and verdict["status"] == "recipient_unauthored"
    assert not (tmp_path / "home" / ".smd").exists()


def test_every_kind_of_line_reads_as_a_question_or_a_check(pr, tmp_path):
    _batch(
        tmp_path,
        "a",
        [
            _letter(1, 1, "Radiology Associates", issue="one_candidate", candidates=["2026-PI-104"]),
            _letter(2, 2, "Mercury Insurance", issue="ambiguous", candidates=["2026-PI-101", "2026-PI-103"]),
            _letter(3, 4, "Superior Court", kind="court_paper", placed_on="2026-PI-105"),
            _letter(5, 5, "Valley Clinic", kind="medical_bill", placed_on="2026-PI-106", issue="tab_not_written",
                    detail="the bill prints no total"),
            _letter(6, 6, "Geico", kind="settlement_offer", placed_on="2026-PI-102", issue="possible_duplicate",
                    detail="the tab already shows 25000.00 on 2026-10-20"),
        ],
    )  # fmt: skip
    _batch(tmp_path, "b", [], at="2026-10-29T21:02:00Z", page_count=31, bundle_issue="unreadable",
           bundle_detail="the scan could not be read all the way through")  # fmt: skip
    verdict = _run(pr)
    assert verdict["asks"] == 5 and verdict["calendar"] == 1
    body = _envelope(tmp_path)["dispatches"][0]["full_body"]
    assert "the only matter I found is 2026-PI-104. Is it that one?" in body
    assert "it could be matter 2026-PI-101 or 2026-PI-103. Which is it?" in body
    assert "filed on matter 2026-PI-106, but it is not on the Medicals tab (the bill prints no total)." in body
    assert "it looks like one already entered (the tab already shows 25000.00 on 2026-10-20)" in body
    assert '5. Scan of 2:02 PM ("b.pdf"), the whole scan (31 pages) could not be read' in body
    assert 'Filed, needs calendaring:\n- Scan of 10:14 AM ("a.pdf"), pages 3-4, court paper from Superior Court' in body
    assert "One reply can answer every item." in body
    # The numbered list a reply is answered from, beside the journal.
    listing = json.loads((tmp_path / "journal" / "lists" / "2026-10-29.json").read_text())
    first = listing["items"][0]
    assert first["n"] == 1 and first["message_id"] == "msg-a" and first["first_page"] == 1
    assert first["candidates"] == ["2026-PI-104"] and "text" not in first
    # Every matter number the body prints is handed to the send gate, paired with its day.
    handoff = json.loads((tmp_path / "home" / ".smd" / "pre_run" / "post-intake-daily-list.json").read_text())
    numbers = {r["matterNumber"] for r in handoff["records"]}
    assert numbers == {"2026-PI-101", "2026-PI-102", "2026-PI-103", "2026-PI-104", "2026-PI-105", "2026-PI-106"}
    assert all(r["dates"] == ["2026-10-29"] for r in handoff["records"])


def test_the_next_list_covers_only_what_came_after(pr, tmp_path):
    _the_day(tmp_path)
    _run(pr)
    # Nothing new: nothing sent, the earlier letter is not listed again.
    assert _run(pr, at=datetime(2026, 10, 31, 0, 45, tzinfo=timezone.utc))["status"] == "nothing_to_list"
    # A bundle that arrived after the first list, on the next day's list.
    _batch(tmp_path, "late", [_letter(1, 1, "Farmers", issue="no_match")], at="2026-10-31T01:00:00Z")
    verdict = _run(pr, at=datetime(2026, 11, 2, 1, 45, tzinfo=timezone.utc))
    assert verdict["asks"] == 1
    assert "Farmers" in _envelope(tmp_path)["dispatches"][0]["full_body"]
    assert "Coastal" not in _envelope(tmp_path)["dispatches"][0]["full_body"]


def test_a_second_list_the_same_day_gets_its_own_reference(pr, tmp_path):
    _the_day(tmp_path)
    _run(pr)
    _batch(tmp_path, "later", [_letter(1, 1, "Farmers", issue="no_match")], at="2026-10-30T00:50:00Z")
    _run(pr, at=datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc))
    assert _envelope(tmp_path)["dispatches"][0]["subject"].startswith("Post list 2026-10-29-2:")
    assert (tmp_path / "journal" / "lists" / "2026-10-29-2.json").is_file()


def test_an_unreadable_record_sends_no_partial_list(pr, tmp_path):
    _the_day(tmp_path)
    (tmp_path / "journal" / "batches" / "broken.json").write_text("{not json")
    verdict = _run(pr)
    assert verdict["wakeAgent"] is False and verdict["status"] == "journal_unreadable"
    assert not (tmp_path / "home" / ".smd").exists()


def test_a_dry_run_writes_nothing(pr, tmp_path):
    _the_day(tmp_path)
    verdict = _run(pr, dry=True)
    assert verdict["status"] == "dry_run" and verdict["asks"] == 1
    assert not (tmp_path / "home").exists() and not (tmp_path / "journal" / "state.json").exists()


def _frozenset_names(path: Path) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text())
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            values = [n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            out[node.targets[0].id] = set(values)
    return out


def test_the_vocabulary_is_the_connectors(pr):
    connector = _frozenset_names(_CONNECTOR)
    for name in ("NOT_PLACED", "PLACED_CHECK", "BUNDLE_ISSUES"):
        assert set(getattr(pr, name)) == connector[name], name
    assert set(pr.KIND_WORDS) == connector["KINDS"]
    assert pr.DEFAULT_JOURNAL_DIR in _CONNECTOR.read_text()
