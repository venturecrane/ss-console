"""Tests for date-prep-brief: file-status facts, the closed catalog, candidate
selection, recipients, the three files, and the gate end to end.

Run from operator/:  python -m pytest skills/date-prep-brief -q
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pre_run = _load("dpb_pre_run", "pre_run.py")
file_status = _load("dpb_file_status", "file_status.py")
catalog = pre_run._CATALOG
brief = pre_run._BRIEF
casework = pre_run._CASEWORK

TODAY = date(2026, 9, 25)
M105 = "m-105"
STEPS = {
    "witness_list_finalize": "prepares",
    "exhibit_list_finalize": "prepares",
    "binder_assemble": "handles",
    "records_refresh": "prepares",
    "motion_calendar_refresh": "surfaces",
}
CFG = {
    "case_manager": {"date_prep": {"level": "handles", "window_days": 14, "steps": STEPS}},
    "escalation": {"case_alert_routing": {"mode": "matter_staff"}},
    "scope": {"inbound_allow_from": ["@firm.test"]},
    "personas": [{"skills": [{"name": "medical-records-chaser", "settings": {"chase_cadence_days": 7}}]}],
}


class StubClient:
    """Answers the paths file_status reads, and records every call."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def get(self, path, **params):
        self.calls.append((path, params))
        answer = self.routes.get((path, params.get("IsCompleted")), self.routes.get(path))
        if isinstance(answer, Exception):
            raise answer
        return answer


def _status_client() -> StubClient:
    memos = [
        {
            "plainText": "[Operator] Trial binder index assembled for matter 2026-PI-105",
            "createdDate": "2026-09-20T10:00:00Z",
        },
        {"plainText": "Trial binder index assembled (typed by a person)", "createdDate": "2026-09-24T10:00:00Z"},
        {"plainText": "[Operator] # Motion Calendar - 2026-PI-105", "createdDate": "2026-09-01"},
    ]
    files = [
        {
            "id": "f-wl",
            "name": "Witness List (draft)",
            "fileExtension": ".docx",
            "dateModified": "2026-06-18T09:00:00Z",
        },
        {"id": "f-el", "name": "Exhibit List", "fileExtension": ".docx", "dateModified": "2026-09-10"},
        {"id": "f-new", "name": "Trial Order", "fileExtension": ".pdf", "dateCreated": "2026-09-22"},
        {"id": "f-kp1", "name": "Kaiser - records", "fileExtension": ".pdf", "dateCreated": "2026-04-02"},
        {"id": "f-kp2", "name": "KAISER records (supplemental)", "fileExtension": ".pdf", "dateCreated": "2026-05-01"},
    ]
    open_tasks = [
        {"id": "t-reyes", "subject": "Medical records outstanding - Dr. Reyes (request roster)"},
        {"id": "t-other", "subject": "Call the client"},
        {"id": "t-probe", "subject": "[SMD-PROBE 2026-09-01T00:00Z] records (request roster)"},
    ]
    done_tasks = [{"id": "t-kaiser", "subject": "Medical records outstanding - Kaiser (request roster)"}]
    return StubClient(
        {
            "/matters/" + M105 + "/documents/files": {"value": files},
            "/matters/" + M105 + "/memos": {"value": memos},
            ("/tasks", False): {"value": open_tasks},
            ("/tasks", True): {"value": done_tasks},
        }
    )


# ---------------------------------------------------------------------------
# file_status: facts, never prose
# ---------------------------------------------------------------------------


def test_matter_status_is_facts_only():
    status = file_status.pull_matter_status(_status_client(), M105)
    assert status["unread"] == []
    assert [f["file_id"] for f in status["files"]] == ["f-new", "f-el", "f-wl", "f-kp2", "f-kp1"]
    assert status["files"][2] == {"file_id": "f-wl", "name": "Witness List (draft).docx", "date": "2026-06-18"}
    # Only [Operator] memos count, and only the latest day per marker leaves.
    assert status["memo_markers"] == {"binder_assembled": "2026-09-20", "motion_calendar": "2026-09-01"}
    assert "Trial binder" not in json.dumps(status["memo_markers"])
    assert status["records"] == [
        {"roster_task_id": "t-reyes", "provider": "Dr. Reyes", "state": "outstanding"},
        {"roster_task_id": "t-kaiser", "provider": "Kaiser", "state": "received", "newest_record": "2026-05-01"},
    ]


