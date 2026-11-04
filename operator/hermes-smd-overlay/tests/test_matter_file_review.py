"""SMD review of a reply carrying matter files (ADR 0102, ``review: smd``)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import matter_binding
from shared import matter_file_reply as mfr
from shared import matter_file_review as rev
from tests.test_matter_file_reply import (  # noqa: F401 - the fixtures and helpers the lane uses
    MATTER,
    STAFF,
    FakeBroker,
    _clean,
    _draft,
    _email,
    _pdf,
    _stage_call,
    lane,
)


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setenv(rev.REVIEW_DIR_ENV, str(tmp_path / "review"))
    monkeypatch.setenv(rev.DECISIONS_DIR_ENV, str(tmp_path / "decisions"))


def _row(**over):
    kw = dict(
        message_id="m1",
        to=STAFF,
        text="Here are the records.",
        html="",
        batches=[
            [{"file_id": "f0", "name": "A.pdf", "sha256": "a" * 64, "size": 10, "handle": "b" * 32}]
        ],
        matter_id=MATTER,
        session_id="s1",
    )
    kw.update(over)
    return rev.hold(**kw)


def test_the_digest_covers_who_what_and_which_bytes() -> None:
    row = _row()
    assert rev.digest(row) == row["digest"]
    for key, value in [("to", "x@firm.example"), ("text", "other"), ("message_id", "m2")]:
        assert rev.digest({**row, key: value}) != row["digest"]
    swapped = json.loads(json.dumps(row))
    swapped["batches"][0][0]["handle"] = "c" * 32
    assert rev.digest(swapped) != row["digest"]


def test_list_shows_every_fact_the_review_needs() -> None:
    text = rev.describe(
        _row(batches=[[{**_row()["batches"][0][0], "pages": "3-11", "page_count": 9}]])
    )
    for fragment in (
        STAFF,
        "Here are the records.",
        "f0",
        "A.pdf",
        "pages 3-11",
        "(9 p)",
        "10 bytes",
        "a" * 64,
    ):
        assert fragment in text


@pytest.mark.parametrize(
    ("row_id", "dig", "fragment"),
    [("nope", "0" * 64, "exactly"), ("0" * 24, "0" * 64, "no reply is waiting")],
)
def test_decide_refuses_malformed_or_unknown(row_id, dig, fragment) -> None:
    with pytest.raises(rev.ReviewError, match=fragment):
        rev.decide(row_id, "approve", dig)


def test_decide_refuses_a_stale_digest_and_an_expired_approval() -> None:
    row = _row()
    with pytest.raises(rev.ReviewError, match="does not match"):
        rev.decide(row["id"], "approve", "0" * 64)
    old = _row(now=time.time() - rev.TTL_SECONDS - 5)
    with pytest.raises(rev.ReviewError, match="expired"):
        rev.decide(old["id"], "approve", old["digest"])
    assert rev.decide(old["id"], "reject", old["digest"]).name.endswith(".reject")


def test_only_a_root_owned_decision_counts(monkeypatch) -> None:
    row = _row()
    path = rev.decide(row["id"], "approve", row["digest"])
    assert path.exists()
    # Written by this (non-root) test process: the gateway ignores it.
    assert os.getuid() == 0 or rev.decision(row) == ""
    real = os.lstat
    monkeypatch.setattr(
        rev.os, "lstat", lambda p: SimpleNamespace(st_mode=real(p).st_mode, st_uid=0)
    )
    assert rev.decision(row) == "approve"


def test_the_cli_refuses_a_decision_from_a_non_root_user(monkeypatch, capsys) -> None:
    row = _row()
    monkeypatch.setattr(rev.os, "geteuid", lambda: 1000)
    assert rev.main(["approve", row["id"], row["digest"]]) == 2
    assert rev.main(["list"]) == 0
    assert row["id"] in capsys.readouterr().out


# ---- the lane ---------------------------------------------------------------


def _review_seat(yaml_path: Path) -> None:
    yaml_path.write_text(
        yaml_path.read_text().replace(
            "matter_file_delivery:\n", "matter_file_delivery:\n  review: smd\n"
        )
    )


def test_lane_a_files_reply_waits_for_review_and_goes_exactly_as_held(
    lane,  # noqa: F811 - the lane fixture, imported
    tmp_path,
    monkeypatch,
) -> None:
    mod, d1, sent, spool, broker = lane
    _review_seat(mod._YAML_PATH)
    _email()
    _stage_call(mod, spool, [(f"R{i}.pdf", _pdf(i + 1)) for i in range(4)])
    notice = _draft(mod)
    assert sent == [] and "matter_files_review" in notice
    (row,) = rev.rows()
    assert row["status"] == rev.STATUS_REVIEW and [len(b) for b in row["batches"]] == [3, 1]
    assert row["batches"][0][0]["page_count"] == 1
    # A redraft cannot slip past the review.
    _draft(mod, call="d2", body="Here they are again.")
    assert sent == []
    # No decision yet: nothing moves.
    mod._review_once()
    assert sent == []
    monkeypatch.setattr(rev, "decision", lambda r, **k: "approve")
    mod._review_once()
    assert [len(s["attachments"]) for s in sent] == [3, 1]
    assert sent[0]["comment"] == row["text"]
    held = [(f["handle"], f["sha256"]) for b in row["batches"] for f in b]
    went = [(a["handle"], a["sha256"]) for s in sent for a in s["attachments"]]
    assert went == held
    (done,) = rev.rows()
    assert done["status"] == rev.STATUS_SENT and done["text"] == ""
    mod._review_once()
    assert len(sent) == 2  # never twice


def test_lane_a_rejected_reply_is_discarded(lane, monkeypatch) -> None:  # noqa: F811 - the lane fixture, imported
    mod, d1, sent, spool, _b = lane
    _review_seat(mod._YAML_PATH)
    _email()
    _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    _draft(mod)
    monkeypatch.setattr(rev, "decision", lambda r, **k: "reject")
    mod._review_once()
    (row,) = rev.rows()
    assert sent == [] and row["status"] == rev.STATUS_REJECTED and row["text"] == ""


def test_lane_a_row_changed_after_review_is_not_sent(lane, monkeypatch) -> None:  # noqa: F811 - the lane fixture, imported
    mod, _d1, sent, spool, _b = lane
    _review_seat(mod._YAML_PATH)
    _email()
    _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    _draft(mod)
    (row,) = rev.rows()
    path = rev.review_dir() / f"{row['id']}.json"
    path.write_text(json.dumps({**row, "to": "someone@else.example"}))
    monkeypatch.setattr(rev, "decision", lambda r, **k: "approve")
    mod._review_once()
    assert sent == [] and rev.rows()[0]["status"] == rev.STATUS_FAILED


def test_without_review_authored_the_reply_sends_at_once(lane) -> None:  # noqa: F811 - the lane fixture, imported
    mod, _d1, sent, spool, _b = lane
    _email()
    _stage_call(mod, spool, [("R.pdf", _pdf(1))])
    _draft(mod)
    assert len(sent) == 1 and rev.rows() == []
    assert mfr.review_by_smd({"matter_file_delivery": {"review": "SMD"}})
    first = {"matter_file_delivery": {"review_first": 1}}
    assert mfr.review_by_smd(first, 0) and not mfr.review_by_smd(first, 1)
    assert not mfr.review_by_smd({"matter_file_delivery": {"review_first": True}}, 0)
    assert not mfr.review_by_smd({"matter_file_delivery": {"recipients": ["@x.example"]}})
    matter_binding._reset_for_tests()


def _review_first_seat(mod, n: int = 1) -> None:
    mod._YAML_PATH.write_text(
        mod._YAML_PATH.read_text().replace(
            "matter_file_delivery:\n", f"matter_file_delivery:\n  review_first: {n}\n"
        )
    )


def _files_reply(mod, spool, n: int) -> None:
    _email(message_id=f"m{n}", session=f"s{n}")
    _stage_call(mod, spool, [(f"R{n}.pdf", _pdf(n))], session=f"s{n}", call=f"st{n}")
    _draft(mod, session=f"s{n}", call=f"d{n}")


def _decide(monkeypatch, decisions: dict[str, str]) -> None:
    monkeypatch.setattr(rev, "decision", lambda r, **k: decisions.get(r["message_id"], ""))


def test_review_first_counts_only_released_replies() -> None:
    """Only a reply SMD released spends review_first; rejected, expired, failed
    and still-waiting rows never do."""
    statuses = [rev.STATUS_REVIEW, rev.STATUS_REJECTED, rev.STATUS_EXPIRED, rev.STATUS_FAILED]
    for status in statuses:
        rev.finish(_row(), status)
    assert rev.released_count() == 0
    first = {"matter_file_delivery": {"review_first": 1}}
    assert mfr.review_by_smd(first, rev.released_count())
    rev.finish(_row(), rev.STATUS_SENT)
    assert rev.released_count() == 1 and not mfr.review_by_smd(first, rev.released_count())


def test_lane_review_first_a_rejected_reply_leaves_the_next_one_held(lane, monkeypatch) -> None:  # noqa: F811 - the lane fixture, imported
    """2026-11-02: SMD rejected the first A&P files reply; the next one must
    still wait for review, never send unreviewed."""
    mod, _d1, sent, spool, _b = lane
    _review_first_seat(mod)
    _files_reply(mod, spool, 1)
    assert sent == []
    _decide(monkeypatch, {"m1": "reject"})
    mod._review_once()
    assert sent == [] and rev.rows()[0]["status"] == rev.STATUS_REJECTED
    _files_reply(mod, spool, 2)
    assert sent == [] and rev.released_count() == 0
    assert sorted(r["status"] for r in rev.rows()) == [rev.STATUS_REJECTED, rev.STATUS_REVIEW]


def test_lane_review_first_a_waiting_reply_spends_nothing(lane, monkeypatch) -> None:  # noqa: F811 - the lane fixture, imported
    """review_first: 1 holds until one reply is released, then sends normally."""
    mod, _d1, sent, spool, _b = lane
    _review_first_seat(mod)
    _files_reply(mod, spool, 1)
    _files_reply(mod, spool, 2)  # m1 still waiting: m2 waits too
    assert sent == [] and len(rev.rows()) == 2
    _decide(monkeypatch, {"m1": "approve"})
    mod._review_once()
    assert [s["message_id"] for s in sent] == ["m1"] and rev.released_count() == 1
    _files_reply(mod, spool, 3)
    assert [s["message_id"] for s in sent] == ["m1", "m3"]
    assert len(rev.rows()) == 2  # m3 was never held


def test_a_reply_smd_wrote_by_hand_never_spends_review_first() -> None:
    """2026-11-04: SMD's own reply to A&P, held and approved through this path,
    took one of the ten review_first releases meant for the Operator's replies
    (released 1 -> 2). Only the Operator's released replies count."""
    first = {"matter_file_delivery": {"review_first": 1}}
    smd = _row(author=rev.AUTHOR_SMD)
    assert smd["digest"] == rev.digest(
        {k: v for k, v in smd.items() if k != "author"}
    )  # never digested
    rev.finish(smd, rev.STATUS_SENT)
    assert rev.released_count() == 0 and mfr.review_by_smd(first, rev.released_count())
    rev.finish(_row(), rev.STATUS_SENT)
    assert rev.released_count() == 1 and not mfr.review_by_smd(first, rev.released_count())


def test_an_unknown_author_is_refused() -> None:
    with pytest.raises(rev.ReviewError):
        _row(author="operator")


def test_mark_smd_corrects_a_hand_reply_held_before_rows_carried_an_author(capsys) -> None:
    """Root's correction for the 2026-11-04 row: the count drops back, the row
    keeps its digest, its mode, and every other field."""
    hand = _row()
    rev.finish(hand, rev.STATUS_SENT)
    rev.finish(_row(), rev.STATUS_SENT)
    assert rev.released_count() == 2
    path = rev.review_dir() / f"{hand['id']}.json"
    mode = path.stat().st_mode & 0o777
    marked = rev.mark_smd(hand["id"])
    assert rev.released_count() == 1 and marked["digest"] == hand["digest"]
    on_disk = json.loads(path.read_text())
    assert on_disk["author"] == rev.AUTHOR_SMD and on_disk["status"] == rev.STATUS_SENT
    assert path.stat().st_mode & 0o777 == mode
    with pytest.raises(rev.ReviewError):
        rev.mark_smd("0" * 24)
    assert rev.main(["history"]) == 0
    out = capsys.readouterr().out
    assert (
        f"{hand['id']}  sent" in out and "by SMD" in out and "1 counted toward review_first." in out
    )
