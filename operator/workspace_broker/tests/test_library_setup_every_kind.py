"""'Set up the document library' at the broker (2026-11-09): one request for every
kind, one reply with one list per kind, the administrator's answer read per kind
in code ("discovery responses: use 2", "memos: keep the current one", a matter
or a name pointed at), and the made file's contract carried to the root writer.
Made-up firm, made-up documents."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import fixed_wording, library_setup_wording as W, reply_binding
from workspace_broker.library_setup_ledger import KINDS, EnvelopeError, validate_envelope
from workspace_broker.tests.test_library_setup_choose import _store, _thread
from workspace_broker.tests.test_library_setup_verbs import ADMIN, ON, _yaml, call, envelope, seat, submit  # noqa: F401 - seat is the fixture, used by name

SHA = {"r": "a" * 64, "r2": "b" * 64, "m": "c" * 64, "d": "d" * 64}


def _cand(fid: str, matter: str, saved: str, contract: str, score: int = 50) -> dict:
    return {"file_id": fid, "matter_id": f"mid-{fid}", "matter_number": matter, "saved_on": saved,
            "sha256": SHA.get(fid, "e" * 64), "score": score, "contract": contract}  # fmt: skip


LIBRARY_REPORT = {
    "setup_outcome": "library",
    "kinds": [
        {"kind": "discovery_set", "setup_outcome": "none_found", "found": 0, "candidates": [], "current": None},
        {
            "kind": "discovery_response",
            "setup_outcome": "candidates",
            "found": 7,
            "candidates": [_cand("r", "2024-0113", "2026-05-08", "styles", 90), _cand("r2", "2025-0021", "2026-02-01", "styles")],
            "current": None,
        },
        {"kind": "mediation_brief", "setup_outcome": "none_found", "found": 2, "candidates": [], "current": {"pinned": True}},
        {
            "kind": "memo",
            "setup_outcome": "candidates",
            "found": 4,
            "candidates": [_cand("m", "2024-0113", "2026-03-03", "marked")],
            "current": {"file_id": "lib-1", "version": "v2", "sha256": "f" * 64},
        },
        {
            "kind": "depo_outline",
            "setup_outcome": "candidates",
            "found": 1,
            "candidates": [_cand("d", "2025-0021", "2025-12-12", "styles")],
            "current": None,
        },
    ],
}  # fmt: skip


@pytest.fixture
def live(seat):
    broker, db, yaml_path, queue = seat
    yaml_path.write_text(_yaml(switch=ON))
    broker.drafting.thread_of = _thread(
        {f"<{x}@mail.firm.example>": "conv-1" for x in ("lib1", "reply1", "reply2", "reply3")}
    )
    return broker, db, yaml_path, queue


def _deliver(broker, jid: str, report: dict) -> None:
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    call(
        broker,
        "drafting_job_record",
        pid=1,
        uid=0,
        job_id=jid,
        state="delivered",
        fields={"cents": 0, "report": report},
    )


def _library(broker, report: dict | None = None) -> str:
    out = submit(broker, kind="library", request_ref="<lib1@mail.firm.example>",
                 request_text="Please set up the document library.")  # fmt: skip
    _deliver(broker, out["job_id"], report or LIBRARY_REPORT)
    return out["job_id"]


def _reply(broker, text: str, ref: str = "<reply1@mail.firm.example>") -> dict:
    env = {"requested_by": ADMIN, "request_ref": ref, "reply_text": text}
    return call(broker, "library_setup_choose", envelope=env)


def _queued(queue: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(queue.glob("*.json"))]


def _rendered(broker, jid: str) -> str:
    (text,) = fixed_wording.render("drafting_job", reply_binding.job_ledger(broker, "drafting_job").read(jid))
    return text


# ---- the request ---------------------------------------------------------------------------


def test_the_kinds_are_every_kind_the_drafting_job_makes_in_the_registrys_order():
    assert KINDS == ("discovery_set", "discovery_response", "mediation_brief", "memo", "depo_outline")
    assert tuple(W.KIND_NAMES) == KINDS


def test_set_up_the_document_library_is_one_job_for_every_kind(live):
    broker, _db, _y, queue = live
    out = submit(broker, kind="library", request_text="Initialize yourself and set up for our firm.")
    assert out["accepted"] is True and out["say"] == W.ack_library()
    (record,) = _queued(queue)
    assert record["setup_kind"] == "library" and "point_matter" not in record


def test_a_second_library_request_while_one_runs_is_refused_in_plain_words(live):
    broker, *_ = live
    submit(broker, kind="library")
    out = submit(broker, kind="library", request_ref="<lib2@mail.firm.example>")
    assert out["accepted"] is False and out["reason"] == W.SENTENCES["refused_underway_library"]


@pytest.mark.parametrize(
    "over,ok",
    [
        ({"kind": "memo", "matter": "2024-0113", "request_text": "Use the memo on matter 2024-0113."}, True),
        ({"kind": "memo", "matter": "2024-9999", "request_text": "Use the memo on matter 2024-0113."}, False),
        ({"kind": "memo", "document": "Case Evaluation", "request_text": "Use our Case Evaluation memo."}, True),
        ({"kind": "memo", "document": "Something else", "request_text": "Use our Case Evaluation memo."}, False),
        ({"kind": "library", "matter": "2024-0113", "request_text": "matter 2024-0113"}, False),
    ],
)
def test_what_a_request_points_at_must_be_its_own_words(over, ok):
    env = envelope(**over)
    if ok:
        assert validate_envelope(env)
    else:
        with pytest.raises(EnvelopeError):
            validate_envelope(env)


def test_a_pointed_request_queues_a_list_of_what_it_points_at(live):
    broker, _db, _y, queue = live
    out = submit(broker, kind="memo", matter="2024-0113", request_text="Set up our memos from matter 2024-0113.")
    assert out["say"] == W.ack_pointed("memo", "2024-0113", False)
    (record,) = _queued(queue)
    assert record["point_matter"] == "2024-0113" and "request_text" not in record


# ---- the reply: one list per kind ------------------------------------------------------


def test_the_library_reply_has_one_section_per_kind_and_says_what_each_uses_now(live):
    broker, *_ = live
    text = _rendered(broker, _library(broker))
    assert text.startswith(W.SENTENCES["library_lead"])
    assert "Discovery sets: we found none of the firm's own, so they stay as they are." in text
    assert "Discovery responses: we found 7 of the firm's own." in text
    assert "1. Matter 2024-0113, saved May 8, 2026, file r" in text
    assert "Mediation briefs: we found 2 of the firm's own and none the firm's file could be made from" in text
    assert text.count(W.SENTENCES["now_standard"]) == 3
    assert W.SENTENCES["now_pinned"] in text and W.SENTENCES["now_accepted"] in text
    assert W.SENTENCES["demand_not_listed"] in text and W.SENTENCES["library_question"] in text
    assert text.endswith(W.SENTENCES["outside"])
    assert "—" not in text  # no em dash


def test_a_pointed_list_says_what_it_looked_at(live):
    broker, *_ = live
    out = submit(broker, kind="memo", matter="2024-0113", request_text="matter 2024-0113 please",
                 request_ref="<reply2@mail.firm.example>")  # fmt: skip
    _deliver(broker, out["job_id"], {"setup_outcome": "candidates", "candidates": [_cand("m", "2024-0113", "2026-03-03", "marked")], "current": None})  # fmt: skip
    text = _rendered(broker, out["job_id"])
    assert text.startswith(W.say("pointed_matter_lead", matter="2024-0113", **W.names("memo")))


def test_a_matter_the_firm_does_not_have_is_told(live):
    broker, *_ = live
    out = submit(broker, kind="memo", matter="1999-0001", request_text="matter 1999-0001")
    _deliver(broker, out["job_id"], {"setup_outcome": "matter_not_found", "candidates": []})
    assert _rendered(broker, out["job_id"]).startswith(W.say("matter_not_found", matter="1999-0001"))


# ---- the answer, read per kind in code ----------------------------------------------------


def test_one_line_per_kind_queues_one_accept_job_per_kind_with_the_contract_the_list_named(live):
    broker, _db, _y, queue = live
    parent = _library(broker)
    out = _reply(broker, "Discovery responses: use 2\nMemos: keep the current one\nDepo outlines: 1\n\nThanks,\nDana")
    assert out["accepted"] is True and len(out["job_ids"]) == 2
    jobs = {r["document_class"]: r for r in _queued(queue) if r.get("mode") == "accept"}
    assert set(jobs) == {"discovery_response", "depo_outline"}
    assert jobs["discovery_response"]["choice"]["file_id"] == "r2"
    assert jobs["discovery_response"]["choice"]["contract"] == "styles"
    assert (
        jobs["discovery_response"]["parent_id"] == parent
        and jobs["discovery_response"]["setup_kind"] == "discovery_response"
    )
    assert out["say"] == W.chosen_library(
        [("discovery_response", "pick", 2), ("memo", "keep", None), ("depo_outline", "pick", 1)]
    )


def test_a_listed_documents_matter_number_chooses_it(live):
    broker, _db, _y, queue = live
    _library(broker)
    out = _reply(broker, "Discovery responses: use the one on matter 2025-0021")
    (job,) = [r for r in _queued(queue) if r.get("mode") == "accept"]
    assert out["accepted"] and job["choice"]["file_id"] == "r2"


def test_a_matter_not_on_the_list_is_looked_at_and_listed_in_the_same_thread(live):
    broker, _db, _y, queue = live
    _library(broker)
    out = _reply(broker, "Memos: use the one on matter 2023-0777")
    lists = [r for r in _queued(queue) if r.get("mode") != "accept" and r.get("point_matter")]
    assert out["accepted"] and [(r["setup_kind"], r["point_matter"]) for r in lists] == [("memo", "2023-0777")]
    assert out["say"] == W.chosen_library([("memo", "matter", "2023-0777")])


def test_a_quoted_name_is_looked_for(live):
    broker, _db, _y, queue = live
    _library(broker)
    out = _reply(broker, 'Deposition outlines: use "Outline for Depo of Example"')
    (pointed,) = [r for r in _queued(queue) if r.get("point_name")]
    assert out["accepted"] and pointed["point_name"] == "Outline for Depo of Example"


def test_then_the_pointed_list_is_chosen_from_in_the_same_thread(live):
    broker, _db, _y, queue = live
    _library(broker)
    _reply(broker, "Memos: use the one on matter 2023-0777")
    (pointed,) = [r for r in _queued(queue) if r.get("point_matter")]
    _deliver(broker, pointed["job_id"], {"setup_outcome": "candidates", "candidates": [_cand("m2", "2023-0777", "2025-01-01", "styles")]})  # fmt: skip
    out = _reply(broker, "use 1", ref="<reply2@mail.firm.example>")
    assert out["accepted"] is True and out["say"] == W.chosen("memo", 1)
    (job,) = [r for r in _queued(queue) if r.get("mode") == "accept"]
    assert job["parent_id"] == pointed["job_id"] and job["choice"]["contract"] == "styles"


def test_every_kind_at_once_with_the_recommended_ones(live):
    broker, _db, _y, queue = live
    _library(broker)
    out = _reply(broker, "Use the recommended one for all of them.")
    jobs = sorted(r["document_class"] for r in _queued(queue) if r.get("mode") == "accept")
    assert out["accepted"] and jobs == ["depo_outline", "discovery_response", "memo"]


@pytest.mark.parametrize(
    "text,key",
    [
        ("use 2", "refused_which_kind"),
        ("Memos: use 3", "refused_not_listed"),
        ("Memos: use 1\nMemos: keep the current one", "refused_two"),
        ("Discovery responses: 1 and 2", "refused_two"),
        ("Looks good, thanks.", "refused_no_choice"),
    ],
)
def test_an_answer_code_cannot_read_exactly_makes_nothing(live, text, key):
    broker, _db, _y, queue = live
    _library(broker)
    before = _queued(queue)
    out = _reply(broker, text)
    assert out["accepted"] is False and out["reason_key"] == key
    assert _queued(queue) == before


def test_a_kind_already_chosen_from_this_list_is_refused_and_other_kinds_still_may_be(live):
    broker, *_ = live
    _library(broker)
    assert _reply(broker, "Memos: 1")["accepted"] is True
    again = _reply(broker, "Memos: 1", ref="<reply2@mail.firm.example>")
    assert again["reason_key"] == "refused_chosen_kind"
    assert _reply(broker, "Discovery responses: 1", ref="<reply3@mail.firm.example>")["accepted"] is True


def test_a_newer_library_request_replaces_the_list(live):
    broker, *_ = live
    _library(broker)
    submit(broker, kind="library", request_ref="<lib9@mail.firm.example>")
    assert _reply(broker, "Memos: 1")["reason_key"] == "refused_superseded"


# ---- the contract reaches the root writer ------------------------------------------------


def test_a_styles_file_is_spooled_with_contract_styles_and_anything_else_is_refused(live, tmp_path):
    broker, *_ = live
    broker.establishment = _store(tmp_path, broker)
    _library(broker)
    jid = _reply(broker, "Discovery responses: 1")["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    data = b"made styles base"
    filed = {"file_id": "lib-2", "version": "v1", "sha256": hashlib.sha256(data).hexdigest()}
    req = {
        "job_id": jid,
        "data_b64": base64.b64encode(data).decode(),
        "filed": filed,
        "fixed_strings": [],
        "replaces": None,
    }
    with pytest.raises(Exception):
        call(broker, "library_setup_accept_submit", pid=1, uid=0, contract="prototypes", **req)
    out = call(broker, "library_setup_accept_submit", pid=1, uid=0, contract="styles", **req)
    sub = json.loads((broker.establishment.runs_dir / out["run_id"] / "submission.json").read_text())
    assert (sub["acceptance"]["kind"], sub["acceptance"]["contract"]) == ("discovery_response", "styles")