def test_plain_headed_notes_still_mark_their_step():
    # The one file-note pattern (2026-09-29): a header line, then plain lines; no
    # "#" heading. Each prep routine's found line still names the step.
    memos = [
        {
            "plainText": "[Operator] Trial binder as of Sep 28, 2026\nTrial binder index assembled for matter 2026-PI-105 and filed as x.docx.\nNothing to do.",
            "createdDate": "2026-09-28",
        },
        {
            "plainText": "[Operator] Motion calendar as of Sep 27, 2026\nMotion calendar assembled: 1 motions filed.\nNothing to do.",
            "createdDate": "2026-09-27",
        },
        {
            "plainText": "[Operator] Response deadlines as of Sep 26, 2026\nProposed response deadline for RFP Set One served Sep 1 on matter 2026-PI-105.\nConfirm.",
            "createdDate": "2026-09-26",
        },
    ]
    assert file_status.memo_markers(memos) == {
        "binder_assembled": "2026-09-28",
        "motion_calendar": "2026-09-27",
        "discovery_status": "2026-09-26",
    }


def test_a_note_updated_in_place_marks_its_step_on_the_update_day():
    # The connector updates a routine's note in place (memo_tools.upsert_memo),
    # so the step's latest day is max(createdDate, lastUpdated).
    memos = [
        {
            "plainText": "[Operator] Motion calendar as of Sep 28, 2026\nMotion calendar assembled: 1 motions filed.\nNothing to do.",
            "createdDate": "2026-09-01T12:00:00Z",
            "lastUpdated": "2026-09-28T15:00:00Z",
        },
    ]
    assert file_status.memo_markers(memos) == {"motion_calendar": "2026-09-28"}


def test_an_enriched_event_uses_the_connectors_local_day_and_time():
    # An event the connector already enriched carries its local day and clock;
    # the brief uses them over its own reading of startTime.
    event = {
        "id": "e1",
        "subject": "Hearing",
        "startTime": "2026-10-07T03:00:00",
        "localDate": "2026-10-06",
        "localTime": "8:00 p.m.",
    }
    client = StubClient({"/events": {"value": [event]}})
    rows = file_status.matter_events(client, {"id": M105, "number": "2026-PI-105"}, "2026-10-01", "2026-10-31")
    assert rows[0]["date"] == "2026-10-06"
    assert rows[0]["time"] == "8:00 p.m."


def test_an_unread_part_is_named_not_emptied():
    client = _status_client()
    client.routes["/matters/" + M105 + "/memos"] = RuntimeError("boom")
    status = file_status.pull_matter_status(client, M105)
    assert status["unread"] == ["memos"]
    assert status["memo_markers"] == {}


def test_dates_come_from_the_per_matter_event_filter():
    client = StubClient(
        {
            "/matters": {"value": [{"id": M105, "number": "2026-PI-105"}, {"id": "bad id!"}]},
            "/events": {
                "value": [
                    {"id": "e-fsc", "subject": "Final Status Conference", "startTime": "2026-10-02T15:30:00Z"},
                    {
                        "id": "e-probe",
                        "subject": "[Operator] [SMD-PROBE 2026-09-01T00:00Z] x",
                        "startTime": "2026-10-01",
                    },
                ]
            },
        }
    )
    out = file_status.pull_dates(client, "2026-09-25", "2026-10-09")
    assert out["events"] == [
        {
            "event_id": "e-fsc",
            "date": "2026-10-02",
            "subject": "Final Status Conference",
            "matter_id": M105,
            "matter_number": "2026-PI-105",
            "time": "3:30 p.m.",
        }
    ]
    assert out["unreadableMatters"] == 1
    event_calls = [params for path, params in client.calls if path == "/events"]
    assert event_calls == [
        {"MatterId": M105, "From": "2026-09-25", "To": "2026-10-09", "ExcludeDeletedEvents": True, "Limit": 500}
    ]


def _one_event(event: dict) -> dict:
    client = StubClient({"/matters": {"value": [{"id": M105, "number": "2026-PI-105"}]}, "/events": {"value": [event]}})
    return file_status.pull_dates(client, "2026-09-25", "2026-10-09")["events"][0]


