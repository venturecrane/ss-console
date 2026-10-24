"""The staff reassignment (``medchron/litigation/reassign/``): a $0 litigation
job kind that moves matters' Person Assisting from one or more people to one
person, then the open tasks the practice system did not move itself, reading
every write back.

Every name, number and id here is invented. The fake practice system applies
a write only after a later read (the vendor answers before it applies), moves
a matter's open tasks itself for the tasks it is told to, and can refuse to
ever apply a chosen write, so the read-back loop and its bound are exercised,
not assumed."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from litigation_testkit import make_inputs, make_job
from medchron.litigation import job as job_mod
from medchron.litigation.reassign import run as run_mod, spec as spec_mod, wake as wake_mod
from medchron.litigation.reassign.write import converge

PAT = "aaaaaaaa-0000-4000-8000-000000000001"  # from, enabled
WEN = "aaaaaaaa-0000-4000-8000-000000000002"  # from, disabled
DANA = "aaaaaaaa-0000-4000-8000-000000000003"  # to, enabled (double space in the record)
ED1 = "aaaaaaaa-0000-4000-8000-000000000004"  # attorney, enabled
ED2 = "aaaaaaaa-0000-4000-8000-000000000005"  # the same attorney, a second disabled record
CHR = "aaaaaaaa-0000-4000-8000-000000000006"  # another attorney
OTHER = "aaaaaaaa-0000-4000-8000-000000000007"

STAFF = [
    {"id": PAT, "firstName": "Pat", "lastName": "Example", "enabled": True},
    {"id": WEN, "firstName": "Wendy", "lastName": "Sample", "enabled": False},
    {"id": DANA, "firstName": "Dana", "lastName": " Newhire", "enabled": True},
    {"id": ED1, "firstName": "Edward", "lastName": "Lawyer", "enabled": True},
    {"id": ED2, "firstName": "Edward", "lastName": "Lawyer", "enabled": False},
    {"id": CHR, "firstName": "Carl", "lastName": "Senior", "enabled": True},
    {"id": OTHER, "firstName": "Olive", "lastName": "Other", "enabled": True},
]


def mid(n: int) -> str:
    return f"bbbbbbbb-0000-4000-8000-{n:012d}"


def tid(n: int) -> str:
    return f"cccccccc-0000-4000-8000-{n:012d}"


CASE = "Matter/CaseDetails/StandardCaseDetails/CaseNumber"


def world() -> dict[str, Any]:
    """matters: id -> row, layouts, file names; tasks: id -> task."""
    m = {
        # Ed's litigation case, Pat assisting: moves. T1 the vendor moves; T2 it does not.
        mid(1): ({"id": mid(1), "number": "100001", "personResponsibleStaffId": ED1, "personAssistingStaffId": PAT},
                 {CASE: "CV-0001"}, []),
        # Ed's (second record) litigation case by its papers, Wendy assisting: moves. T3 a straggler.
        mid(2): ({"id": mid(2), "number": "100002", "personResponsibleStaffId": ED2, "personAssistingStaffId": WEN},
                 {}, ["Complaint for damages.pdf"]),
        # Carl's litigation case, Pat assisting: outside an "Ed's" scope.
        mid(3): ({"id": mid(3), "number": "100003", "personResponsibleStaffId": CHR, "personAssistingStaffId": PAT},
                 {CASE: "CV-0003"}, []),
        # Ed's case NOT in litigation, Pat assisting: outside a litigation-only scope.
        mid(4): ({"id": mid(4), "number": "100004", "personResponsibleStaffId": ED1, "personAssistingStaffId": PAT},
                 {}, ["Intake.pdf"]),
        # Someone else's paralegal: never a candidate.
        mid(5): ({"id": mid(5), "number": "100005", "personResponsibleStaffId": ED1, "personAssistingStaffId": OTHER},
                 {CASE: "CV-0005"}, []),
    }  # fmt: skip
    t = {
        tid(1): {"id": tid(1), "matter": {"id": mid(1)}, "subject": "Calendar the CMC", "assignees": [{"id": PAT}]},
        tid(2): {"id": tid(2), "matter": {"id": mid(1)}, "subject": "Draft responses", "dueDateOnly": "2026-03-01T00:00:00",
                 "assignees": [{"id": PAT}, {"id": CHR}, {"id": PAT}], "categories": ["Discovery"],
                 "subTasks": [{"subject": "first"}]},
        tid(3): {"id": tid(3), "matter": {"id": mid(2)}, "subject": "Serve the summons", "assignees": [{"id": WEN}]},
        tid(4): {"id": tid(4), "matter": {"id": mid(3)}, "subject": "Not ours", "assignees": [{"id": PAT}]},
    }  # fmt: skip
    return {"matters": m, "tasks": t}


class Vendor:
    """The practice system, with its lag: a write applies on the SECOND read
    after it; ``auto`` tasks move with their matter; ``stuck`` writes never
    apply; ``late`` writes apply only once sent twice."""

    def __init__(self, w: dict[str, Any], *, auto=(tid(1),), stuck=(), late=(), staff=None) -> None:
        self.w = copy.deepcopy(w)
        self.auto, self.stuck, self.late = set(auto), set(stuck), set(late)
        self.staff = staff or STAFF
        self.pending: dict[str, list[Any]] = {}  # key -> [reads until applied, apply fn]
        self.sent: dict[str, int] = {}
        self.writes: list[tuple[str, str, dict[str, Any]]] = []
        self.reads: dict[str, int] = {}

    def _tick(self, key: str) -> None:
        self.reads[key] = self.reads.get(key, 0) + 1
        p = self.pending.get(key)
        if p:
            p[0] -= 1
            if p[0] <= 0:
                del self.pending[key]
                p[1]()

    def get(self, path: str, **params: Any) -> Any:
        if params.get("Offset"):
            return {"value": []}
        if path == "/staff":
            return {"value": self.staff}
        if path == "/matters":
            return {"value": [row for row, _l, _f in self.w["matters"].values()] if params["Status"] == "Open" else []}
        if path == "/tasks":
            return {"value": [t for t in self.w["tasks"].values() if t["matter"]["id"] == params["MatterId"]]}
        parts = path.strip("/").split("/")
        if parts[0] == "tasks":
            self._tick(path)
            return copy.deepcopy(self.w["tasks"][parts[1]])
        row, layout, files = self.w["matters"][parts[1]]
        if path.endswith("/layouts"):
            return {"value": [{"values": [{"key": k, "value": v} for k, v in layout.items()]}]}
        if path.endswith("/documents/files"):
            return {"value": [{"id": f"f{i}", "name": n} for i, n in enumerate(files)]}
        self._tick(path)
        return copy.deepcopy(row)

    def request(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        self.writes.append((method, path, copy.deepcopy(json)))
        self.sent[path] = self.sent.get(path, 0) + 1
        kind, key = path.strip("/").split("/")
        if key in self.stuck or (key in self.late and self.sent[path] < 2):
            return {}
        if kind == "matters":
            self.pending[path] = [2, lambda: self._patch_matter(key, json)]
        else:
            self.pending[path] = [2, lambda: self._put_task(key, json)]
        return {}

    def _patch_matter(self, key: str, body: dict[str, Any]) -> None:
        """As the vendor does (proven 2026-10-15): the main from
        ``personAssistingStaffId``, the additional list ONLY from
        ``personAssistingStaffIds``; ``personAssistingStaffs`` is accepted and
        ignored, and the list read back never holds the main."""
        row = self.w["matters"][key][0]
        if "personAssistingStaffIds" in body:
            row["personAssistingStaffs"] = [
                {"id": i, "href": f"/staff/{i}", "rel": "staff"} for i in body["personAssistingStaffIds"]
            ]
        if "personAssistingStaffId" in body:
            old, row["personAssistingStaffId"] = row.get("personAssistingStaffId"), body["personAssistingStaffId"]
            for t in self.w["tasks"].values():
                if t["matter"]["id"] == key and t["id"] in self.auto and old != row["personAssistingStaffId"]:
                    t["assignees"] = [{"id": row["personAssistingStaffId"] if a["id"] == old else a["id"]}
                                      for a in t["assignees"]]  # fmt: skip

    def _put_task(self, key: str, body: dict[str, Any]) -> None:
        self.w["tasks"][key]["assignees"] = [{"id": i} for i in body["assigneeIds"]]


class Seat:
    def __init__(self, vendor: Vendor) -> None:
        self.client = vendor


SPEC = {
    "from_names": ["Pat Example", "Wendy"],
    "to_name": "Dana Newhire",
    "attorney_names": ["Ed"],
    "litigation_only": True,
    "statuses": ["Open", "Pending"],
}


def make_run(tmp_path: Path, vendor: Vendor, **spec: Any) -> run_mod.ReassignRun:
    jd = make_job(tmp_path / "job", report="reassign", reassign={**SPEC, **spec}, scope={"all": True},
                  request_text="update all cases assigned to Pat Example or Wendy to Dana Newhire")  # fmt: skip
    return run_mod.ReassignRun(
        jd, inputs_dir=str(make_inputs(tmp_path / "inputs")), seat_factory=lambda: Seat(vendor), log=lambda m: None,
        poll_seconds=0.0, settle_seconds=0.0, write_pause=0.0, sleep=lambda s: None,
    )  # fmt: skip


def verdict(r: run_mod.ReassignRun) -> dict[str, Any]:
    return json.loads(r.run().to_json())


def assignees(v: Vendor, t: str) -> list[str]:
    return [a["id"] for a in v.w["tasks"][t]["assignees"]]


# ---- the move, end to end ---------------------------------------------------------------


def test_eds_litigation_cases_move_and_the_vendor_moves_some_tasks_itself(tmp_path: Path) -> None:
    v = Vendor(world())
    out = verdict(make_run(tmp_path, v))
    assert out["verdict"] == "delivered", out
    c = out["counts"]
    assert (c["cases_to_move"], c["cases_moved"], c["cases_not_moved"]) == (2, 2, 0)
    assert (c["tasks_auto_moved"], c["tasks_swept"], c["tasks_moved"], c["tasks_not_moved"]) == (1, 2, 3, 0)
    assert c["cases_outside_scope"] == 2 and c["tasks_naming_from"] == 3
    assert v.w["matters"][mid(1)][0]["personAssistingStaffId"] == DANA
    assert v.w["matters"][mid(2)][0]["personAssistingStaffId"] == DANA
    # outside the scope: untouched, and counted by responsible attorney
    assert v.w["matters"][mid(3)][0]["personAssistingStaffId"] == PAT
    assert v.w["matters"][mid(4)][0]["personAssistingStaffId"] == PAT
    assert assignees(v, tid(4)) == [PAT]
    d = out["reassign"]
    assert d["outside_by_attorney"] == {"Carl Senior": 1, "Edward Lawyer": 1}
    assert d["from"] == ["Pat Example", "Wendy Sample"] and d["to"] == "Dana Newhire"
    assert d["attorneys"] == ["Edward Lawyer"] and d["from_with_nothing"] == []
    # the main and the (empty) additional list are written together, and the vendor's own task move is not re-sent
    patches = [w for w in v.writes if w[0] == "PATCH"]
    assert sorted(p[1] for p in patches) == [f"/matters/{mid(1)}", f"/matters/{mid(2)}"]
    assert all(p[2] == {"personAssistingStaffId": DANA, "personAssistingStaffIds": []} for p in patches)
    assert not any(w[1] == f"/tasks/{tid(1)}" for w in v.writes)
    assert out["proof"]["readback"] == "confirmed" and "alerts" not in out["proof"]


def test_the_straggler_put_keeps_co_assignees_categories_and_owner(tmp_path: Path) -> None:
    v = Vendor(world())
    verdict(make_run(tmp_path, v))
    puts = [w for w in v.writes if w == ("PUT", f"/tasks/{tid(2)}", w[2])]
    assert len(puts) == 1
    body = puts[0][2]
    assert body["assigneeIds"] == [DANA, CHR]  # Pat swapped for Dana, Carl kept, deduped
    assert body["staffId"] == ED1  # the matter's responsible staff owns the full replace
    assert body["categories"] == ["Discovery"] and body["subject"] == "Draft responses"
    assert body["dueDateOnly"] == "2026-03-01" and body["matterId"] == mid(1)
    assert assignees(v, tid(2)) == [DANA, CHR] and assignees(v, tid(3)) == [DANA]


def test_a_task_that_takes_on_its_second_put_converges(tmp_path: Path) -> None:
    v = Vendor(world(), late=(tid(3),))
    out = verdict(make_run(tmp_path, v))
    assert out["counts"]["tasks_not_moved"] == 0 and assignees(v, tid(3)) == [DANA]
    assert v.sent[f"/tasks/{tid(3)}"] == 2


def test_a_task_that_never_converges_is_listed_never_dropped(tmp_path: Path) -> None:
    v = Vendor(world(), stuck=(tid(3),))
    out = verdict(make_run(tmp_path, v))
    assert out["verdict"] == "delivered"
    assert out["counts"]["tasks_not_moved"] == 1 and out["reassign"]["tasks_not_moved"] == {"100002": 1}
    assert out["proof"]["alerts"] == ["readback_mismatch"]  # SMD is paged
    assert v.sent[f"/tasks/{tid(3)}"] == run_mod.ATTEMPTS  # bounded


def test_falsifier_a_matter_write_that_never_takes_is_reported_by_its_read_back(tmp_path: Path) -> None:
    """Fails if the read-back is skipped: the vendor ACCEPTS the PATCH and never
    applies it, so only reading the matter again can tell."""
    v = Vendor(world(), stuck=(mid(2),))
    out = verdict(make_run(tmp_path, v))
    assert out["counts"]["cases_moved"] == 1 and out["reassign"]["cases_not_moved"] == ["100002"]
    assert v.reads[f"/matters/{mid(2)}"] > run_mod.ATTEMPTS  # read after every write, more than once
    assert assignees(v, tid(3)) == [WEN]  # its tasks are left with the case


def test_converge_with_the_read_back_removed_would_claim_success() -> None:
    """The mutation the falsifier above guards: a check that does not read."""
    still, _ = converge(["k"], lambda k: None, lambda k: True, sleep=lambda s: None)
    assert still == []  # a no-read check "succeeds", which is why every check reads the far end
    still, reads = converge(["k"], lambda k: None, lambda k: False, attempts=2, polls=3, sleep=lambda s: None)
    assert still == ["k"] and reads == 1 + 2 * 3


# ---- scope ------------------------------------------------------------------------------


def test_all_cases_and_no_attorney_moves_everything_pat_and_wendy_assist(tmp_path: Path) -> None:
    v = Vendor(world())
    out = verdict(make_run(tmp_path, v, litigation_only=False, attorney_names=[]))
    assert out["counts"]["cases_moved"] == 4 and out["counts"]["cases_outside_scope"] == 0
    assert v.w["matters"][mid(5)][0]["personAssistingStaffId"] == OTHER


def test_a_from_person_with_nothing_in_scope_is_named(tmp_path: Path) -> None:
    w = world()
    del w["matters"][mid(2)]
    out = verdict(make_run(tmp_path, Vendor(w)))
    assert out["reassign"]["from_with_nothing"] == ["Wendy Sample"]


def test_nothing_in_scope_delivers_the_counts_and_writes_nothing(tmp_path: Path) -> None:
    v = Vendor(world())
    out = verdict(make_run(tmp_path, v, attorney_names=["Olive"]))
    assert out["verdict"] == "delivered" and out["counts"]["cases_to_move"] == 0
    assert out["proof"]["nothing_to_file"] is True and v.writes == []


# ---- refusals ---------------------------------------------------------------------------


def test_an_ambiguous_name_refuses_and_writes_nothing(tmp_path: Path) -> None:
    staff = [
        *STAFF,
        {"id": "aaaaaaaa-0000-4000-8000-000000000099", "firstName": "Wendy", "lastName": "Second", "enabled": True},
    ]
    v = Vendor(world(), staff=staff)
    out = verdict(make_run(tmp_path, v))
    assert out["verdict"] == "held" and out["reason"].startswith("staff_unresolved: from ambiguous")
    assert out["reassign"]["unresolved"] == {"role": "from", "kind": "ambiguous", "name": "Wendy", "matches": 2}
    assert v.writes == []


def test_a_disabled_person_to_move_to_refuses(tmp_path: Path) -> None:
    v = Vendor(world())
    out = verdict(make_run(tmp_path, v, to_name="Wendy Sample", from_names=["Pat Example"]))
    assert out["verdict"] == "held" and out["reason"] == "staff_unresolved: to inactive"
    assert v.writes == []


def test_names_resolve_with_whitespace_case_and_a_first_name_prefix() -> None:
    assert spec_mod.resolve("dana   NEWHIRE", STAFF, "to").active_ids == (DANA,)
    ed = spec_mod.resolve("Ed's", STAFF, "attorney")
    assert ed.name == "Edward Lawyer" and ed.ids == (ED1, ED2)  # one person, two records
    with pytest.raises(spec_mod.Unresolved, match="from unknown"):
        spec_mod.resolve("Nobody", STAFF, "from")


# ---- proof run, journal, envelope ---------------------------------------------------------


def test_a_proof_run_stops_before_the_first_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDCHRON_PROOF_RUN", "1")
    v = Vendor(world())
    out = verdict(make_run(tmp_path, v))
    assert out["verdict"] == "dry_run" and out["stage"] == "matters"
    assert out["counts"]["cases_to_move"] == 2 and out["counts"]["tasks_naming_from"] == 3
    assert v.writes == []


def test_every_write_is_journaled_and_rides_the_verdict(tmp_path: Path) -> None:
    v = Vendor(world(), late=(tid(3),))
    r = make_run(tmp_path, v)
    out = verdict(r)
    rows = r.journal.rows()
    assert [x["phase"] for x in rows] == ["intent", "result"] * len(v.writes)
    assert [(w["method"], w["path"]) for w in out["writes"]] == [(m, p) for m, p, _b in v.writes]
    assert all(set(w) == {"method", "path", "status", "ok"} for w in out["writes"])


def test_the_runner_refuses_a_malformed_reassignment(tmp_path: Path) -> None:
    with pytest.raises(job_mod.LitigationJobError):
        job_mod.load(make_job(tmp_path / "a", report="reassign", reassign={**SPEC, "statuses": []}))
    with pytest.raises(job_mod.LitigationJobError):
        job_mod.load(make_job(tmp_path / "b", reassign=SPEC))  # a status list carrying a reassignment


def test_the_wake_carries_names_numbers_and_counts_only() -> None:
    text = wake_mod.compose(
        job_id="01KTJ0BX0000000000000000AA", skill="litigation-status", state="delivered",
        fields={"counts": {"cases_moved": 2, "tasks_not_moved": 1}}, env={"requester": "admin@firm.example"},
        trig="request", stage=None, hold_line=None,
        detail={"from": ["Pat Example", "<script>"], "to": "Dana Newhire", "attorneys": [],
                "litigation_only": True, "statuses": ["Open", "Pending"], "cases_not_moved": ["100002", "a b c"],
                "tasks_not_moved": {"100002": 1}, "outside_by_attorney": {"Carl Senior": 1}},
    )  # fmt: skip
    assert "Kind: litigation. Report: reassign." in text
    assert "Moved from: Pat Example." in text and "<script>" not in text
    assert "Cases not moved: 100002." in text and "Tasks not moved: 100002 1." in text
    assert "Attorneys: all." in text and "Scope: litigation cases only; statuses Open, Pending." in text


def test_the_reassignment_waits_for_its_own_proof_run() -> None:
    from medchron.lane_proof import lane_key

    assert lane_key("litigation", {"report": "reassign"}) == "litigation-status.reassign"
    assert lane_key("litigation", {}) == "litigation-status"


# ---- the additional assisting list (the 2026-10-15 dry-run defect) ------------------------


def _extra(v: Vendor, m: str) -> list[str]:
    return [x["id"] for x in v.w["matters"][m][0].get("personAssistingStaffs") or []]


def _listed_world(**rows: tuple[str, list[str]]) -> dict[str, Any]:
    """Ed's litigation cases only, each (main, additional list)."""
    w: dict[str, Any] = {"matters": {}, "tasks": {}}
    for n, (main, extra) in enumerate(rows.values(), start=11):
        w["matters"][mid(n)] = (
            {"id": mid(n), "number": f"2000{n}", "personResponsibleStaffId": ED1, "personAssistingStaffId": main,
             "personAssistingStaffs": [{"id": i, "href": f"/staff/{i}", "rel": "staff"} for i in extra]},
            {CASE: f"CV-{n}"}, [],
        )  # fmt: skip
    return w


