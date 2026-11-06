"""An SMD review release that is a job's reply is recorded as that job's reply
(2026-11-06, ``shared.review_job``).

THE DEFECT (A&P). A drafting job's completion went out as an SMD hand release
(``hold(author="smd")``, then root's ``approve``): a plain reply outside the
broker's bound-reply claim, so the job looked unanswered and a ``jobs rewake``
would have sent the completion a second time. Each rule below has a test that
fails on origin/main.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from shared import matter_file_review as rev
from shared import review_job
from tests.test_matter_file_reply import (  # noqa: F401 - the fixtures and helpers the lane uses
    MATTER,
    STAFF,
    lane,
)

JOB = "01JZ0000000000000000000ABC"
OTHER = "01JZ0000000000000000000XYZ"
GRAPH = "AAMkREQUESTEMAIL0001="
RFC = "<req-1@firm.example>"


@pytest.fixture(autouse=True)
def _seat(tmp_path, monkeypatch):
    monkeypatch.setenv(rev.REVIEW_DIR_ENV, str(tmp_path / "review"))
    monkeypatch.setenv(rev.DECISIONS_DIR_ENV, str(tmp_path / "decisions"))
    monkeypatch.setenv(review_job.AUDIT_DB_ENV, str(tmp_path / "audit.db"))
    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(tmp_path / "requests.db"))
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.execute(
        "CREATE TABLE drafting_jobs (id TEXT PRIMARY KEY, state TEXT, request_ref TEXT, "
        "reply_sent_at TEXT, attempt INTEGER NOT NULL DEFAULT 1, reply_key TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT, action_type TEXT, metadata TEXT)"
    )
    conn.commit()
    conn.close()


def _job(tmp_path, job_id=JOB, state="delivered", ref=GRAPH, reply_key=None) -> None:
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.execute(
        "INSERT INTO drafting_jobs (id, state, request_ref, reply_key, reply_sent_at) VALUES (?,?,?,?,?)",
        (job_id, state, ref, reply_key, "2026-11-06T00:00:00Z" if reply_key else None),
    )
    conn.commit()
    conn.close()


def _row(**over):
    kw = dict(
        message_id=GRAPH,
        to=STAFF,
        text="The brief is filed in the matter.",
        html="",
        batches=[],
        matter_id=MATTER,
        session_id="smd-hand",
    )
    kw.update(over)
    return rev.hold(**kw)


# -- rule 1: an approve says which job the reply answers ----------------------


def test_an_smd_reply_approved_without_saying_which_job_is_refused(tmp_path) -> None:
    """FALSIFIER: on origin/main this approve succeeds, and the hand reply goes
    out unrecorded (the 2026-11-06 route)."""
    row = _row(author=rev.AUTHOR_SMD)
    with pytest.raises(rev.ReviewError, match="--job"):
        rev.decide(row["id"], "approve", row["digest"])
    assert not (tmp_path / "decisions").exists() or not list((tmp_path / "decisions").iterdir())


def test_an_unmarked_approve_in_a_thread_with_an_unreplied_job_is_refused(tmp_path) -> None:
    """FALSIFIER: on origin/main the Operator's row is approved here too."""
    _job(tmp_path)
    row = _row()
    with pytest.raises(rev.ReviewError, match=f"drafting_job:{JOB} \\(delivered\\)"):
        rev.decide(row["id"], "approve", row["digest"])


def test_a_job_in_the_thread_is_found_through_the_request_index(tmp_path) -> None:
    """The job's request_ref is the request's RFC id; the held reply answers a
    later email in the same thread (the index joins them)."""
    _job(tmp_path, ref=RFC)
    later = "AAMkLATEREMAIL0002="
    _index(tmp_path, [(GRAPH, RFC, "CONV-1"), (later, "<later@firm.example>", "CONV-1")])
    assert review_job.unreplied_jobs(later) == [
        {"kind": "drafting_job", "job_id": JOB, "state": "delivered"}
    ]
    assert review_job.unreplied_jobs("AAMkELSEWHERE0003=") == []


def _index(tmp_path, rows) -> None:
    conn = sqlite3.connect(tmp_path / "requests.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS requests (vendor_message_id TEXT PRIMARY KEY, internet_message_id TEXT, "
        "received_at REAL NOT NULL, sender TEXT, subject TEXT, reply_opening TEXT, opening_at REAL, "
        "thread_ref TEXT, request_opening TEXT, request_text TEXT, reply_text TEXT)"
    )
    for vid, imid, thread in rows:
        conn.execute(
            "INSERT OR REPLACE INTO requests (vendor_message_id, internet_message_id, received_at, thread_ref) "
            "VALUES (?,?,?,?)",
            (vid, imid, 1.0, thread),
        )
    conn.commit()
    conn.close()


def test_a_job_bound_to_the_email_is_found_through_its_binding_row(tmp_path) -> None:
    """The job's completion turn bound a reply to this email and never sent it."""
    _job(tmp_path, ref=RFC)
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.execute(
        "INSERT INTO audit_log (ts, action_type, metadata) VALUES ('t', 'REPLY_BINDING', ?)",
        (json.dumps({"outcome": "bound", "job_id": JOB, "graph_message_id": GRAPH}),),
    )
    conn.commit()
    conn.close()
    assert [j["job_id"] for j in review_job.unreplied_jobs(GRAPH)] == [JOB]