def test_an_event_carries_its_own_local_time_and_place():
    """Smokeball's startTime is the firm's local clock in the event's zone
    (vendor create-event: "date and time will correlate with the time zone
    provided"), read as written. The brief's first Done line says when and
    where, so the facts carry both as the event states them."""
    row = _one_event(
        {
            "id": "e-h",
            "subject": "Hearing",
            "startTime": "2026-10-06T09:30:00",
            "timeZone": "America/Los_Angeles",
            "location": "Department 3",
            "allDay": False,
        }
    )
    assert (row["date"], row["time"], row["location"]) == ("2026-10-06", "9:30 a.m.", "Department 3")
    # A 9:00 a.m. trial is 9:00 a.m., never shifted from UTC to 2:00 a.m.
    trial = _one_event(
        {"id": "e-t", "subject": "Jury Trial", "startTime": "2026-10-13T09:00:00", "timeZone": "America/Los_Angeles"}
    )
    assert (trial["date"], trial["time"]) == ("2026-10-13", "9:00 a.m.")
    # A stored 16:30 reads as the firm sees it in Smokeball: 4:30 p.m.
    late = _one_event(
        {"id": "e-l", "subject": "H", "startTime": "2026-10-06T16:30:00", "timeZone": "America/Los_Angeles"}
    )
    assert (late["date"], late["time"]) == ("2026-10-06", "4:30 p.m.")


def test_the_facts_line_passes_the_time_and_place_to_the_turn():
    candidate = {**_event("e-h", "2026-10-06"), "time": "9:30 a.m.", "location": "Department 3", "days_out": 11}
    plan = {
        "candidate": candidate,
        "status": {},
        "chases": {},
        "catalog": [],
        "recipients": {"leg": "matter_staff_responsible", "cc": []},
    }
    assert brief.facts(plan)["event"] == {
        "event_id": "e-h",
        "date": "2026-10-06",
        "time": "9:30 a.m.",
        "location": "Department 3",
        "subject": "Hearing",
        "days_out": 11,
    }


def test_the_zone_is_never_arithmetic_and_all_day_or_no_clock_means_no_time():
    no_zone = _one_event({"id": "e-a", "subject": "H", "startTime": "2026-10-02T15:30:00"})
    assert (no_zone["date"], no_zone["time"]) == ("2026-10-02", "3:30 p.m.") and "location" not in no_zone
    bad_zone = _one_event({"id": "e-b", "subject": "H", "startTime": "2026-10-02T09:30:00", "timeZone": "Mars/Base"})
    assert bad_zone["time"] == "9:30 a.m."
    no_clock = _one_event({"id": "e-d", "subject": "H", "startTime": "2026-10-02", "timeZone": "America/Los_Angeles"})
    assert no_clock["date"] == "2026-10-02" and "time" not in no_clock


def test_a_malformed_start_date_has_no_time_like_the_connector():
    for raw in ("2026-13-45T09:30:00", "2026-10-02T25:30:00", "2026-10-02Tab:cd:00"):
        assert file_status._when({"startTime": raw, "timeZone": "America/Los_Angeles"}) == (None, None)
    all_day = _one_event(
        {
            "id": "e-c",
            "subject": "H",
            "startTime": "2026-10-02T00:00:00",
            "timeZone": "America/Los_Angeles",
            "allDay": True,
        }
    )
    assert all_day["date"] == "2026-10-02" and "time" not in all_day


# ---------------------------------------------------------------------------
# catalog: closed, level-bound, basis-carrying
# ---------------------------------------------------------------------------


def _status():
    return file_status.pull_matter_status(_status_client(), M105)


def test_catalog_offers_only_what_the_facts_and_levels_support():
    chases = {"t-reyes": {"attempts": 0, "last_chased": None}}
    built = catalog.build_catalog(_status(), STEPS, chases, cadence_days=7, today=TODAY)
    ids = [e["catalog_id"] for e in built]
    # witness list exists only as a draft; the exhibit list is final; the binder
    # predates the newest file; motion calendar is at surfaces (never offered);
    # Reyes is outstanding and never chased; Kaiser was received (not offered).
    assert ids == ["binder_assemble", "witness_list_finalize", "records_refresh:t-reyes"]
    by_id = {e["catalog_id"]: e for e in built}
    assert by_id["witness_list_finalize"]["params"] == {"job": "finalize_witness_list", "file_id": "f-wl"}
    assert by_id["binder_assemble"]["params"] == {"job": "assemble_binder"}
    assert by_id["witness_list_finalize"]["skill"] == "trial-binder-assembler"
    assert by_id["binder_assemble"]["level"] == "handles"
    assert by_id["records_refresh:t-reyes"]["params"] == {
        "job": "chase_records",
        "roster_task_id": "t-reyes",
        "provider": "Dr. Reyes",
        "mode": "chase",
    }
    assert all(e["basis"] for e in built)


def test_an_unlisted_step_is_never_offered():
    assert catalog.build_catalog(_status(), {}, {}, cadence_days=7, today=TODAY) == []


