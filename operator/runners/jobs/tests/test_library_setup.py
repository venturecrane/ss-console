"""'Set up our ...': the job that finds the firm's own files of a kind, both switch
states, the roster, the holds, and the drafting lane's second kind of job. Every
fixture is made up; no document system is reached, and the client fake refuses
every call that is not a read."""

from __future__ import annotations

import io
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import docx
import pytest

import library_setup_testkit as kit
from test_drafting_memo_base import _firm_memo

from jobs import lane_proof
from jobs.lane_proof import admits as real_admits  # before the conftest's every-lane-proven stub
from jobs.drafting_lane import DraftingBroker, DraftingLane
from jobs.firmbase import hunt as H
from jobs.library_setup import job as J, roster as R
from jobs.library_setup_lane import child_cmd, compose_wake

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)
MATTER_A, MATTER_B = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
STAFF = [
    {"firstName": "Alex", "lastName": "Example", "email": "alex@firm.example"},
    {"firstName": "Blair", "lastName": "Sample", "email": "blair@firm.example"},
    {"firstName": "Blair", "lastName": "Other", "email": "other@firm.example"},
]


def _ago(years: float) -> str:
    return (NOW - timedelta(days=365.25 * years)).isoformat().replace("+00:00", "Z")


def _memo(author: str, words: int = 1000, sign: str = "") -> bytes:
    d = docx.Document()
    d.core_properties.author = author
    d.core_properties.last_modified_by = author
    d.add_paragraph(" ".join(["word"] * words))
    if sign:
        d.add_paragraph(sign)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _firm_memo_by(author: str, sign: str = "") -> bytes:
    """A made-up firm memo the memo plan can adopt (the memo kit's), saved by
    ``author``, long enough for a memo's length band, signed when ``sign``."""
    d = docx.Document(io.BytesIO(_firm_memo()))
    d.core_properties.author = author
    d.core_properties.last_modified_by = author
    d.add_paragraph(" ".join(["word"] * 600))
    if sign:
        d.add_paragraph(sign)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class ReadOnlyClient:
    """A case system with two matters; every call but a read fails the test."""

    def __init__(self, files: dict[str, tuple[str, dict, bytes]], *, unlisted: tuple[str, ...] = (), staff=STAFF):
        self.files = files  # file id -> (matter id, row, bytes)
        self.unlisted = unlisted
        self.staff = staff
        self.calls: list[str] = []

    def get(self, path: str, **params):
        self.calls.append(path)
        if path == "/matters":
            if params.get("Status") == "Open":
                return {"value": [{"id": MATTER_A}, {"id": MATTER_B}]}
            return {"value": []}
        if path == "/staff":
            return {"value": self.staff}
        if path.endswith("/documents/files"):
            mid = path.split("/")[2]
            if mid in self.unlisted:
                raise RuntimeError("429")
            return {"value": [row for m, row, _ in self.files.values() if m == mid]}
        if path.startswith("/matters/"):
            return {"number": {MATTER_A: "1001", MATTER_B: "1002"}[path.split("/")[2]]}
        raise AssertionError(path)

    def download_file(self, matter_id: str, file_id: str):
        self.calls.append(f"download {file_id}")
        return {}, self.files[file_id][2]

    def __getattr__(self, name):
        raise AssertionError(f"the setup job reads only; it called {name}")


def _row(fid: str, name: str, years: float) -> dict:
    return {"id": fid, "name": name, "fileExtension": ".docx", "dateModified": _ago(years), "sizeBytes": 1000}


def _client(**kw) -> ReadOnlyClient:
    return ReadOnlyClient(
        {
            "f-new": (MATTER_A, _row("f-new", "Case Evaluation Memo.docx", 0.5), _firm_memo_by("Alex Example")),
            "f-old": (
                MATTER_B,
                _row("f-old", "Liability Analysis.docx", 3.0),
                _firm_memo_by("Blair Sample", sign="Blair Sample"),
            ),
            "f-short": (MATTER_B, _row("f-short", "Short memo.docx", 0.1), _memo("Alex Example", words=40)),
            "f-other": (MATTER_A, _row("f-other", "Mediation Brief.docx", 0.2), _memo("Alex Example")),
        },
        **kw,
    )


