"""An identity hold can be answered "leave it out", and the answer moves the job.

WHY (2026-10-22). A chronology on one matter held because a folded email
attachment carried a date of birth that was not the client's. The requester,
an attorney, answered "Just leave it out of the chronology." Nothing could act
on that: the gate read only a release file nobody wrote, there was no withhold
at all, and ``medchron_job_resume`` refused every hold but the audit gate's. So
the Operator told him it had no way to act, and he forwarded the exchange to
SMD asking for help. Each test names the failure it stands against.

2026-10-24 (the Captain): the firm is no longer asked. A record that names
someone other than the client is withheld BY RULE wherever it is found, the
run goes on, and the delivered package discloses it. The firm's own withhold
(``identity_decide``) keeps working and keeps its wording.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import test_upload_daemon as ud
from medchron import config as config_mod, covered, decisions, identity_decide, job as job_mod
from medchron.gates import foreign_identity as FI
from medchron.resume import MARKER_PREFIX
from medchron.stages import identity as identity_stage, units as units_stage
from medchron.stages.base import StageRun
from medchron.update import coverage as update_coverage
from medchron_testkit import FakeSeat, seed_raw_manifest
from test_update_guards import _fold_scene

HELD_ID = "msgatt-92add4c914d8"
OTHER_DOB = "[p.1] (machine transcription)\nDate of Birth: 03/17/1948\noffice visit"


def _sr(job_dir: Path, firm: Path, data_root: Path, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat(),
    )


def _held_scene(data_root: Path) -> Path:
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True, exist_ok=True)
    (sd / "text" / f"{HELD_ID}.txt").write_text(OTHER_DOB)
    (sd / "text" / "f1.txt").write_text("Patient seen for neck pain. " * 20)
    seed_raw_manifest(
        data_root,
        [
            {"id": "f1", "name": "clinic note", "ext": ".pdf", "ok": True},
            {
                "id": HELD_ID,
                "name": "Kaiser records 2019",
                "ext": ".pdf",
                "ok": True,
                "from_email": "RE: records",
                "container": "FW records from Kaiser.msg",
            },
        ],
    )
    return sd


def _decide(sd: Path, action: str, ids: list[str]) -> None:
    FI.record_decision(
        sd, action=action, ids=ids, by="attorney@firm.example", source="msg-1", at="2026-10-22T19:03:00Z"
    )


# ---- the decision file --------------------------------------------------------
def test_a_decision_records_who_decided_and_a_later_one_replaces_it(data_root: Path) -> None:
    sd = data_root / "example-matter"
    _decide(sd, "release", [HELD_ID])
    _decide(sd, "withhold", ["92add4c914d8"])  # the same document, named by its sha12
    assert FI.withheld(sd) == {"92add4c914d8"} and FI.released(sd) == set()
    body = json.loads((sd / FI.RELEASE_FILE).read_text())
    assert [x["action"] for x in body["decisions"]] == ["release", "withhold"]
    assert body["decisions"][-1]["by"] == "attorney@firm.example" and body["decisions"][-1]["source"] == "msg-1"


def test_a_decision_without_who_or_where_is_refused(data_root: Path) -> None:
    with pytest.raises(ValueError):
        FI.record_decision(data_root / "example-matter", action="withhold", ids=[HELD_ID], by="", source="m", at="t")


# ---- the identity stage --------------------------------------------------------
def test_an_attachment_naming_another_person_is_withheld_by_rule_and_the_run_goes_on(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: restore the identity stage's exit 2 and this returns 2."""
    sd = _held_scene(data_root)
    log: list[str] = []
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, log)) == 0, log
    assert FI.withheld(sd) == {HELD_ID}
    assert FI.withheld_reason(sd, HELD_ID) == FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]
    body = json.loads((sd / FI.RELEASE_FILE).read_text())
    assert body["decisions"][-1]["by"] == "rule"
    # Decided once: a second pass finds nothing new and records nothing.
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, log)) == 0
    assert len(json.loads((sd / FI.RELEASE_FILE).read_text())["decisions"]) == 1


