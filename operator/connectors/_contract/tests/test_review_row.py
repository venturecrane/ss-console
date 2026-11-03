"""The review row's file, decision and countdown rules."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from records_contract import review_row as rr


def _row(**over) -> dict:
    fields = dict(
        message_id="m1",
        to="p@firm.example",
        text="t",
        html="<p>t</p>",
        batches=[],
        matter_id="m",
        session_id="records:j1",
    )
    fields.update(over)
    return rr.hold_row(**fields)


def test_write_row_is_0600_and_atomic(tmp_path: Path) -> None:
    row = _row()
    path = rr.write_row(row, tmp_path / "review")
    assert path.name == f"{row['id']}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "review").stat().st_mode) == 0o700
    assert json.loads(path.read_text()) == row
    assert not list((tmp_path / "review").glob("*.tmp"))


def test_write_row_refuses_a_row_without_an_id(tmp_path: Path) -> None:
    with pytest.raises(rr.ReviewError):
        rr.write_row({"id": "../x"}, tmp_path)


def test_reason_is_capped_and_redirect_only_when_set() -> None:
    row = _row(reason="x" * 900)
    assert len(row["reason"]) == rr.REASON_CHARS and "redirect" not in row
    assert _row(redirect=True)["redirect"] is True
    # The redirect flag is in the digest.
    assert _row(redirect=True)["digest"] != _row()["digest"]


def test_digest_ignores_status_and_time() -> None:
    row = _row()
    assert rr.digest({**row, "status": "sent", "created_at": 0}) == row["digest"]


def test_released_count_counts_only_sent(tmp_path: Path) -> None:
    for status in ("sent", "sent", "rejected", "review", "expired", "failed"):
        rr.write_row({**_row(), "status": status}, tmp_path)
    (tmp_path / "junk.json").write_text("not json")
    (tmp_path / "noid.json").write_text(json.dumps({"status": "sent"}))
    assert rr.released_count(tmp_path) == 2
    assert rr.released_count(tmp_path / "absent") == 0


def test_decision_file_name_and_exclusivity(tmp_path: Path) -> None:
    row = _row()
    path = rr.decision_file(tmp_path / "d", row["id"], row["digest"], "approve")
    assert path.name == f"{row['id']}.{row['digest']}.approve"
    with pytest.raises(FileExistsError):
        rr.decision_file(tmp_path / "d", row["id"], row["digest"], "approve")
    for bad in (("approve", "zz", row["digest"]), ("approve", row["id"], "short"), ("maybe", row["id"], row["digest"])):
        word, rid, dig = bad
        with pytest.raises(rr.ReviewError):
            rr.decision_file(tmp_path / "d", rid, dig, word)


def test_decision_for_reads_only_root_owned_regular_files(tmp_path: Path) -> None:
    row = _row()
    d = tmp_path / "d"
    assert rr.decision_for(d, row) is None
    rr.decision_file(d, row["id"], row["digest"], "reject")
    # Running as a non-root uid, the file is ours, so it is NOT a root decision.
    want = "reject" if os.geteuid() == 0 else None
    assert rr.decision_for(d, row) == want
    # A decision for a changed row matches nothing, root or not.
    assert rr.decision_for(d, {**row, "digest": "0" * 64}) is None


def test_decision_for_ignores_a_symlink(tmp_path: Path) -> None:
    row = _row()
    d = tmp_path / "d"
    d.mkdir()
    target = tmp_path / "t"
    target.write_text("")
    (d / f"{row['id']}.{row['digest']}.approve").symlink_to(target)
    assert rr.decision_for(d, row) is None


def test_dirs_follow_the_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(rr.REVIEW_DIR_ENV, raising=False)
    assert str(rr.review_dir()) == rr.DEFAULT_REVIEW_DIR
    monkeypatch.setenv(rr.REVIEW_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(rr.DECISIONS_DIR_ENV, str(tmp_path / "x"))
    assert rr.review_dir() == tmp_path and rr.decisions_dir() == tmp_path / "x"


def test_expired() -> None:
    assert rr.expired({"created_at": 0}, now=rr.TTL_SECONDS + 1)
    assert not rr.expired({"created_at": 0}, now=rr.TTL_SECONDS)