def _switch(mode: str = "observe", error: str | None = None):
    """The real ``logic_in_code.Switch`` (library_setup_testkit)."""
    return kit.switch(mode, error)


def _inputs_dir(*emails: str) -> str:
    """A real drafting inputs directory with these authored attorneys."""
    return kit.drafting_inputs(Path(tempfile.mkdtemp()) / "inputs", *emails)


def _inputs(*emails: str):
    """The REAL loader's answer (``drafting.firm.load``), never a look-alike: a
    method-shaped stub here once hid ``inputs.attorneys()`` on a property."""
    return kit.loaded(Path(tempfile.mkdtemp()) / "inputs", *emails)


JOB = {"kind": "library_setup", "setup_kind": "memo", "attorney_named": None, "live": True}


# ---- the hunt narrowed to one attorney -------------------------------------------------


def test_the_hunt_narrowed_to_one_person_keeps_only_files_naming_them():
    rows = [
        {**_row("a", "Memo one.docx", 0.5), "matter_id": "m"},
        {**_row("b", "Memo two.docx", 0.5), "matter_id": "m"},
        {**_row("c", "Memo three.docx", 0.5), "matter_id": "m"},
    ]
    files = {
        "a": _memo("Alex Example"),
        "b": _memo("Someone Else", sign="Respectfully, Blair Q. Sample"),
        "c": _memo("Someone Else"),
    }
    hunted = H.rank("memo", rows, lambda _m, f: files[f], staff=["Alex Example"], now=NOW, person="Blair Sample")
    assert [c.file_id for c in hunted.candidates] == ["b"]
    assert hunted.counts["not_this_person"] == 2
    everyone = H.rank("memo", rows, lambda _m, f: files[f], staff=["Alex Example"], now=NOW)
    assert {c.file_id for c in everyone.candidates} == {"a", "b", "c"}


def test_names_person_reads_the_core_properties_and_the_signature_only():
    assert H.names_person(_memo("Blair Sample"), "Blair Sample")
    assert H.names_person(_memo("x", sign="Blair Sample"), "Blair Sample")
    assert not H.names_person(_memo("x"), "Blair Sample")


# ---- the roster ------------------------------------------------------------------------


def test_the_roster_maps_each_authored_email_to_its_case_system_name():
    roster = R.attorneys(_inputs("alex@firm.example", "BLAIR@firm.example"), _client())
    assert [(a.case_system_name, a.folded_name) for a in roster] == [
        ("Alex Example", "alex example"),
        ("Blair Sample", "blair sample"),
    ]


@pytest.mark.parametrize(
    "inputs,client,code",
    [
        (None, None, R.ROSTER_INPUTS_MISSING),
        (_inputs("nobody@firm.example"), None, R.ROSTER_UNMATCHED),
    ],
)
def test_a_roster_that_cannot_be_built_says_why(inputs, client, code):
    with pytest.raises(R.RosterUnusable) as exc:
        R.attorneys(inputs, client or _client())
    assert exc.value.code == code


def test_a_staff_listing_that_fails_is_could_not_look():
    class Down(ReadOnlyClient):
        def get(self, path, **params):
            raise RuntimeError("down")

    with pytest.raises(R.RosterUnusable) as exc:
        R.attorneys(_inputs("alex@firm.example"), Down({}))
    assert exc.value.code == R.COULD_NOT_LOOK


@pytest.mark.parametrize(
    "named,want,outcome",
    [
        ("Alex Example", "alex example", ""),
        ("alex  EXAMPLE", "alex example", ""),
        ("Alex", "alex example", ""),
        ("Sample", "blair sample", ""),
        ("Blair", None, R.ATTORNEY_AMBIGUOUS),
        ("Casey", None, R.ATTORNEY_NOT_ROSTERED),
        ("Alex Sample", None, R.ATTORNEY_NOT_ROSTERED),
    ],
)
def test_a_named_attorney_is_exactly_one_on_the_roster_or_not_at_all(named, want, outcome):
    roster = R.attorneys(_inputs("alex@firm.example", "blair@firm.example", "other@firm.example"), _client())
    found, said = R.pick(named, roster)
    assert (found.folded_name if found else None, said) == (want, outcome)