def test_records_chase_respects_the_authored_cadence_and_invents_none():
    recent = {"t-reyes": {"attempts": 2, "last_chased": "2026-09-22"}}
    stale = {"t-reyes": {"attempts": 2, "last_chased": "2026-09-01"}}
    steps = {"records_refresh": "prepares"}
    assert catalog.build_catalog(_status(), steps, recent, cadence_days=7, today=TODAY) == []
    assert len(catalog.build_catalog(_status(), steps, stale, cadence_days=7, today=TODAY)) == 1
    # No authored cadence: a provider already chased is not re-offered.
    assert catalog.build_catalog(_status(), steps, stale, cadence_days=None, today=TODAY) == []


def test_newest_record_matches_distinctive_name_words_only():
    files = [
        {"name": "Valley Imaging - MRI.pdf", "date": "2026-03-01"},
        {"name": "Valley Orthopedics.pdf", "date": "2026-08-01"},
        {"name": "Dr Reyes records.pdf", "date": "2026-06-01"},
        {"name": "Reyes (undated).pdf", "date": None},
    ]
    assert file_status.newest_record("Valley Imaging Center", files) == "2026-03-01"
    assert file_status.newest_record("Dr. Reyes", files) == "2026-06-01"
    assert file_status.newest_record("Northgate Pediatrics", files) is None
    assert file_status.newest_record("Dr.", files) is None  # nothing distinctive to match on


def _update_ids(stale_days, chases=None):
    steps = {"records_refresh": "prepares"}
    built = catalog.build_catalog(_status(), steps, chases or {}, cadence_days=7, today=TODAY, stale_days=stale_days)
    return {e["catalog_id"]: e for e in built if e["params"].get("mode") == "update"}


def test_a_received_provider_with_stale_records_is_offered_an_update():
    got = _update_ids(60)
    assert list(got) == ["records_refresh:t-kaiser"]
    entry = got["records_refresh:t-kaiser"]
    assert entry["skill"] == "medical-records-chaser"
    assert entry["params"] == {
        "job": "request_updated_records",
        "roster_task_id": "t-kaiser",
        "provider": "Kaiser",
        "mode": "update",
        "newest_record": "2026-05-01",
    }
    casework._validate_step(catalog.envelope_catalog([entry])[0])


def test_the_update_offer_is_off_without_an_authored_threshold():
    assert _update_ids(None) == {}


def test_records_newer_than_the_threshold_are_not_stale():
    assert _update_ids(200) == {}  # 2026-05-01 is 147 days before 2026-09-25


def test_a_provider_asked_since_the_newest_record_is_not_asked_again():
    asked = {"t-kaiser": {"attempts": 3, "last_chased": "2026-06-10"}}
    assert _update_ids(60, asked) == {}
    before = {"t-kaiser": {"attempts": 2, "last_chased": "2026-04-20"}}
    assert list(_update_ids(60, before)) == ["records_refresh:t-kaiser"]


def test_the_chaser_can_run_the_update_step():
    """The catalog offers an update the chaser's own procedure names, with the
    same params, so a "yes" routes to a step the routine knows how to run."""
    text = (_DIR.parent / "medical-records-chaser" / "SKILL.md").read_text()
    assert "## Updated-records request (a routed step)" in text
    for token in ("`mode: update`", "`newest_record`", "`roster_task_id`", "`prepares`", "`handles`"):
        assert token in text, token


def test_a_failed_read_offers_nothing_on_that_part():
    status = _status()
    status["unread"] = ["files"]
    built = catalog.build_catalog(status, STEPS, {}, cadence_days=7, today=TODAY)
    assert [e["catalog_id"] for e in built] == ["records_refresh:t-reyes"]


def test_the_envelope_catalog_matches_the_ledger_step_shape():
    built = catalog.build_catalog(_status(), STEPS, {}, cadence_days=7, today=TODAY)
    for entry in catalog.envelope_catalog(built):
        assert set(entry) == {"catalog_id", "skill", "level", "params"}
        casework._validate_step(entry)  # the broker's own check accepts it
        # The job rides in params, which the ledger carries whole into
        # reply_verdicts' steps_to_run: the reply turn reads it there.
        assert entry["params"]["job"] in catalog.JOB_SKILLS
        assert catalog.JOB_SKILLS[entry["params"]["job"]] == entry["skill"]


