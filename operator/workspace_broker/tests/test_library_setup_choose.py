"""The administrator's choice from a "set up our ..." list, read in code
(library_setup_choose.py), and the made file's hand-off to the root writer.
Every refusal path makes nothing; both switch states; the audit rows of a
switched-on seat. Made-up firm."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import library_setup_wording as W
from workspace_broker.library_setup_choose import choice_of
from workspace_broker.tests.test_library_setup_verbs import ADMIN, OFF, ON, _yaml, call, seat, submit  # noqa: F401 - the fixture, used by name

CANDIDATES = [
    {
        "file_id": "f-old",
        "matter_id": "m-2",
        "matter_number": "1002",
        "saved_on": "2023-11-07",
        "sha256": "a" * 64,
        "score": 90,
    },
    {
        "file_id": "f-new",
        "matter_id": "m-1",
        "matter_number": "1001",
        "saved_on": "2026-05-08",
        "sha256": "b" * 64,
        "score": 70,
    },
]


def _thread(conversations: dict[str, str]):
    return lambda ref: conversations.get(ref, "conv-other")


def _list(broker, ref="<setup1@mail.firm.example>", donors=None, **over) -> str:
    jid = submit(broker, request_ref=ref, **over)["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    report = {"setup_outcome": "candidates", "candidates": CANDIDATES, **({"chart_donors": donors} if donors else {})}
    call(
        broker,
        "drafting_job_record",
        pid=1,
        uid=0,
        job_id=jid,
        state="delivered",
        fields={"cents": 0, "report": report},
    )
    return jid


def choose(broker, text="Use 2 please.", sender=ADMIN, ref="<reply1@mail.firm.example>"):
    env = {"requested_by": sender, "request_ref": ref, "reply_text": text}
    return call(broker, "library_setup_choose", envelope=env)


@pytest.fixture
def live(seat):
    broker, db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(switch=ON))
    broker.drafting.thread_of = _thread(
        {"<setup1@mail.firm.example>": "conv-1", "<reply1@mail.firm.example>": "conv-1"}
    )
    return broker, db, yaml_path, queue


def _audit(db: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


# ---- reading the choice -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,index,why",
    [
        ("2", 1, ""),
        ("Let's go with number 1.", 0, ""),
        ("use f-new", 1, ""),
        ("1 and 2", None, "refused_two"),
        ("number 3", None, "refused_not_listed"),
        ("thanks, looks good", None, "refused_no_choice"),
        ("2\n1. Matter 1002, saved November 7, 2023, file f-old", 1, ""),  # the list's own line is skipped
        ("use 2, the one saved 2026-05-08", 1, ""),
    ],
)
def test_the_choice_is_exactly_one_listed_number_or_file_id(text, index, why):
    assert choice_of(text, CANDIDATES) == (index, why)


# ---- the verb -------------------------------------------------------------------------------


def test_a_valid_choice_queues_one_accept_job_with_the_chosen_documents_ids(live):
    broker, db, _y, queue = live
    parent = _list(broker)
    out = choose(broker)
    assert out["accepted"] is True and out["live"] is True and out["say"] == W.chosen("memo", 2)
    record = json.loads((queue / f"{out['job_id']}.json").read_text())
    assert record["mode"] == "accept" and record["parent_id"] == parent
    assert record["choice"] == {
        "n": 2,
        **{k: CANDIDATES[1][k] for k in ("file_id", "matter_id", "matter_number", "saved_on", "sha256")},
    }
    assert "reply_text" not in json.dumps(record)
    assert _audit(db)[-1] == "LIBRARY_SETUP_CHOSEN"


@pytest.mark.parametrize(
    "text,sender,key",
    [
        ("1 and 2", ADMIN, "refused_two"),
        ("9", ADMIN, "refused_not_listed"),
        ("ok", ADMIN, "refused_no_choice"),
        ("2", "staff@firm.example", "refused_not_requester"),
    ],
)
def test_every_refusal_is_a_wording_file_sentence_and_makes_nothing(live, text, sender, key):
    broker, _db, _y, queue = live
    _list(broker)
    before = set(queue.glob("*.json"))
    out = choose(broker, text=text, sender=sender)
    assert (
        out["accepted"] is False
        and out["reason_key"] == key
        and out["reason"] in {W.say(key, **W.names("memo")) if "{" in W.SENTENCES[key] else W.SENTENCES[key]}
    )
    assert set(queue.glob("*.json")) == before


def test_a_choice_the_model_says_is_of_another_kind_is_refused(live):
    broker, *_ = live
    _list(broker)
    env = {
        "requested_by": ADMIN,
        "request_ref": "<reply1@mail.firm.example>",
        "reply_text": "2",
        "kind": "mediation_brief",
    }
    assert call(broker, "library_setup_choose", envelope=env)["reason_key"] == "refused_no_list"


def test_a_reply_in_another_thread_has_no_list(live):
    broker, *_ = live
    _list(broker)
    assert choose(broker, ref="<elsewhere@mail.firm.example>")["reason_key"] == "refused_no_list"


def test_a_thread_the_mailbox_cannot_read_refuses_rather_than_guesses(live):
    broker, *_ = live
    _list(broker)

    def down(_ref):
        raise RuntimeError("graph down")

    broker.drafting.thread_of = down
    assert choose(broker)["reason_key"] == "refused_unreadable"


def test_a_list_a_later_request_replaced_cannot_be_chosen_from(live):
    broker, *_ = live
    _list(broker)
    submit(broker, request_ref="<setup9@mail.firm.example>")  # a later "set up our memos"
    assert choose(broker)["reason_key"] == "refused_superseded"


def test_a_second_choice_from_the_same_list_is_refused(live):
    broker, *_ = live
    _list(broker)
    assert choose(broker)["accepted"] is True
    assert choose(broker, ref="<reply1@mail.firm.example>")["reason_key"] == "refused_chosen"


def test_off_the_choice_is_answered_as_today_and_nothing_is_queued(live):
    broker, db, yaml_path, queue = live
    _list(broker)
    yaml_path.write_text(_yaml(switch=OFF))
    before = set(queue.glob("*.json"))
    assert choose(broker) == {"ok": True, "accepted": False, "live": False}
    assert set(queue.glob("*.json")) == before and "LIBRARY_SETUP_CHOSEN" not in _audit(db)


def test_an_unreadable_switch_queues_a_dark_job_that_holds(live):
    broker, _db, yaml_path, queue = live
    _list(broker)
    yaml_path.write_text(_yaml(switch="logic_in_code:\n  library_setup: loud"))
    out = choose(broker)
    assert out["accepted"] is True and out["live"] is False and "say" not in out
    assert json.loads((queue / f"{out['job_id']}.json").read_text())["live"] is False


# ---- the hand-off ----------------------------------------------------------------------------


def _accept_running(broker) -> str:
    _list(broker)
    jid = choose(broker)["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    return jid


def _store(tmp_path: Path, broker):
    """The REAL establishment spool (its status read is the one the lane polls)."""
    from workspace_broker.establishment_store import EstablishmentStore

    root = tmp_path / "spool"
    for d in ("runs", "results", "staging"):
        (root / d).mkdir(parents=True)
    return EstablishmentStore(root, broker.ledger)


def _intake_answers(store, run_id: str, status: str) -> None:
    """What the root intake writes when a run ends (``EstablishIntake._write_result``)."""
    result = {"schema_version": 1, "run_id": run_id, "phase": "install", "status": status, "reasons": []}
    (store.results_dir / f"{run_id}.json").write_text(json.dumps(result))


def test_the_made_file_is_spooled_in_the_writers_shape(live, tmp_path):
    broker, *_ = live
    broker.establishment = _store(tmp_path, broker)
    jid = _accept_running(broker)
    data = b"made file bytes"
    filed = {"file_id": "lib-1", "version": "v1", "sha256": hashlib.sha256(data).hexdigest()}
    out = call(broker, "library_setup_accept_submit", pid=1, uid=0, job_id=jid, data_b64=base64.b64encode(data).decode(),
               filed=filed, fixed_strings=["Plaintiff's Mediation Brief"], replaces=None)  # fmt: skip
    run = broker.establishment.runs_dir / out["run_id"]
    assert (run / "file.docx").read_bytes() == data
    sub = json.loads((run / "submission.json").read_text())
    assert sub["scope"] == "library_accept" and sub["phase"] == "install"
    acc = sub["acceptance"]
    assert set(acc) == {"kind", "attorney", "file_id", "version", "contract", "accepted_by", "accepted_at", "sha256",
                        "fixed_strings", "replaces", "chart_source"}  # fmt: skip
    assert (acc["kind"], acc["file_id"], acc["version"], acc["contract"], acc["accepted_by"]) == (
        "memo", "lib-1", "v1", "marked", ADMIN)  # fmt: skip
    pending = call(broker, "library_setup_accept_status", pid=1, uid=0, job_id=jid)
    assert pending["status"] == "pending"
    _intake_answers(broker.establishment, out["run_id"], "installed")
    done = call(broker, "library_setup_accept_status", pid=1, uid=0, job_id=jid)
    assert done["status"] == "complete" and done["result"]["status"] == "installed"


DONORS = [{"file_id": "d-1", "matter_id": "m-3", "sha256": "d" * 64, "extra": "dropped"}]


def test_the_hunts_chart_donors_ride_the_choice_and_the_chart_source_rides_the_acceptance(live, tmp_path):
    """The coordinator's decision on #3876: the accept job takes a chartless memo's
    tables' look from the firm's own chart memos the hunt named; the broker hands it
    the ids, and the acceptance carries which one was used."""
    broker, db, *_ = live
    broker.establishment = _store(tmp_path, broker)
    _list(broker, donors=DONORS)
    jid = choose(broker)["job_id"]
    conn = sqlite3.connect(db)
    try:
        (raw,) = conn.execute("SELECT choice_json FROM library_setup_jobs WHERE id=?", (jid,)).fetchone()
    finally:
        conn.close()
    assert json.loads(raw)["chart_donors"] == [{"file_id": "d-1", "matter_id": "m-3", "sha256": "d" * 64}]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    data = b"made file bytes"
    chart = {"from": "firm_memo", "file_id": "d-1", "sha256": "d" * 64}
    filed = {"file_id": "lib-1", "version": "v1", "sha256": hashlib.sha256(data).hexdigest()}
    out = call(broker, "library_setup_accept_submit", pid=1, uid=0, job_id=jid, data_b64=base64.b64encode(data).decode(),
               filed=filed, fixed_strings=[], replaces=None, chart_source=chart)  # fmt: skip
    sub = json.loads((broker.establishment.runs_dir / out["run_id"] / "submission.json").read_text())
    assert sub["acceptance"]["chart_source"] == chart


def test_bytes_that_are_not_the_filed_ones_are_refused(live, tmp_path):
    broker, *_ = live
    broker.establishment = _store(tmp_path, broker)
    jid = _accept_running(broker)
    with pytest.raises(Exception):
        call(broker, "library_setup_accept_submit", pid=1, uid=0, job_id=jid, data_b64=base64.b64encode(b"x").decode(),
             filed={"file_id": "lib-1", "version": "v1", "sha256": "0" * 64}, fixed_strings=[], replaces=None)  # fmt: skip
    assert not list(broker.establishment.runs_dir.iterdir())


def test_only_root_may_hand_off(live, tmp_path):
    broker, *_ = live
    broker.establishment = _store(tmp_path, broker)
    jid = _accept_running(broker)
    with pytest.raises(Exception):
        call(broker, "library_setup_accept_submit", job_id=jid, data_b64="", filed={}, fixed_strings=[], replaces=None)


# ---- the read-back ----------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["accepted", "accepted_pending"])
def test_the_read_back_names_the_document_kind_and_who(live, outcome):
    from workspace_broker import fixed_wording, reply_binding

    broker, db, *_ = live
    jid = _accept_running(broker)
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered",
         fields={"cents": 0, "report": {"setup_outcome": outcome}})  # fmt: skip
    (text,) = fixed_wording.render("drafting_job", reply_binding.job_ledger(broker, "drafting_job").read(jid))
    values = {"saved": "May 8, 2026", "matter": "1001", "file_id": "f-new", **W.names("memo")}
    assert text.startswith(W.say("done_firm", **values))
    assert (W.say("done_pending") in text) is (outcome == "accepted_pending")
    assert _audit(db)[-1] == "LIBRARY_SETUP_ACCEPTED"


def test_a_dark_seat_writes_no_audit_row(seat):
    broker, db, *_ = seat
    _list(broker)
    assert not [t for t in _audit(db) if t.startswith("LIBRARY_SETUP")]