def test_from_only_in_the_list_with_to_already_the_main_is_moved_out_of_the_list(tmp_path: Path) -> None:
    v = Vendor(_listed_world(a=(DANA, [PAT])))
    out = verdict(make_run(tmp_path, v))
    assert out["counts"]["cases_to_move"] == 1 and out["counts"]["cases_moved"] == 1, out
    row = v.w["matters"][mid(11)][0]
    assert row["personAssistingStaffId"] == DANA and _extra(v, mid(11)) == []
    assert v.writes[0][2] == {"personAssistingStaffId": DANA, "personAssistingStaffIds": []}


def test_from_in_the_list_with_another_main_is_replaced_in_the_list_main_unchanged(tmp_path: Path) -> None:
    v = Vendor(_listed_world(a=(OTHER, [PAT, CHR])))
    verdict(make_run(tmp_path, v))
    assert v.w["matters"][mid(11)][0]["personAssistingStaffId"] == OTHER
    assert _extra(v, mid(11)) == [DANA, CHR]


def test_from_as_main_with_another_person_in_the_list_keeps_the_list(tmp_path: Path) -> None:
    v = Vendor(_listed_world(a=(PAT, [CHR])))
    verdict(make_run(tmp_path, v))
    assert v.w["matters"][mid(11)][0]["personAssistingStaffId"] == DANA
    assert _extra(v, mid(11)) == [CHR]