# ---------------------------------------------------------------------------
# jobs: a yes runs exactly the job it approved (2026-09-28: a yes to finalize
# the exhibit list ran the binder, because both share one routine)
# ---------------------------------------------------------------------------


def test_every_step_names_a_closed_job_in_its_own_routine():
    assert set(catalog.STEP_JOBS) | {"records_refresh"} == set(catalog.STEP_SKILLS)
    assert set(catalog.RECORDS_JOBS) == {"chase", "update"}
    jobs = list(catalog.STEP_JOBS.values()) + list(catalog.RECORDS_JOBS.values())
    assert len(jobs) == len(set(jobs)) == len(catalog.JOB_SKILLS)
    for job in jobs:
        assert job.replace("_", "").isalpha() and job.islower(), job


def test_steps_sharing_a_routine_name_different_jobs():
    built = catalog.build_catalog(
        {"files": [{"file_id": "f-el", "name": "Exhibit List (draft).pdf", "date": "2026-09-01"}]},
        {"binder_assemble": "prepares", "exhibit_list_finalize": "prepares"},
        {},
        cadence_days=7,
        today=TODAY,
    )
    by_id = {e["catalog_id"]: e for e in built}
    assert by_id["binder_assemble"]["skill"] == by_id["exhibit_list_finalize"]["skill"]
    assert by_id["binder_assemble"]["params"]["job"] == "assemble_binder"
    assert by_id["exhibit_list_finalize"]["params"] == {"job": "finalize_exhibit_list", "file_id": "f-el"}


def _job_sections(skill: str) -> list[str]:
    text = (_DIR.parent / skill / "SKILL.md").read_text()
    return [line for line in text.splitlines() if line.startswith("### Job: ")]


def test_every_job_has_its_own_section_in_its_routine():
    """The router runs the ``### Job: `<job>``` section the step names. A job
    with no section, or two, would leave the turn to guess."""
    for job, skill in catalog.JOB_SKILLS.items():
        sections = _job_sections(skill)
        assert sections.count("### Job: `" + job + "`") == 1, (skill, job)
    # And no routine carries a section for a job the catalog cannot name.
    for skill in set(catalog.JOB_SKILLS.values()):
        for heading in _job_sections(skill):
            job = heading[len("### Job: `") : -1]
            assert catalog.JOB_SKILLS.get(job) == skill, (skill, heading)


def test_the_router_runs_exactly_the_named_job_and_carries_the_answer_in():
    text = (_DIR.parent / "matter-inbox-router" / "SKILL.md").read_text()
    rule = next(line for line in text.splitlines() if "When it returns `steps_to_run`" in line)
    for token in (
        "`params.job`",
        "run **exactly the job the entry names and nothing else**",
        "`### Job: <job>`",
        "run nothing and say so plainly",
        "the yes IS that confirmation",
        "never asks for it again",
        "never restates the approval",
        "It starts with what was done and where it is in the matter",
    ):
        assert token in rule, token
    # Code's confirmation already names the approved item ("Got it. I'll
    # prepare 1 (...)"); a report that leads with the approval again gives the
    # reply two leads (live, 2026-09-29). The old lead rule is gone everywhere.
    assert "leads with what was approved" not in rule
    reference = (_DIR / "references" / "decision-catalog.md").read_text()
    for job in catalog.JOB_SKILLS:
        assert "`" + job + "`" in reference, job
    assert "The yes IS that confirmation" in reference
    assert "never restates the approval" in reference
    for skill in set(catalog.JOB_SKILLS.values()):
        text = " ".join((_DIR.parent / skill / "SKILL.md").read_text().split())
        assert "leads with what was approved" not in text, skill
        assert "never restates the approval" in text, skill


def _job_section(skill: str, job: str) -> str:
    text = (_DIR.parent / skill / "SKILL.md").read_text()
    start = text.index("### Job: `" + job + "`")
    end = text.find("\n### ", start + 1)
    nxt = text.find("\n## ", start + 1)
    ends = [e for e in (end, nxt) if e != -1]
    return text[start : min(ends)] if ends else text[start:]