# ---- the job, both switch states ------------------------------------------------------


def _run(job=None, mode="enforce", **kw):
    client = kw.pop("client", None) or _client()
    out = J.run({**JOB, **(job or {})}, client=client, switch=_switch(mode, kw.pop("error", None)), now=NOW, **kw)
    return out, client


def test_live_the_job_names_the_candidates_in_its_band_by_file_id_date_and_matter():
    out, client = _run()
    assert out["outcome"] == "delivered" and out["setup_outcome"] == J.CANDIDATES
    # best first: the older file is signed by firm staff as well as saved by them
    assert [c["file_id"] for c in out["candidates"]] == ["f-old", "f-new"]
    newest = out["candidates"][1]
    assert newest["matter_number"] == "1001" and newest["saved_on"] == "2026-05-08"
    assert "f-short" not in json.dumps(out) and "f-other" not in json.dumps(out)
    # ids, dates, numbers, counts: never a file's name
    assert "Memo" not in json.dumps(out) and "Analysis" not in json.dumps(out)


def test_off_the_job_records_the_same_facts_as_observed_and_reads_only():
    out, client = _run(mode="observe")
    assert out["outcome"] == "observed" and out["live"] is False
    assert [c["file_id"] for c in out["candidates"]] == ["f-old", "f-new"]
    assert all(c == "/matters" or c.startswith(("/matters/", "/staff", "download ")) for c in client.calls)


def test_a_switch_on_now_but_off_when_queued_stays_observed():
    out, _ = _run(job={"live": False}, mode="enforce")
    assert out["outcome"] == "observed"


def test_an_unreadable_switch_holds_for_smd_and_reads_nothing():
    out, client = _run(mode="enforce", error="customer.yaml not parseable")
    assert out["outcome"] == "held" and out["hold"] == J.SWITCH_UNREADABLE
    assert client.calls == []


def test_a_matter_that_could_not_be_listed_holds_rather_than_says_none():
    out, _ = _run(client=_client(unlisted=(MATTER_B,)))
    assert out["outcome"] == "held" and out["hold"] == R.COULD_NOT_LOOK


def test_a_document_the_kinds_plan_cannot_adopt_is_never_listed_and_is_counted_by_reason():
    """The A&P rehearsal, 2026-11-07: every memo the firm would have been shown was
    refused by the memo plan, so a pick would have been told the wrong reason."""
    plain = _memo("Alex Example")  # a memo with no heading for the plan to read roles from
    client = _client()
    client.files["f-plain"] = (MATTER_A, _row("f-plain", "Plain memo.docx", 0.1), plain)
    out, _ = _run(client=client)
    assert [c["file_id"] for c in out["candidates"]] == ["f-old", "f-new"]
    assert out["counts"]["plan_refused"] == 1 and out["counts"]["plan_refused_no_heading"] == 1


def test_a_kind_whose_every_candidate_the_plan_refuses_is_none_found():
    client = ReadOnlyClient(
        {
            "a": (MATTER_A, _row("a", "Memo one.docx", 0.5), _memo("Alex Example")),
            "b": (MATTER_B, _row("b", "Memo two.docx", 1.5), _memo("Blair Sample")),
        }
    )
    out, _ = _run(client=client)
    assert (out["setup_outcome"], out["candidates"]) == (J.NONE_FOUND, [])
    assert out["counts"]["plan_refused"] == 2


def test_nothing_in_the_band_is_none_found():
    client = ReadOnlyClient({"f": (MATTER_A, _row("f", "Memo.docx", 0.5), _memo("Alex Example", words=10))})
    out, _ = _run(client=client)
    assert out["setup_outcome"] == J.NONE_FOUND and out["candidates"] == []