def test_falsifier_a_list_write_that_never_takes_is_not_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails if the list's read-back is removed: the main is already TO, so a
    check of the main alone would call the matter moved while FROM stays."""
    v = Vendor(_listed_world(a=(DANA, [PAT])), stuck=(mid(11),))
    out = verdict(make_run(tmp_path, v))
    assert out["counts"]["cases_moved"] == 0 and out["reassign"]["cases_not_moved"] == ["200011"]
    assert out["proof"]["alerts"] == ["readback_mismatch"]


def test_the_ignored_field_name_would_not_move_the_list() -> None:
    """The fake honours only ``personAssistingStaffIds``, as the vendor does, so
    a regression to ``personAssistingStaffs`` leaves FROM in the list."""
    v = Vendor(_listed_world(a=(DANA, [PAT])))
    v.request("PATCH", f"/matters/{mid(11)}", json={"personAssistingStaffs": [], "personAssistingStaffId": DANA})
    for _ in range(3):
        v.get(f"/matters/{mid(11)}")
    assert _extra(v, mid(11)) == [PAT]


def test_assisting_desired_state_rules() -> None:
    from medchron.litigation.reassign import assisting

    f = {PAT, WEN}
    assert assisting.desired(PAT, [CHR], f, DANA) == (DANA, [CHR])
    assert assisting.desired(OTHER, [PAT, WEN, CHR], f, DANA) == (OTHER, [DANA, CHR])
    assert assisting.desired(PAT, [DANA, WEN], f, DANA) == (DANA, [])  # TO leaves the list when it is the main
    assert assisting.body("", [PAT], f, DANA) == {"personAssistingStaffIds": [DANA]}  # no main is never sent empty
