"""The re-check economies of the Case File Manager, on synthetic matters:

* a re-check pack is proportional to the change (only the new or changed and
  the prior-cited documents are listed; unchanged completed tasks, events and
  memos are left to the prior record);
* a re-check is answered with a patch that code merges onto the prior record,
  then validates unchanged; a patch that cannot be merged is answered with a
  request for the whole record;
* a matter whose documents did not change is re-ranked in code, with no call;
* the Operator's own stamped writes are not a change;
* a bulk upload is read whole;
* the listing carries no document id, and ``[doc N]`` resolves in code.

Every name, number and date here is invented.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

import casefile_testkit as kit
from medchron.casefile import firm as firm_mod, pack, read, recheck
from medchron.casefile.run import CasefileRun

NEW = "f5f5f5f5-f5f5-4f5f-8f5f-f5f5f5f5f5f5"
NEW2 = "f6f6f6f6-f6f6-4f6f-8f6f-f6f6f6f6f6f6"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pricing_path: Path) -> dict:
    for k, v in kit.environ(tmp_path).items():
        monkeypatch.setenv(k, v)
    return {
        "inputs": kit.make_inputs(tmp_path / "inputs"),
        "state": tmp_path / "state",
        "pricing": str(pricing_path),
        "tmp": tmp_path,
    }


def _run(env: dict, seat: kit.CfSeat, model: kit.Scripted, job: str = kit.JOB) -> tuple[CasefileRun, dict]:
    jd = kit.make_job(env["tmp"] / f"job-{job[-4:]}", job_id=job)
    r = CasefileRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=env["pricing"],
        state_dir=env["state"],
        seat_factory=lambda: seat,
        client=model,
        log=lambda m: None,
        today=lambda: dt.date(2026, 10, 26),
        readback_pause=0,
    )
    return r, json.loads(r.run().to_json())


def _add_doc(seat: kit.CfSeat, fid: str, name: str, text: str) -> None:
    seat.blobs[fid] = kit.make_pdf([text])
    seat.files[kit.M1].append(seat._row(fid, name, "2026-10-25"))


def _reads(model: kit.Scripted) -> list[dict]:
    return [c for c in model.calls if c["tools"][-1]["name"] == "record_review"]


# ---- 1. the re-check pack is proportional to the change --------------------------------


def _big_matter(n: int) -> dict[str, Any]:
    files = [
        {"id": f"{i:08x}-0000-4000-8000-{i:012x}", "name": f"Scanned page {i}", "ext": ".pdf", "size": 10,
         "folderId": "fo", "created": "2026-01-01", "modified": "2026-01-01", "deleted": False}
        for i in range(n)
    ]  # fmt: skip
    return {
        "matter": {"id": kit.M1, "number": "100001", "status": "Open"},
        "detail": {"title": "Gamma v. Example"},
        "folders": [{"id": "fo", "path": "/Records"}],
        "files": files,
        "tasks_open": [{"id": kit.TASK_OPEN, "subject": "Request police report", "createdDate": "2026-01-02"}],
        "tasks_done": [
            {"id": kit.TASK_DONE, "subject": "Old letter", "completedDate": "2026-02-01"},
            {"id": kit.TASK2, "subject": "Fresh call", "completedDate": "2026-10-20"},
        ],
        "events": [
            {"id": "ev1", "subject": "Old hearing", "startTime": "2026-03-01"},
            {"id": "ev2", "subject": "Mediation", "startTime": "2026-12-01"},
        ],  # fmt: skip
        "memos": [{"id": "me1", "title": "Old memo", "dateCreated": "2026-02-02", "text": "old"}],
        "layouts": {"item": {"row": {"layoutDesign": {"id": kit.DESIGN}}, "values": {"Provider": "Example Imaging"}}},
    }


def _pack(tmp: Path, inv: dict[str, Any], prior: dict | None, changed: set[str] | None) -> dict:
    firm = firm_mod.load(kit.make_inputs(tmp / "in"))
    ctx = read.CasefileContext(kit.M1, inv["files"], tmp / "data", 20000)
    return pack.build(ctx, inv, firm, prior, changed)


def test_a_recheck_pack_lists_only_the_changed_and_cited_documents(tmp_path: Path) -> None:
    inv = _big_matter(2000)
    cited, new = inv["files"][5]["id"], {inv["files"][7]["id"], inv["files"][9]["id"]}
    prior = {
        "delivered_at": "2026-10-01",
        "manifest": {f["id"]: f["modified"] for f in inv["files"]},
        "review": {"number": "100001", "open_tasks": [{"task_id": kit.TASK_OPEN, "proof_doc_ids": [cited]}]},
    }
    full = _pack(tmp_path, inv, None, None)["text"]
    re_ = _pack(tmp_path, inv, prior, new)["text"]
    listed = [ln for ln in re_.splitlines() if ln.startswith("[doc ")]
    assert len(listed) == 3  # two new, one the prior record cites; not 2000
    assert sum(1 for ln in listed if ln.endswith("NEW OR CHANGED")) == 2
    assert "list_files and fetch_doc reach every other document" in re_
    # the prior record cites its document as [doc N], never the id
    assert cited not in re_ and "[doc " in re_.split("PRIOR REVIEW RECORD", 1)[1].splitlines()[2]
    # open tasks and layouts stay; unchanged completed tasks, past events and old memos go
    assert "Request police report" in re_ and "MEDICALS TAB" in re_
    assert "Fresh call" in re_ and "Old letter" not in re_
    assert "Mediation" in re_ and "Old hearing" not in re_ and "Old memo" not in re_
    assert "COMPLETED TASKS (1 of 2;" in re_
    assert len(re_) * 10 < len(full), (len(re_), len(full))


def test_a_listing_row_carries_no_document_id_and_saves_its_characters(tmp_path: Path) -> None:
    inv = _big_matter(1000)
    text = _pack(tmp_path, inv, None, None)["text"]
    rows = [ln for ln in text.splitlines() if ln.startswith("[doc ")]
    assert len(rows) == 1000 and not any(recheck.UUID.search(r) for r in rows)
    # the old row was "[doc N] | <36-char id> | folder | ...": 39 characters more per row
    old = sum(len(r) + len(" | ") + 36 for r in rows)
    assert old - sum(len(r) for r in rows) == 1000 * 39


# ---- 2. the patch ------------------------------------------------------------------------

PRIOR = {
    "number": "100001",
    "headline": "Old headline.",
    "open_tasks": [{"task_id": "T1", "action": "leave_open", "why": "a"}, {"task_id": "T2", "action": "leave_open"}],
    "medicals": [{"provider": "Clinic", "service_start": "2026-01-13", "charge": 10.0}],
    "facilities": [{"facility": "Clinic", "records": "in"}],
    "questions": ["Is the bill paid?"],
}


def test_a_patch_replaces_by_key_appends_new_items_and_removes() -> None:
    got = recheck.merge(
        PRIOR,
        {
            "set": {"headline": "New headline."},
            "upsert": {
                "open_tasks": [{"task_id": "T2", "action": "question", "why": "b"}, {"task_id": "T3", "action": "x"}],
                "medicals": [{"provider": " clinic ", "service_start": "2026-01-13", "charge": 12.0}],
                "questions": ["Who is the adjuster?"],
            },
            "remove": {"facilities": ["Clinic"], "questions": ["is the bill paid?"]},
        },
    )
    assert got["headline"] == "New headline."
    assert [t["task_id"] for t in got["open_tasks"]] == ["T1", "T2", "T3"]
    assert got["open_tasks"][1]["action"] == "question" and got["open_tasks"][0] == PRIOR["open_tasks"][0]
    assert got["medicals"] == [{"provider": " clinic ", "service_start": "2026-01-13", "charge": 12.0}]
    assert got["facilities"] == [] and got["questions"] == ["Who is the adjuster?"]
    assert PRIOR["headline"] == "Old headline."  # the prior record is never changed in place
    assert recheck.merge(PRIOR, {}) == PRIOR  # an empty patch: nothing changed


@pytest.mark.parametrize(
    "patch",
    [
        {"replace": {}},  # unknown part
        {"set": {"no_such_field": 1}},
        {"upsert": {"headline": ["x"]}},  # not a list keyed by item
        {"upsert": {"open_tasks": [{"action": "x"}]}},  # no key
        {"upsert": {"open_tasks": [{"task_id": "T9"}, {"task_id": "t9"}]}},  # the same item twice
        {"remove": {"open_tasks": ["T9"]}},  # nothing to remove
        {"upsert": {"open_tasks": [{"task_id": "T1"}]}, "remove": {"open_tasks": ["T1"]}},
        {"set": {"open_tasks": []}, "upsert": {"open_tasks": [{"task_id": "T1"}]}},
    ],
)
def test_a_patch_that_cannot_be_merged_unambiguously_is_refused(patch: dict) -> None:
    with pytest.raises(recheck.PatchRefused):
        recheck.merge(PRIOR, patch)


def test_a_prior_record_holding_two_items_with_one_key_refuses_a_patch_on_that_key() -> None:
    prior = {**PRIOR, "open_tasks": [{"task_id": "T1"}, {"task_id": "T1", "why": "dup"}]}
    with pytest.raises(recheck.PatchRefused):
        recheck.merge(prior, {"upsert": {"open_tasks": [{"task_id": "T1", "why": "c"}]}})


def test_a_changed_matter_is_answered_with_a_patch_merged_and_validated(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted())
    _add_doc(seat, NEW, "New letter.pdf", "EXAMPLE LETTER\nto Gamma Example")

    def patch(number: str, pack_text: str, turn: int) -> dict:
        n = kit.doc_n(pack_text, NEW, "New letter.pdf")
        assert n is not None and NEW not in pack_text  # listed by number only
        return {"set": {"headline": f"A new letter is in ([doc {n}])."}}

    model = kit.Scripted(patch_fn=patch)
    r, v = _run(env, seat, model, job="01KTJ0BX0000000000000000CH")
    assert v["verdict"] == "delivered", v
    assert v["metrics"]["recheck"]["patch"] == 1, (v["metrics"]["recheck"], v.get("matters_held"))
    assert v["metrics"]["recheck"]["full"] == 0
    got = json.loads((r.data / "m" / kit.M1 / "review.json").read_text())
    rec = got["review"]
    assert rec["headline"] == f"A new letter is in ({NEW})."  # [doc N] resolved to the id
    assert [t["task_id"] for t in rec["open_tasks"]] == [kit.TASK_OPEN, kit.TASK_MONEY]  # the rest from the prior
    assert got["pack"]["answer"] == "patch"


def test_an_unmergeable_patch_falls_back_to_the_whole_record(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted())
    _add_doc(seat, NEW, "New letter.pdf", "EXAMPLE LETTER\nto Gamma Example")

    def patch(number: str, pack_text: str, turn: int) -> dict | None:
        return {"remove": {"open_tasks": ["no-such-task"]}} if turn == 1 else None

    model = kit.Scripted(patch_fn=patch)
    _r, v = _run(env, seat, model, job="01KTJ0BX0000000000000000CH")
    assert v["verdict"] == "delivered", v
    rc = v["metrics"]["recheck"]
    assert (rc["patch"], rc["full"], rc["patch_refused"]) == (0, 1, 1)
    asked = json.dumps([c["messages"][-1] for c in _reads(model)])
    assert "Call record_review with the whole record" in asked


def test_a_patch_with_no_prior_record_is_answered_with_a_request_for_the_whole_record() -> None:
    use = {"name": read.PATCH_TOOL["name"], "input": {"set": {"headline": "x"}}}
    record, _p, why = read._answer(use, None, {})
    assert record is None and "no prior record" in why


# ---- 3. no call when no document changed ---------------------------------------------------


def test_a_matter_changed_only_in_its_tasks_is_reranked_in_code_with_no_call(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted())
    seat.tasks.append({**seat._task("e9e9e9e9-e9e9-4e9e-8e9e-e9e9e9e9e9e9", kit.M1, "Call adjuster")})
    model = kit.Scripted()
    r, v = _run(env, seat, model, job="01KTJ0BX0000000000000000CJ")
    assert v["verdict"] == "delivered", v
    assert model.calls == [] and v["cents"] == 0
    assert v["metrics"]["recheck"]["reranked_in_code"] == 1 and v["matters_current"] == 2
    rows = {x["matter_id"]: x for x in json.loads((r.data / "rows.json").read_text())}
    assert rows[kit.M1]["status_word"].startswith("Current (re-ranked")
    # the prior job's still-open proposals keep counting in the rank
    # (the first job proposed the closure and the Medicals row; both still wait)
    assert rows[kit.M1]["rank"]["reason"] == "2 changes proposed for approval."
    assert rows[kit.M1]["review"]["open_tasks"][0]["action"] == "mark_complete"  # the prior closure stands


# ---- 4. the Operator's own hand ---------------------------------------------------------


def test_a_task_the_operator_wrote_is_not_a_change(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted())
    seat.tasks.append(seat._task("e8e8e8e8-e8e8-4e8e-8e8e-e8e8e8e8e8e8", kit.M1, "[Operator] Chase the records"))
    model = kit.Scripted()
    _r, v = _run(env, seat, model, job="01KTJ0BX0000000000000000CK")
    assert model.calls == [] and v["metrics"]["recheck"]["current"] == 2, v


def test_stamped_ids_name_only_operator_written_items() -> None:
    inv = {
        "tasks_open": [{"id": "a", "subject": "[Operator] x"}, {"id": "b", "subject": "Call"}],
        "tasks_done": [{"id": "c", "subject": "Call", "note": " [operator] Completed on approval"}],
        "events": [{"id": "d", "subject": "[Operator] reminder"}, {"id": "e", "subject": "Hearing"}],
        "memos": [{"id": "f", "text": "[Operator] note"}, {"id": "g", "title": "A memo", "text": "by hand"}],
    }
    assert recheck.stamped_ids(inv) == {"a", "c", "d", "f"}


# ---- 5. a bulk upload is a whole read -----------------------------------------------------


def test_a_bulk_upload_is_read_whole_not_rechecked(env: dict) -> None:
    env["inputs"] = kit.make_inputs(env["tmp"] / "inputs-bulk", bulk_recheck_docs=1)
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted())
    _add_doc(seat, NEW, "New letter.pdf", "EXAMPLE LETTER\nto Gamma Example")
    _add_doc(seat, NEW2, "Another letter.pdf", "EXAMPLE LETTER TWO\nto Gamma Example")
    model = kit.Scripted()
    _r, v = _run(env, seat, model, job="01KTJ0BX0000000000000000CM")
    assert v["verdict"] == "delivered", v
    assert v["metrics"]["recheck"]["bulk_full_read"] == 1
    packs = [kit.first_text(c) for c in _reads(model)]
    assert packs and all("PRIOR REVIEW RECORD" not in p for p in packs)


# ---- 6. [doc N] resolves in code, and fails closed ------------------------------------------


def test_doc_refs_resolve_to_ids_and_an_unknown_number_is_a_violation() -> None:
    refs = {1: {"id": "id-one"}, 2: {"id": "id-two"}}
    got, problems = recheck.resolve(
        {"headline": "see [doc 2]", "source_doc_id": 1, "proof_doc_ids": ["[doc 2]", "doc 1", "9"], "n": 3}, refs
    )
    assert got["headline"] == "see id-two" and got["source_doc_id"] == "id-one" and got["n"] == 3
    assert got["proof_doc_ids"][:2] == ["id-two", "id-one"]
    assert problems == [
        "unknown_doc: [doc 9] is not a document on this matter (use a number from the listing or list_files)"
    ]
    task = kit.TASK_OPEN
    got = recheck.to_refs({"x": f"{kit.DOC_REPORT.upper()} and {task}"}, {kit.DOC_REPORT: 1})
    assert got == {"x": f"[doc 1] and {task}"}  # a document id becomes [doc N]; a task id stays


def test_a_record_citing_an_unknown_doc_number_is_refused_back_to_the_reader(env: dict) -> None:
    def bad(rec: dict) -> dict:
        rec["open_tasks"][0]["proof_doc_ids"] = ["[doc 99]"]
        return rec

    model = kit.Scripted(mutate=bad)
    _r, v = _run(env, kit.CfSeat(), model)
    assert v["verdict"] == "delivered", v
    m1 = [c for c in _reads(model) if "100001" in kit.first_text(c)]
    assert "unknown_doc: [doc 99]" in json.dumps(m1[1]["messages"][-1])