def test_the_identity_stage_takes_a_withheld_attachment_out_of_the_built_units(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """build_units ran before the identity stage, so the attachment is already
    in a unit; the rule withhold must take it out, as a rerun of build_units
    would. FALSIFIER: drop ``_leave_out`` and the attachment stays composed."""
    sd = _held_scene(data_root)
    (sd / "units").mkdir()
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": "f1"}, {"id": HELD_ID}]))
    (sd / "units" / "_unassigned.json").write_text(json.dumps([{"id": HELD_ID}]))
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, [])) == 0
    assert [r["id"] for r in json.loads((sd / "units" / "alpha.json").read_text())] == ["f1"]
    assert json.loads((sd / "units" / "_unassigned.json").read_text()) == [{"id": HELD_ID}]  # not a unit


def test_the_hold_line_names_the_attachment_and_its_email_never_the_other_person(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    # FALSIFIER (2026-10-22): the line carried the id and "a date of birth
    # 03/17/1948 that is not the client's"; the driver copies it into the job's
    # reason, the Operator relayed the id as an internal tag, could name no
    # file, and sent the attorney to search the saved emails himself.
    sd = _held_scene(data_root)
    log: list[str] = []
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, log)) == 0
    line = next(x for x in log if x.startswith("IDENTITY:"))
    assert "name a different person than the client" in line and "left out by rule" in line
    assert HELD_ID in line and '"Kaiser records 2019"' in line
    assert 'attached to the email "FW records from Kaiser.msg"' in line
    assert "1948" not in line and "date of birth" not in line.lower()
    detail = json.loads((sd / identity_stage.HOLDS_FILE).read_text())
    assert detail[0]["id"] == HELD_ID and any("1948" in f for f in detail[0]["found"])  # SMD's, on the seat


def test_an_attachment_the_manifest_cannot_name_is_held_by_id_alone(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sd = _held_scene(data_root)
    seed_raw_manifest(
        data_root,
        [{"id": HELD_ID, "ext": ".pdf", "ok": True, "from_email": "e"}],
    )
    log: list[str] = []
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, log)) == 0
    line = next(x for x in log if x.startswith("IDENTITY:"))
    assert HELD_ID in line and "attached to the email" not in line and '"' not in line.split(HELD_ID)[1][:2]
    assert (sd / identity_stage.HOLDS_FILE).is_file()


# ---- build_units: nothing downstream reads it ----------------------------------
def test_a_withheld_document_joins_no_unit(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sd = _held_scene(data_root)
    (sd / "extracted.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {
                    "id": "f1",
                    "name": "clinic note",
                    "ext": ".pdf",
                    "text_path": str(sd / "text" / "f1.txt"),
                    "chars": 9,
                },
                {"id": HELD_ID, "name": "attachment", "ext": ".pdf", "text_path": str(sd / "text" / f"{HELD_ID}.txt")},
            ]
        )
    )
    _decide(sd, "withhold", [HELD_ID])
    log: list[str] = []
    assert units_stage.run(_sr(job_dir, firm_config_path, data_root, log)) == 0, log
    ids = [
        r["id"] for f in (sd / "units").glob("*.json") if not f.name.startswith("_") for r in json.loads(f.read_text())
    ]
    assert ids == ["f1"]
    assert any("left out by an identity decision" in line for line in log)


# ---- disclosure and coverage ---------------------------------------------------
def test_a_withheld_document_is_disclosed_as_left_out_not_held_as_residue(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sd = _held_scene(data_root)
    (sd / "units").mkdir()
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": "f1", "name": "clinic note"}]))
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm_config_path))
    assert decisions.orphans(job, cfg, sd, job.units[0], dry_run=False).held  # before: unexplained residue
    _decide(sd, "withhold", [HELD_ID])
    d = decisions.orphans(job, cfg, sd, job.units[0], dry_run=False)
    assert not d.held
    entry = {"name": "Kaiser records 2019.pdf", "reason": FI.WITHHELD_REASON}  # keyed as coverage keys a pulled row
    assert entry in json.loads((sd / "orphans.json").read_text())["orphans"]


