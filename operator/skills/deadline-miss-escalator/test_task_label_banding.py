"""Task labels, per-recipient banding and honest counts (2026-09-24 pilot review).

Four defects the Captain read in one morning's pilot emails, each pinned here by
a test that fails against the code that shipped them:

* a deadline line named the matter and the date but not the task;
* a recipient whose deadlines ranked sixth or lower seat-wide was told
  "0 need you", with their overdue deadlines filed as "routine confirmations";
* a recipient holding only blanket-ack items was also told "0 need you";
* "Ranked by what the record says" sat over items the record did not rank.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent


def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, SKILL_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


items_mod = _load("digest_items.py", "escalator_digest_items_under_test")
render = _load("render.py", "escalator_render_for_labels")
envelope = _load("dispatch_envelope.py", "escalator_envelope_for_labels")
ledger = _load("escalation_ledger.py", "escalator_ledger_for_labels")
pre_run = _load("pre_run.py", "escalator_pre_run_for_labels")

TODAY = date(2026, 9, 22)

CAPTION_SUBJECT = "Follow up 7/15 re $1,200 lien 2026-PI-999 *urgent* Smith v. Jones"
HOSTILE_SUBJECT = "Pay $1,200 by 7/15/2026 on PI-2026-0009 *now*"


# ---------------------------------------------------------------------------
# display_label
# ---------------------------------------------------------------------------


def test_a_caption_refuses_the_whole_label():
    assert items_mod.display_label(CAPTION_SUBJECT) is None
    assert items_mod.display_label("Depo prep, Smith vs Jones") is None
    assert items_mod.display_label("Order in re Estate of Doe") is None


def test_a_hostile_subject_is_masked_and_neutralized():
    label = items_mod.display_label(HOSTILE_SUBJECT)
    assert label == "Pay \u2026 by \u2026 on \u2026 now"
    for leaked in ("$", "1,200", "7/15", "2026", "0009", "*"):
        assert leaked not in label


def test_each_gate_shape_is_masked():
    cases = {
        "Call re 2026-08-01 hearing": "2026",
        "Call re August 5, 2026 hearing": "August 5",
        "Send letter re 4455667": "4455667",
        "Pay 400 dollars to clinic": "400",
        "Case 1:24-cv-01234 status": "01234",
        "Check A123456789 status": "123456789",
    }
    for subject, leaked in cases.items():
        label = items_mod.display_label(subject)
        assert label is not None and leaked not in label, (subject, label)


def test_markers_and_citations_refuse_the_label():
    for subject in (
        "Guarantee the payoff",
        "Finish by end of month",
        "Response per Fed. R. Civ. P. 36",
        "Review 925 F.3d 1339",
        "A.R.S. \u00a7 12-821 notice",
    ):
        assert items_mod.display_label(subject) is None, subject


def test_label_strips_provenance_collapses_space_and_caps_length():
    assert items_mod.display_label("[Operator]   Call  client re  records") == "Call client re records"
    long = items_mod.display_label("word " * 60)
    assert long is not None and len(long) == items_mod.LABEL_MAX_CHARS and long.endswith("\u2026")
    assert items_mod.display_label("   ") is None
    assert items_mod.display_label(None) is None


def test_label_neutralizes_markdown_and_quotes():
    label = items_mod.display_label('Draft [reply] _now_ #1 `x` "quoted" **bold**')
    assert label is not None
    for ch in '*_`#[]"':
        assert ch not in label, (ch, label)
    assert "\u2014" not in (items_mod.display_label("Call client \u2014 today") or "")


# ---------------------------------------------------------------------------
# parse -> digest -> render
# ---------------------------------------------------------------------------


def _pull(task_subject: str, event_title: str = "Smith v. Jones status conference") -> dict:
    return {
        "tasks": {
            "value": [
                {
                    "id": "t-1",
                    "matterId": "m-1",
                    "subject": task_subject,
                    "dueDate": "2026-09-20",
                    "matterNumber": "2026-PI-101",
                }
            ]
        },
        "events": {
            "value": [
                {
                    "id": "ev-1",
                    "matterId": "m-2",
                    "subject": event_title,
                    "startTime": "2026-09-25T10:00:00Z",
                    "matterNumber": "2026-PI-102",
                }
            ]
        },
    }


def _parsed_digest(task_subject: str, event_title: str = "Status conference") -> dict:
    deadlines, problem, _stats = pre_run.parse_pull(_pull(task_subject, event_title))
    assert problem is None
    return pre_run.project_digest(deadlines, pre_run.EscalationWindows(), ledger, today=TODAY)


def test_a_task_line_names_the_task_masked():
    digest = _parsed_digest(HOSTILE_SUBJECT)
    body = render.render_digest(digest, ack_snooze_days=7)
    assert (
        '1. matter 2026-PI-101, "Pay \u2026 by \u2026 on \u2026 now", task-deadline 2026-09-20 (overdue by 2 days)'
        in body
    )


def test_an_event_title_never_renders():
    digest = _parsed_digest("Call client", event_title="Status conference with Judge Alvarez")
    body = render.render_digest(digest, ack_snooze_days=7)
    assert "Status conference" not in body
    assert "Alvarez" not in body
    assert "matter 2026-PI-102, court-date 2026-09-25" in body
    # Parse builds no label for an event at all (the first of two guards).
    event = next(i for i in digest["needs_you"] if i["label"] == "court-date")
    assert event["subject_display"] is None
    # Even a digest item that somehow carried a label for an event renders none.
    forged = dict(digest["needs_you"][0], label="court-date", subject_display="Status conference")
    assert "Status conference" not in render._item_line(forged)


def test_the_raw_subject_never_reaches_the_digest():
    digest = _parsed_digest(HOSTILE_SUBJECT)
    blob = json.dumps(digest)
    for leaked in ("$1,200", "7/15/2026", "PI-2026-0009", "*now*"):
        assert leaked not in blob


def test_a_caption_task_renders_without_a_label():
    digest = _parsed_digest(CAPTION_SUBJECT)
    body = render.render_digest(digest, ack_snooze_days=7)
    assert "1. matter 2026-PI-101, task-deadline 2026-09-20 (overdue by 2 days)" in body
    assert "Smith" not in body and "Jones" not in body
    first = next(line for line in body.split("\n") if line.startswith("1. "))
    assert '"' not in first


def test_blanket_lines_carry_the_label_too():
    body = render.render_digest(
        {
            "needs_you": [],
            "blanket_ack_only": [
                {
                    "matter_id": "m-1",
                    "matter_number": "2026-PI-101",
                    "label": "task-deadline",
                    "authored_date": "2026-09-20",
                    "days_out": -2,
                    "subject_display": "Call client",
                }
            ],
        },
        ack_snooze_days=7,
    )
    assert '- matter 2026-PI-101, "Call client", task-deadline 2026-09-20 (overdue by 2 days).' in body


# ---------------------------------------------------------------------------
# Per-recipient banding: the 2026-09-22 shape
# ---------------------------------------------------------------------------


def _dl(matter_id: str, task_id: str, days_out: int, number: str) -> object:
    return pre_run.MatterDeadline(
        matter_id=matter_id,
        authored_date=date.fromordinal(TODAY.toordinal() + days_out),
        label="task-deadline",
        task_id=task_id,
        matter_number=number,
    )


def _yaml(tmp_path) -> str:
    path = tmp_path / "customer.yaml"
    path.write_text(
        "escalation:\n"
        "  red_flag_recipients:\n    - ops@firm.example\n"
        "  case_alert_routing:\n"
        "    mode: matter_staff\n"
        "    fallback_recipients:\n      - fallback@firm.example\n"
        "scope:\n  inbound_allow_from:\n    - '@firm.example'\n"
    )
    return str(path)


def test_a_recipient_whose_items_rank_sixth_seat_wide_still_gets_them_as_needs_you(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Amy's matter holds the five most overdue items on the seat; Bob's two
    # items rank sixth and seventh. Seat-wide, Bob's are all overflow.
    deadlines = [_dl("m-amy", f"t-a{i}", -60 + i, "2026-PI-101") for i in range(5)] + [
        _dl("m-bob", "t-b1", -3, "2026-PI-102"),
        _dl("m-bob", "t-b2", -2, "2026-PI-102"),
    ]
    digest = pre_run.project_digest(deadlines, pre_run.EscalationWindows(), ledger, today=TODAY)
    assert all(i["matter_id"] == "m-amy" for i in digest["needs_you"])  # the seat-wide premise
    staff = {
        "m-amy": {"responsible": {"email": "amy@firm.example", "enabled": True}, "assisting": []},
        "m-bob": {"responsible": {"email": "bob@firm.example", "enabled": True}, "assisting": []},
    }
    meta = envelope.build_and_write(
        digest=digest,
        deadlines=deadlines,
        states={},
        ledger=ledger,
        today=TODAY,
        ack_snooze_days=7,
        customer_yaml_path=_yaml(tmp_path),
        staff_pull=lambda ids, budget: staff,
    )
    assert meta["dispatch_count"] == 2
    written = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json").read_text())
    by_who = {d["recipients"][0]: d for d in written["dispatches"]}
    bob = by_who["bob@firm.example"]
    assert bob["subject"] == "[Deadlines] 2 need you, 2026-09-22"
    assert "## Needs you today (2)" in bob["full_body"]
    assert "## Also open" not in bob["full_body"]
    assert "t-b1" not in bob["full_body"]  # task ids never render
    # Nothing is lost or duplicated: every firing item still appends once.
    appended = [a["item_key"] for d in written["dispatches"] for a in d["appends"]]
    assert len(appended) == len(set(appended)) == 7


def test_rebalance_keeps_the_top_five_and_recounts_the_rest():
    sub = {
        "needs_you": [],
        "admin_confirms": items_mod.group_by_matter(
            [
                {"matter_id": "m-1", "task_id": f"t-{i}", "days_out": -i, "ack_code": f"ACK-{i}", "last_raised": None}
                for i in range(7)
            ]
        ),
    }
    items_mod.rebalance_bands(sub)
    assert [i["task_id"] for i in sub["needs_you"]] == ["t-6", "t-5", "t-4", "t-3", "t-2"]
    assert sub["admin_confirms"]["total"] == 2
    assert sub["admin_confirms"]["matter_count"] == 1
    assert sub["admin_confirms"]["matters"][0]["ack_codes"] == ["ACK-1", "ACK-0"]


def test_a_blanket_only_recipient_never_reads_zero():
    deadlines = [
        pre_run.MatterDeadline(
            matter_id="m-1",
            authored_date=date(2026, 9, 20),
            label="task-deadline",
            task_id=None,  # no stable id -> blanket-ack only
            matter_number="2026-PI-101",
        )
    ]
    digest = pre_run.project_digest(deadlines, pre_run.EscalationWindows(), ledger, today=TODAY)
    assert digest["needs_you"] == []
    assert digest["subject"] == "[Deadlines] 1 need you, 2026-09-22"
    sub = envelope.split_digest(digest, {"m-1"}, "2026-09-22")
    assert sub["subject"] == "[Deadlines] 1 need you, 2026-09-22"


# ---------------------------------------------------------------------------
# Headings and preamble
# ---------------------------------------------------------------------------


def _ny(days_out: int, date_iso: str, marker=None) -> dict:
    return {
        "matter_id": "m-1",
        "matter_number": "2026-PI-101",
        "label": "task-deadline",
        "authored_date": date_iso,
        "days_out": days_out,
        "ack_code": "ACK-AAAAAA",
        "priority_marker": marker,
    }


def test_preamble_is_omitted_for_indistinguishable_items():
    body = render.render_digest({"needs_you": [_ny(-2, "2026-09-20"), _ny(-2, "2026-09-20")]}, ack_snooze_days=7)
    assert "Ranked by" not in body
    assert "Most overdue first." not in body


def test_preamble_says_what_the_order_actually_is():
    by_date = render.render_digest({"needs_you": [_ny(-5, "2026-09-17"), _ny(-2, "2026-09-20")]}, ack_snooze_days=7)
    assert "Most overdue first." in by_date and "Ranked by" not in by_date
    marked = render.render_digest(
        {"needs_you": [_ny(-2, "2026-09-20", marker="URGENT"), _ny(-5, "2026-09-17")]}, ack_snooze_days=7
    )
    assert "Ranked by what the record says, most consequential first." in marked


def test_one_matter_is_singular_in_the_overflow_heading():
    band = {"total": 2, "matter_count": 1, "matters": [{"matter_number": "2026-PI-101", "count": 2, "ack_codes": []}]}
    body = render.render_digest({"needs_you": [], "admin_confirms": band}, ack_snooze_days=7)
    assert "## Also open (2 across 1 matter)" in body
    assert not re.search(r"across 1 matters", body)


def test_nothing_rendered_calls_an_item_routine():
    band = {"total": 1, "matter_count": 1, "matters": [{"matter_number": "2026-PI-101", "count": 1, "ack_codes": []}]}
    digest = {"needs_you": [_ny(-2, "2026-09-20")], "admin_confirms": band}
    for body in (render.render_digest(digest, ack_snooze_days=7), render.render_skeleton(digest)):
        assert "routine" not in body.lower()