def test_every_finalize_job_renders_a_word_document_never_a_text_file():
    """Live 2026-09-29: the finalized witness list was filed as a caption-less
    .txt through add_file. A final list is a Word document rendered through
    render_docx_draft, carrying the draft's caption and a signature marker."""
    finalize = [job for job in catalog.JOB_SKILLS if job.startswith("finalize_")]
    assert sorted(finalize) == ["finalize_exhibit_list", "finalize_witness_list"]
    for job in finalize:
        section = _job_section(catalog.JOB_SKILLS[job], job)
        assert "`mcp_smokeball_render_docx_draft`" in section, job
        assert "Never a `.txt`, never `add_file`" in section, job
        assert "`content_text`" not in section, job
        assert "caption block" in section, job
        assert "{{ATTORNEY: signature block}}" in section, job
        assert "(final)" in section and ".docx" in section, job
    text = " ".join((_DIR.parent / "trial-binder-assembler" / "SKILL.md").read_text().split())
    for token in (
        "**A finalized list is a Word document, rendered, never a text file.**",
        "never with `add_file` / `content_text`",
        "absent means absent",
        "Omit `document_class`",
    ):
        assert token in text, token


# ---------------------------------------------------------------------------
# candidates: one per matter, unbriefed, not woken today, in window
# ---------------------------------------------------------------------------


def _event(event_id, day, matter=M105, number="2026-PI-105"):
    return {"event_id": event_id, "date": day, "subject": "Hearing", "matter_id": matter, "matter_number": number}


def test_candidates_take_the_nearest_unbriefed_date_per_matter():
    events = [
        _event("e-late", "2026-10-05"),
        _event("e-near", "2026-10-02"),
        _event("e-out", "2026-10-30"),
        _event("e-other", "2026-09-30", matter="m-106", number="2026-PI-106"),
    ]
    rows = pre_run.candidates(events, {}, {}, TODAY, 14)
    assert [(r["event_id"], r["days_out"]) for r in rows] == [("e-other", 5), ("e-near", 7)]


def test_a_briefed_date_is_never_briefed_again():
    event = _event("e-near", "2026-10-02")
    key = pre_run.date_item_key(event)
    rows = [
        {
            "event": "briefed",
            "item_key": key,
            "matter_id": M105,
            "kind": "date",
            "source_id": "e-near",
            "n": 1,
            "thread_ref": "t",
            "dispatch_ref": "a" * 32,
            "payload": {"action": "step"},
            "ts": "2026-09-24T15:00:00Z",
        }
    ]
    states = casework.derive_state(rows)
    assert pre_run.candidates([event], states, {}, TODAY, 14) == []


def test_a_date_woken_for_today_waits_for_tomorrow():
    event = _event("e-near", "2026-10-02")
    key = pre_run.date_item_key(event)
    assert pre_run.candidates([event], {}, {key: "2026-09-25"}, TODAY, 14) == []
    assert len(pre_run.candidates([event], {}, {key: "2026-09-24"}, TODAY, 14)) == 1


# ---------------------------------------------------------------------------
# recipients: matter_staff, granted, attorney to, staff cc
# ---------------------------------------------------------------------------

STAFF = {
    "responsible": {"email": "atty@firm.test", "enabled": True},
    "assisting": [
        {"email": "para@firm.test"},
        {"email": "gone@firm.test", "former": True},
        {"email": "x@elsewhere.test"},
    ],
}


def test_recipients_are_the_owner_and_granted_staff():
    got = brief.recipients(pre_run._ROUTING, CFG, STAFF)
    assert got == {"to": ["atty@firm.test"], "cc": ["para@firm.test"], "leg": "matter_staff_responsible"}


def test_no_brief_without_matter_staff_routing_or_a_granted_owner():
    central = {**CFG, "escalation": {"case_alert_routing": {"mode": "central"}}}
    assert brief.recipients(pre_run._ROUTING, central, STAFF) is None
    ungranted = {**STAFF, "responsible": {"email": "atty@elsewhere.test"}}
    assert brief.recipients(pre_run._ROUTING, CFG, ungranted) is None
    assert brief.recipients(pre_run._ROUTING, CFG, None) is None


FALLBACK_CFG = {
    **CFG,
    "escalation": {"case_alert_routing": {"mode": "matter_staff", "fallback_recipients": ["office@firm.test"]}},
}


def test_a_matter_with_no_owner_goes_to_the_authored_fallback_and_says_why():
    no_owner = {"responsible": None, "assisting": []}
    got = brief.recipients(pre_run._ROUTING, FALLBACK_CFG, no_owner)
    assert got == {"to": ["office@firm.test"], "cc": [], "leg": "fallback", "why": "no_owner_in_record"}
    off_roster = {**STAFF, "responsible": {"email": "atty@elsewhere.test"}}
    assert brief.recipients(pre_run._ROUTING, FALLBACK_CFG, off_roster)["why"] == "owner_not_on_roster"
    assert brief.recipients(pre_run._ROUTING, FALLBACK_CFG, None)["why"] == "staff_unread"
    # A routable owner always beats the fallback.
    assert brief.recipients(pre_run._ROUTING, FALLBACK_CFG, STAFF)["leg"] == "matter_staff_responsible"