def test_a_rule_withhold_is_disclosed_with_the_rules_reason_and_counts_as_accounted_for(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: disclose every withhold as WITHHELD_REASON (the firm's
    direction), which would say the firm decided something it never saw."""
    sd = _held_scene(data_root)
    (sd / "units").mkdir()
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": "f1", "name": "clinic note", "ext": ".pdf"}]))
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm_config_path))
    FI.withhold_by_rule(sd, [HELD_ID], ground=FI.RULE_ANOTHER_PERSON)
    assert not decisions.orphans(job, cfg, sd, job.units[0], dry_run=False).held
    reason = "names a person other than the client, so it was left out of this chronology"
    assert {"name": "Kaiser records 2019.pdf", "reason": reason} in json.loads((sd / "orphans.json").read_text())[
        "orphans"
    ]
    sets = covered.covered_sets(sd, "alpha", cfg)
    assert HELD_ID in sets["covered"] and HELD_ID not in sets["uncovered"]


def test_a_later_firm_withhold_takes_the_firms_wording() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as t:
        sd = Path(t)
        FI.withhold_by_rule(sd, [HELD_ID], ground=FI.RULE_UNPLACED)
        assert FI.withheld_reason(sd, HELD_ID) == FI.RULE_REASONS[FI.RULE_UNPLACED]
        _decide(sd, "withhold", [HELD_ID])
        assert FI.withheld_reason(sd, HELD_ID) == FI.WITHHELD_REASON
        assert FI.withheld_reason(sd, "never-decided") == FI.WITHHELD_REASON


def test_a_withheld_document_counts_as_accounted_for_so_an_update_never_asks_again(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sd = _held_scene(data_root)
    (sd / "units").mkdir()
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": "f1", "name": "clinic note", "ext": ".pdf"}]))
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm_config_path))
    _decide(sd, "withhold", [HELD_ID])
    assert not decisions.orphans(job, cfg, sd, job.units[0], dry_run=False).held
    sets = covered.covered_sets(sd, "alpha", cfg)
    assert HELD_ID in sets["covered"] and HELD_ID not in sets["uncovered"]


def test_the_coverage_gate_finds_a_disclosed_orphan_whose_extension_is_kept_apart(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The latent key mismatch: orphans.json named the bare file, the gate
    looked it up with its extension, and called the disclosed file dropped."""
    from medchron.stages import coverage as coverage_stage

    sd = _held_scene(data_root)
    (sd / "units").mkdir()
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": "f1", "name": "clinic note", "ext": ".pdf"}]))
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm_config_path))
    # The control: the entry keyed the old way (bare name) trips the gate.
    (sd / "orphans.json").write_text(json.dumps({"orphans": [{"name": "attachment", "reason": FI.WITHHELD_REASON}]}))
    log: list[str] = []
    coverage_stage.run(_sr(job_dir, firm_config_path, data_root, log))
    assert any("COVERAGE GATE FAIL" in line for line in log), log
    _decide(sd, "withhold", [HELD_ID])
    decisions.orphans(job, cfg, sd, job.units[0], dry_run=False)
    log.clear()
    coverage_stage.run(_sr(job_dir, firm_config_path, data_root, log))
    assert not any("COVERAGE GATE FAIL" in line for line in log), log


def test_an_updates_skip_list_counts_a_withheld_document_accounted_for(tmp_path: Path) -> None:
    ud_dir = tmp_path / "runs" / "alpha" / "update"
    ud_dir.mkdir(parents=True)
    (ud_dir / "selection.json").write_text(json.dumps({"skipped": {FI.WITHHELD_REASON: [HELD_ID]}}))
    (tmp_path / "raw_manifest.jsonl").write_text(json.dumps({"id": HELD_ID, "ok": True}) + "\n")
    assert update_coverage.covered_sets(tmp_path, "alpha")["covered"] == [HELD_ID]