def test_a_job_whose_reply_is_recorded_owes_nothing(tmp_path) -> None:
    _job(tmp_path, reply_key="1:delivered")
    assert review_job.unreplied_jobs(GRAPH) == []
    # A resumed job's older held reply does not answer its new ending.
    _job(tmp_path, job_id=OTHER, reply_key="0:held")
    assert [j["job_id"] for j in review_job.unreplied_jobs(GRAPH)] == [OTHER]


def test_an_unreadable_ledger_is_never_no_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(review_job.AUDIT_DB_ENV, str(tmp_path / "missing.db"))
    row = _row()
    with pytest.raises(rev.ReviewError, match="audit db"):
        rev.decide(row["id"], "approve", row["digest"])


def test_approve_with_job_records_it_on_the_row_and_its_digest(tmp_path) -> None:
    _job(tmp_path)
    row = _row(author=rev.AUTHOR_SMD)
    job = review_job.resolve_job(JOB)
    assert job == {"kind": "drafting_job", "job_id": JOB}
    path = rev.decide(row["id"], "approve", row["digest"], job=job)
    (held,) = rev.rows()
    assert held["job"] == job and held["digest"] == rev.digest(held) != row["digest"]
    assert path.name == f"{row['id']}.{held['digest']}.approve"
    # The job is digested: a row edited to name another job matches no decision.
    assert rev.digest({**held, "job": {"kind": "drafting_job", "job_id": OTHER}}) != held["digest"]


def test_approve_with_no_job_is_the_explicit_answer(tmp_path) -> None:
    _job(tmp_path)
    row = _row(author=rev.AUTHOR_SMD)
    rev.decide(row["id"], "approve", row["digest"], no_job=True)
    (held,) = rev.rows()
    assert held["job"] == rev.JOB_NONE and held["digest"] == row["digest"]
    assert rev.row_job(held) is None


def test_job_and_no_job_together_and_a_split_reply_are_refused(tmp_path) -> None:
    _job(tmp_path)
    row = _row(author=rev.AUTHOR_SMD)
    job = {"kind": "drafting_job", "job_id": JOB}
    with pytest.raises(rev.ReviewError, match="two different"):
        rev.decide(row["id"], "approve", row["digest"], job=job, no_job=True)
    split = _row(
        author=rev.AUTHOR_SMD,
        batches=[[{"name": "a", "sha256": "a" * 64, "handle": "b" * 32}]] * 2,
    )
    with pytest.raises(rev.ReviewError, match="several parts"):
        rev.decide(split["id"], "approve", split["digest"], job=job)


def test_a_row_held_with_its_job_needs_no_flag(tmp_path) -> None:
    _job(tmp_path)
    job = {"kind": "drafting_job", "job_id": JOB}
    row = _row(author=rev.AUTHOR_SMD, job=job)
    assert rev.decide(row["id"], "approve", row["digest"]).exists()
    with pytest.raises(rev.ReviewError, match="already held"):
        other = _row(author=rev.AUTHOR_SMD, job=job)
        rev.decide(other["id"], "approve", other["digest"], no_job=True)


def test_resolve_job_refuses_an_unknown_or_malformed_id(tmp_path) -> None:
    with pytest.raises(rev.ReviewError, match="no ledger"):
        review_job.resolve_job(OTHER)
    with pytest.raises(rev.ReviewError, match="job id"):
        review_job.resolve_job("drafting_job:nope")
    _job(tmp_path)
    assert review_job.resolve_job(f"drafting_job:{JOB}")["job_id"] == JOB


def test_the_cli_approve_takes_job_and_no_job(tmp_path, monkeypatch, capsys) -> None:
    _job(tmp_path)
    monkeypatch.setattr(rev.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rev.os, "chown", lambda *a: None)
    row = _row(author=rev.AUTHOR_SMD)
    assert rev.main(["approve", row["id"], row["digest"]]) == 2
    assert "--job" in capsys.readouterr().err
    assert rev.main(["approve", row["id"], row["digest"], "--job", JOB]) == 0
    assert rev.rows()[0]["job"] == {"kind": "drafting_job", "job_id": JOB}


# -- the release carries the job to the broker --------------------------------


def test_the_release_of_a_job_reply_names_the_job_to_the_broker(lane, monkeypatch) -> None:  # noqa: F811 - the lane fixture, imported
    """FALSIFIER: on origin/main the release is a plain reply and the broker is
    never told which job it answers, so it records nothing."""
    mod, _d1, sent, _spool, _b = lane
    job = {"kind": "drafting_job", "job_id": JOB}
    _row(author=rev.AUTHOR_SMD, job=job)
    plain = _row(message_id="AAMkPLAIN0000000004=")
    monkeypatch.setattr(rev, "decision", lambda r, **k: "approve")
    mod._review_once()
    by_id = {s["message_id"]: s for s in sent}
    assert by_id[GRAPH]["for_job"] == job
    assert "for_job" not in by_id[plain["message_id"]]