def test_the_fallback_is_roster_granted_and_matter_staff_only():
    no_owner = {"responsible": None, "assisting": []}
    ungranted = {
        **CFG,
        "escalation": {"case_alert_routing": {"mode": "matter_staff", "fallback_recipients": ["x@elsewhere.test"]}},
    }
    assert brief.recipients(pre_run._ROUTING, ungranted, no_owner) is None
    central = {
        **CFG,
        "escalation": {"case_alert_routing": {"mode": "central", "fallback_recipients": ["office@firm.test"]}},
    }
    assert brief.recipients(pre_run._ROUTING, central, no_owner) is None


# ---------------------------------------------------------------------------
# The gate end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SMD_CASEWORK_LEDGER_PATH", str(tmp_path / "casework.jsonl"))
    monkeypatch.setenv("SMD_ESCALATION_LEDGER_PATH", str(tmp_path / "escalation.jsonl"))
    beats: list = []
    monkeypatch.setattr(pre_run, "heartbeat", lambda verb, action, basis: beats.append((action, basis)) or True)
    monkeypatch.setattr(pre_run._ROUTING, "pull_matter_staff", lambda ids, budget: {M105: STAFF})
    client = _status_client()
    events = {"events": [_event("e-fsc", "2026-10-02")]}

    def pull(query):
        if query["op"] == "dates":
            return events
        return {"status": file_status.pull_matter_status(client, query["matter_id"])}

    monkeypatch.setattr(pre_run, "connector_pull", pull)
    return tmp_path, beats


def _run(cfg):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = pre_run.run(cfg, TODAY)
    assert rc == 0
    return json.loads(out.getvalue().strip().splitlines()[-1])


def test_no_case_manager_block_means_the_job_is_off(seat):
    _, beats = seat
    assert _run({"escalation": CFG["escalation"]}) == {"wakeAgent": False}
    assert beats == [("SUPPRESSED_WAKE", "case_manager_unauthored:date_prep_off")]


def test_a_date_in_window_wakes_with_facts_and_writes_the_envelope(seat):
    home, beats = seat
    line = _run(CFG)
    assert line["wakeAgent"] is True
    facts = line["date_prep"]
    assert facts["event"] == {"event_id": "e-fsc", "date": "2026-10-02", "subject": "Hearing", "days_out": 7}
    assert all(e["params"]["job"] for e in facts["catalog"])
    assert "atty@firm.test" not in json.dumps(facts)  # addresses stay with the tool
    envelope = json.loads((home / ".smd" / "pre_run" / "date-prep-brief.brief.json").read_text())
    assert envelope["recipients"] == ["atty@firm.test"] and envelope["cc"] == ["para@firm.test"]
    assert envelope["subject_label"] == "2026-PI-105: Hearing, Oct 2"
    assert envelope["item_key"] == pre_run.date_item_key(_event("e-fsc", "2026-10-02"))
    assert [e["catalog_id"] for e in envelope["catalog"]][0] == "binder_assemble"
    handoff = json.loads((home / ".smd" / "pre_run" / "date-prep-brief.json").read_text())
    assert "2026-10-02" in handoff["dates"] and handoff["records"][0]["matterNumber"] == "2026-PI-105"
    assert (home / ".smd" / "pre_run" / "date-prep-brief.brief.json").stat().st_mode & 0o777 == 0o600
    assert beats[-1] == ("EMITTED_WAKE", "date_in_window:brief_prepared")
    # The next hourly tick the same day does not wake for the same date.
    assert _run(CFG) == {"wakeAgent": False}
    assert beats[-1] == ("SUPPRESSED_WAKE", "no_unbriefed_date_in_window")


def test_a_failed_date_pull_suppresses_with_its_reason(seat, monkeypatch):
    _, beats = seat
    monkeypatch.setattr(pre_run, "connector_pull", lambda query: None)
    assert _run(CFG) == {"wakeAgent": False}
    assert beats == [("SUPPRESSED_WAKE", "degraded:date_pull_failed")]


def test_nothing_to_offer_sends_nothing(seat):
    _, beats = seat
    cfg = {
        **CFG,
        "case_manager": {
            "date_prep": {"level": "handles", "window_days": 14, "steps": {"motion_calendar_refresh": "surfaces"}}
        },
    }
    assert _run(cfg) == {"wakeAgent": False}
    assert beats == [("SUPPRESSED_WAKE", "case_manager:no_step_offered")]