def test_the_fold_skips_a_firm_withheld_attachment_and_records_no_rule_decision(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    job, cfg = job_mod.load(job_dir), config_mod.load(str(firm_config_path))
    sd, sha12 = _fold_scene(data_root, "Patient Name: Gamma Other\nDOB: 05/05/1990\nOffice visit for knee pain.")
    _decide(sd, "withhold", [sha12])
    d = decisions.fold(job, cfg, sd, dry_run=False)
    assert not d.held and d.payload["fold"] == []
    assert FI.withheld_reason(sd, sha12) == FI.WITHHELD_REASON  # the firm's, not overwritten by the rule


# ---- the root command ----------------------------------------------------------
JOB = "01M5J9RGY9G9FFXYBZ3CH59XBY"
IDENTITY_REASON = (
    "identity: an emailed attachment names a different person than the client; last output: IDENTITY HOLD: 1 ..."
)


def _seat(tmp_path: Path, job_dir: Path, *, reason: str = IDENTITY_REASON, daemon_state: str = "held"):
    d, broker = ud._daemon(tmp_path)
    broker.rows[JOB] = {"state": "held", "reason": reason}
    jd = d.job_dir(JOB)
    jd.mkdir(parents=True)
    shutil.copy(job_dir / "job.yaml", jd / "job.yaml")
    (jd / "daemon.json").write_text(json.dumps({"claimed": True, "state": daemon_state}))
    return d


def test_the_command_records_the_firms_withhold_and_resumes_through_the_daemon(
    tmp_path: Path, job_dir: Path, data_root: Path
) -> None:
    sd = _held_scene(data_root)
    d = _seat(tmp_path, job_dir)
    p = identity_decide.plan(d, JOB, action="withhold", ids=[HELD_ID], by="attorney@firm.example", source="msg-1")
    identity_decide.apply(d, p)
    assert FI.withheld(sd) == {HELD_ID}
    marker = json.loads((d.queue / f"{MARKER_PREFIX}{JOB}.json").read_text())
    assert marker["job_id"] == JOB and marker["redo"] == ["build_units"] and "attorney@firm.example" in marker["reason"]


def test_a_release_reopens_nothing_upstream(tmp_path: Path, job_dir: Path, data_root: Path) -> None:
    _held_scene(data_root)
    d = _seat(tmp_path, job_dir)
    p = identity_decide.plan(d, JOB, action="release", ids=[HELD_ID], by="smd", source="vfy_x")
    assert p["marker"]["redo"] == []


@pytest.mark.parametrize(
    ("kw", "ids", "needle"),
    [
        ({"reason": "refused: list_matter: x"}, [HELD_ID], "not an identity hold"),
        ({"daemon_state": "running"}, [HELD_ID], "does not hold"),
        ({}, ["msgatt-000000000000"], "not documents on job"),
    ],
)
def test_the_command_refuses_what_it_cannot_record(
    tmp_path: Path, job_dir: Path, data_root: Path, kw: dict, ids: list[str], needle: str
) -> None:
    _held_scene(data_root)
    d = _seat(tmp_path, job_dir, **kw)
    with pytest.raises(identity_decide.Refused, match=needle):
        identity_decide.plan(d, JOB, action="withhold", ids=ids, by="attorney@firm.example", source="msg-1")
    assert not (d.queue / f"{MARKER_PREFIX}{JOB}.json").exists()


# ---- an update (2026-10-24) --------------------------------------------------
def test_an_updates_rule_withhold_is_covered_named_in_records_reviewed_and_not_counted(tmp_path: Path) -> None:
    """The update's identity screen withholds by rule (update_select); the
    document counts as accounted for, so the next update never reads it again,
    and Records Reviewed names it without counting it among the documents
    reviewed. FALSIFIER: drop the rule key from ``update/reviewed.accounted``
    and the document is reported as "not read" and counted."""
    from medchron.update import reviewed as RV

    rule = FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]
    ud_dir = tmp_path / "runs" / "alpha" / "update"
    ud_dir.mkdir(parents=True)
    sel = {"skipped": {rule: ["d9"]}, "exhibits": []}
    (ud_dir / "selection.json").write_text(json.dumps(sel))
    rows = [{"id": "d9", "ok": True, "name": "Other Clinic records", "ext": ".pdf"}, {"id": "d1", "ok": True}]
    (tmp_path / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert "d9" in update_coverage.covered_sets(tmp_path, "alpha")["covered"]
    out = RV.accounted(rows, sel, [], [{"id": "d1", "exhibit": 5}], [])
    assert out["docs"] == 1 and out["left_out"] == ["Other Clinic records.pdf"]
    assert out["unread"] == []