def test_an_attorneys_setup_hunts_only_their_files_and_names_them():
    out, _ = _run(
        job={"attorney_named": "Blair Sample"}, inputs_dir=_inputs_dir("alex@firm.example", "blair@firm.example")
    )
    assert out["setup_outcome"] == J.CANDIDATES and [c["file_id"] for c in out["candidates"]] == ["f-old"]
    assert out["attorney_key"] == "blair sample" and out["attorney_name"] == "Blair Sample"


@pytest.mark.parametrize("mode,state", [("enforce", "delivered"), ("observe", "observed")])
def test_an_attorney_not_on_the_roster_is_said_and_nothing_is_hunted(mode, state):
    out, client = _run(job={"attorney_named": "Casey"}, mode=mode, inputs_dir=_inputs_dir("alex@firm.example"))
    assert (out["outcome"], out["setup_outcome"]) == (state, R.ATTORNEY_NOT_ROSTERED)
    assert not any(c.startswith("download") for c in client.calls)


def test_a_roster_email_the_case_system_lacks_holds_for_smd():
    out, _ = _run(job={"attorney_named": "Alex"}, inputs_dir=_inputs_dir("gone@firm.example"))
    assert out["outcome"] == "held" and out["hold"] == R.ROSTER_UNMATCHED


def test_the_roster_reads_the_real_loaders_attorneys_property():
    """Regression (the 2026-11-07 rehearsal): ``DraftingFirm.attorneys`` is a
    property. ``roster.attorneys`` called it, and every request naming an
    attorney raised TypeError on a firm's real inputs. Fails on that code."""
    from jobs.drafting import firm as drafting_firm

    assert isinstance(getattr(drafting_firm.DraftingFirm, "attorneys"), property)
    inputs = _inputs("alex@firm.example")
    assert isinstance(inputs, drafting_firm.DraftingFirm)
    assert [a.email for a in R.attorneys(inputs, _client())] == ["alex@firm.example"]


def test_drafting_inputs_that_do_not_load_hold_the_named_attorney_for_smd():
    out, _ = _run(job={"attorney_named": "Alex"}, inputs_dir=str(Path(tempfile.mkdtemp()) / "missing"))
    assert out["outcome"] == "held" and out["hold"] == R.ROSTER_INPUTS_MISSING


def test_an_unknown_kind_or_an_unreadable_job_file_holds(tmp_path):
    out, _ = _run(job={"setup_kind": "letter"})
    assert out["outcome"] == "held" and out["hold"] == J.BAD_JOB
    assert J.run_dir(tmp_path)["hold"] == J.BAD_JOB


# ---- the drafting lane's second kind of job --------------------------------------------

RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[-1])
assert sys.argv[-2] == "library-setup", sys.argv
job = json.loads(jd.joinpath('job.json').read_text())
assert job['kind'] == 'library_setup' and job['slug'] == 'example', job
out = [{"outcome": OUTCOME, "live": OUTCOME == "delivered", "setup_outcome": "candidates", "counts": {"matters_listed": 2},
        "candidates": [{"file_id": "f-new", "matter_number": "1001", "saved_on": "2026-05-09", "score": 90}]}]