def test_an_unowned_matter_is_briefed_to_the_fallback(seat, monkeypatch):
    home, beats = seat
    monkeypatch.setattr(pre_run._ROUTING, "pull_matter_staff", lambda ids, budget: {M105: {"responsible": None}})
    line = _run(FALLBACK_CFG)
    assert line["wakeAgent"] is True
    assert line["date_prep"]["recipients"] == {"to": "firm fallback contact", "cc": 0, "why": "no_owner_in_record"}
    envelope = json.loads((home / ".smd" / "pre_run" / "date-prep-brief.brief.json").read_text())
    assert envelope["recipients"] == ["office@firm.test"] and envelope["routing_leg"] == "fallback"
    assert envelope["routing_why"] == "no_owner_in_record"


def test_an_owned_matter_carries_no_routing_note(seat):
    home, _ = seat
    assert _run(FALLBACK_CFG)["wakeAgent"] is True
    envelope = json.loads((home / ".smd" / "pre_run" / "date-prep-brief.brief.json").read_text())
    assert "routing_why" not in envelope and envelope["routing_leg"] == "matter_staff"


def test_a_skipped_date_names_the_gate_that_held_it(seat, monkeypatch):
    _, beats = seat
    monkeypatch.setattr(pre_run._ROUTING, "pull_matter_staff", lambda ids, budget: {M105: {"responsible": None}})
    assert _run(CFG) == {"wakeAgent": False}
    assert beats == [("SUPPRESSED_WAKE", "no_briefable_date:unroutable")]


def test_the_pilot_config_arms_the_job():
    import yaml

    cfg = yaml.safe_load((_DIR.parents[1] / "customers" / "pilot-smokeball" / "customer.yaml").read_text())
    prep = pre_run.date_prep_config(cfg)
    assert prep is not None and prep["window_days"] >= 7
    ap = yaml.safe_load((_DIR.parents[1] / "customers" / "ashton-price" / "customer.yaml").read_text())
    assert pre_run.date_prep_config(ap) is None


# ---------------------------------------------------------------------------
# Done lines: a step the turn runs itself, and work from earlier runs
# ---------------------------------------------------------------------------


def _step_ran_row(event_id="e-earlier"):
    step = {
        "catalog_id": "records_refresh:t-reyes",
        "skill": "medical-records-chaser",
        "level": "handles",
        "params": {"provider": "Valley Imaging", "mode": "update", "newest_record": "2026-06-20"},
    }
    return {
        "item_key": casework.item_key(matter_id=M105, kind="date", source_id=event_id),
        "matter_id": M105,
        "kind": "date",
        "source_id": event_id,
        "event": "step_ran",
        "payload": {"action": "step", "class": "open", "step": step},
        "tool_call_id": "call-memo",
        "ts": "2026-09-24T15:05:00Z",
    }


def test_a_handles_step_carries_its_done_line(seat):
    home, _ = seat
    _run(CFG)
    envelope = json.loads((home / ".smd" / "pre_run" / "date-prep-brief.brief.json").read_text())
    by_id = {e["catalog_id"]: e for e in envelope["catalog"]}
    assert by_id["binder_assemble"]["done_line"] == "I assembled the trial binder index"
    assert all("done_line" not in e for e in envelope["catalog"] if e["level"] != "handles")
    assert "done_since" not in envelope, "no quiet authored, no earlier work told"


def test_quiet_brings_this_matters_untold_work_into_the_brief(seat):
    home, _ = seat
    (home / "casework.jsonl").write_text(json.dumps(_step_ran_row()) + "\n", encoding="utf-8")
    cfg = {**CFG, "case_manager": {**CFG["case_manager"], "quiet": {"level": "surfaces"}}}
    _run(cfg)
    envelope = json.loads((home / ".smd" / "pre_run" / "date-prep-brief.brief.json").read_text())
    assert envelope["done_since"] == [
        {
            "item_key": _step_ran_row()["item_key"],
            "matter_id": M105,
            "kind": "date",
            "source_id": "e-earlier",
            "line": "matter 2026-PI-105: on 2026-09-24 I asked Valley Imaging for records dated after 2026-06-20",
        }
    ]
    handoff = json.loads((home / ".smd" / "pre_run" / "date-prep-brief.json").read_text())
    for day in ("2026-09-24", "2026-06-20"):
        assert day in handoff["dates"] and day in handoff["records"][0]["dates"]