(jd / 'verdict.json').write_text(json.dumps(out))
print(json.dumps(out))
"""


class Broker:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []
        self.requests: list[dict] = []

    def _request(self, req: dict) -> dict:
        self.requests.append(req)
        a = req["action"]
        if a == "drafting_job_status":
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "drafting_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            self.rows[req["job_id"]]["state"] = req["state"]
            return {"ok": True}
        if a.startswith("delivery_"):
            return {"ok": True, "replies": []}
        raise AssertionError(f"a library setup job speaks only the drafting job's status and record, not {a}")


def _lane(tmp_path: Path, outcome: str) -> tuple[DraftingLane, Broker]:
    run_dir = tmp_path / "run"
    q = run_dir / "drafting-queue"
    q.mkdir(parents=True)
    broker = Broker()
    p = tmp_path / "runner.py"
    p.write_text(RUNNER.replace("OUTCOME", repr(outcome)))
    lane = DraftingLane(
        run_dir=run_dir,
        broker=DraftingBroker(broker),
        runner_cmd=[os.environ.get("PYTHON", "python3"), str(p), "draft"],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=q,
        inputs_dir=str(tmp_path / "no-drafting-inputs"),
    )
    return lane, broker


def _queue(lane: DraftingLane, broker: Broker, live: bool, job_id: str = "01SETUPJ0B00000000000000AB") -> str:
    broker.rows[job_id] = {"state": "submitted"}
    env = {
        "kind": "library_setup",
        "job_id": job_id,
        "setup_kind": "memo",
        "document_class": "memo",
        "attorney_named": None,
        "requester": "admin@firm.example",
        "message_ref": "<req@firm.example>",
        "library_matter_number": "LIB-1",
        "live": live,
    }
    (lane.queue / f"{job_id}.json").write_text(json.dumps(env))
    return job_id


def test_dark_the_lane_records_observed_and_wakes_no_one(tmp_path):
    lane, broker = _lane(tmp_path, "observed")
    jid = _queue(lane, broker, live=False)
    assert lane.tick() == "observed"
    assert [s for _j, s, _f in broker.records] == ["running", "observed"]
    fields = broker.records[-1][2]
    assert fields["cents"] == 0 and fields["report"]["candidates"][0]["file_id"] == "f-new"
    assert "wake" not in lane._daemon_state(jid)
    assert lane.tick() is None  # terminal: never run again
    assert not any(r["action"] == "drafting_allowance" for r in broker.requests)


def test_live_the_lane_records_delivered_and_wakes_deliver_mode_with_ids_only(tmp_path):
    lane, broker = _lane(tmp_path, "delivered")
    jid = _queue(lane, broker, live=True)
    assert lane.tick() == "delivered"
    task = lane._daemon_state(jid)["wake"]["task"]
    assert task.splitlines()[0] == f"Run the document-drafter skill's DELIVER mode for drafting job {jid}."
    assert "Mode: library setup" in task and "Document class: memo." in task and "Files: none." in task


def test_a_runner_with_no_verdict_is_failed_and_pages_smd(tmp_path, monkeypatch):
    lane, broker = _lane(tmp_path, "delivered")
    (tmp_path / "runner.py").write_text("import sys\nsys.exit(3)\n")
    told = []
    monkeypatch.setattr("jobs.library_setup_lane.job_notes.tell", lambda *a, **k: told.append(k["why"]))
    jid = _queue(lane, broker, live=True)
    assert lane.tick() == "failed"
    assert told and "wake" not in lane._daemon_state(jid)


def test_the_child_is_the_lanes_runner_with_library_setup_for_draft():
    assert child_cmd(["setpriv", "nice", "/bin/jobs", "draft"]) == ["setpriv", "nice", "/bin/jobs", "library-setup"]
    assert "Gamma" not in compose_wake("01X", {"setup_kind": "memo", "requester": "a@b.example"})


def test_a_dark_setup_is_admitted_without_a_proof_and_a_live_one_has_its_own_key(tmp_path):
    lane, broker = _lane(tmp_path, "observed")  # the real DraftingLane, never a look-alike
    env = tmp_path / "e.json"
    env.write_text(json.dumps({"kind": "library_setup", "live": False}))
    assert real_admits(lane, "01X", env, {"state": "submitted"}) is True
    assert lane_proof.lane_key("drafting", {"kind": "library_setup", "document_class": "memo"}) == (
        "document-drafter.library_setup"
    )
    assert lane_proof.lane_key("drafting", {"kind": "library_setup", "mode": "accept"}) == (
        "document-drafter.library_setup_accept"
    )
    env.write_text(json.dumps({"kind": "library_setup", "live": True}))
    broker.rows["01X"] = {"state": "submitted"}
    assert real_admits(lane, "01X", env, {"state": "submitted"}) is False  # live: waits for its own proof
