"""Tests for the escalator's deterministic renderer + routing + envelope
(WS-RENDER). Same import style as ``test_escalator_pre_run.py``: path-loaded
modules so the suite runs from any cwd."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
OPERATOR_DIR = SKILL_DIR.parents[1]


def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, SKILL_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


render = _load("render.py", "escalator_render_under_test")
routing = _load("routing.py", "escalator_routing_under_test")
envelope = _load("dispatch_envelope.py", "escalator_envelope_under_test")
ledger = _load("escalation_ledger.py", "escalator_ledger_under_test")


# ---------------------------------------------------------------------------
# canonical_body_sha256 — pinned to the shared arbiter fixture.
# ---------------------------------------------------------------------------


def test_canonical_hash_matches_arbiter_vectors():
    vectors = json.loads((OPERATOR_DIR / "contracts" / "fixtures" / "body-canon-vectors.json").read_text())["vectors"]
    assert vectors, "arbiter fixture is empty"
    names = {v["name"] for v in vectors}
    assert "trailing_newline" in names  # the REQUIRED vector
    for vector in vectors:
        assert render.canonical_body_sha256(vector["input"]) == vector["sha256"], vector["name"]


# ---------------------------------------------------------------------------
# render_digest
# ---------------------------------------------------------------------------


def _item(
    matter="2026-PI-101",
    label="task-deadline",
    days=-2,
    code="ACK-AAAAAA",
    marker=None,
    absent=None,
    task_id="t-1",
):
    return {
        "matter_id": "m-" + (matter or "absent"),
        "matter_number": matter,
        "matter_number_absent": absent,
        "task_id": task_id,
        "label": label,
        "authored_date": "2026-08-29",
        "days_out": days,
        "ack_code": code,
        "last_raised": None,
        "priority_marker": marker,
    }


def _digest(**overrides):
    base = {
        "subject": "[Deadlines] 1 need you, 2026-08-31",
        "needs_you": [_item()],
    }
    base.update(overrides)
    return base


def test_render_digest_carries_template_markup_and_values():
    body = render.render_digest(_digest(), ack_snooze_days=7)
    assert "## Needs you today (1)" in body
    assert "1. matter 2026-PI-101, task-deadline 2026-08-29 (overdue by 2 days) [ACK-AAAAAA]" in body
    assert "ESCALATION_ACKNOWLEDGED" in body
    assert "goes quiet for 7 days" in body
    assert "no client message has been sent" in body
    # No em dashes anywhere (law-seat first-draft rule).
    assert "—" not in body


def test_conditional_sections_omitted_whole():
    body = render.render_digest(_digest(), ack_snooze_days=7)
    for heading in (
        "## Admin confirms",
        "## Under active escalation elsewhere",
        "## Awaiting clearance",
        "## Blanket-ack only",
    ):
        assert heading not in body


def test_admin_and_elsewhere_render_grouped_lines():
    digest = _digest(
        admin_confirms={
            "total": 3,
            "matter_count": 2,
            "matters": [
                {
                    "matter_id": "m-1",
                    "matter_number": "2026-PI-102",
                    "matter_number_absent": None,
                    "count": 2,
                    "ack_codes": ["ACK-BBBBBB", "ACK-CCCCCC"],
                    "last_raised": None,
                    "items": [],
                },
                {
                    "matter_id": "m-2",
                    "matter_number": None,
                    "matter_number_absent": "no_number_on_record",
                    "count": 1,
                    "ack_codes": ["ACK-DDDDDD"],
                    "last_raised": None,
                    "items": [],
                },
            ],
        },
        under_active_escalation_elsewhere={
            "total": 2,
            "matter_count": 1,
            "matters": [
                {
                    "matter_id": "m-3",
                    "matter_number": "2026-PI-103",
                    "matter_number_absent": None,
                    "count": 2,
                    "ack_codes": [],
                    "last_raised": "2026-08-28T14:00:00Z",
                    "items": [],
                }
            ],
        },
    )
    body = render.render_digest(digest, ack_snooze_days=7)
    assert "## Admin confirms (3 across 2 matters)" in body
    assert "- matter 2026-PI-102: 2 routine confirmations. [ACK-BBBBBB] [ACK-CCCCCC]" in body
    assert "- no number on record: 1 routine confirmation. [ACK-DDDDDD]" in body
    assert "## Under active escalation elsewhere (2 across 1 matters)" in body
    assert "- matter 2026-PI-103: 2 items under active escalation (last raised 2026-08-28)." in body


def test_matter_number_absences_render_exact_phrases_never_guid():
    digest = _digest(
        needs_you=[
            _item(matter=None, absent="no_number_on_record", code="ACK-EEEEEE"),
            _item(matter=None, absent="lookup_failed", code="ACK-FFFFFF", task_id="t-2"),
        ]
    )
    digest["needs_you"][0]["matter_number"] = None
    digest["needs_you"][1]["matter_number"] = None
    body = render.render_digest(digest, ack_snooze_days=7)
    assert "no number on record, task-deadline" in body
    assert "matter number unavailable, task-deadline" in body
    assert "m-None" not in body


def test_consequence_map_is_closed():
    assert render.consequence_line({"priority_marker": "CRITICAL"}) == ("the task is marked CRITICAL in Smokeball")
    assert render.consequence_line({"label": "court-date"}) == "a court date the firm authored"
    # Unknown signal renders nothing — never a sentinel, never invented urgency.
    assert render.consequence_line({"label": "task-deadline"}) is None
    assert render.consequence_line({"priority_marker": "SUPER URGENT!!"}) is None


def test_footer_is_a_sibling_not_a_list_child():
    body = render.render_digest(_digest(), ack_snooze_days=7)
    footer_line = next(line for line in body.split("\n") if line.startswith("Reply with"))
    assert not footer_line.startswith(("-", " ", "1.")), "footer must not nest in a list"


def test_probe_artifacts_footer_line():
    digest = _digest(probe_artifacts={"excluded": 3, "stale": 1, "stale_task_ids": ["t-9"]})
    body = render.render_digest(digest, ack_snooze_days=7)
    assert "Probe artifacts excluded from this digest: 3." in body
    assert "t-9" in body


def test_rekey_notice_on_and_off():
    on = render.render_digest(_digest(), ack_snooze_days=7, rekey_count=2)
    off = render.render_digest(_digest(), ack_snooze_days=7, rekey_count=0)
    assert "Item identity was corrected for 2 calendar items" in on
    assert "Item identity was corrected" not in off


def test_skeleton_is_identifier_free():
    digest = _digest(admin_confirms={"total": 4, "matter_count": 2, "matters": [{}]})
    body = render.render_skeleton(digest)
    assert "1 item need" in body
    assert "4 routine confirmations" in body
    # Zero dates, zero matter numbers, zero ACK codes, zero task ids.
    assert not re.search(r"\d{4}-\d{2}-\d{2}", body)
    assert "ACK-" not in body
    assert "2026-PI" not in body
    assert "no client message has been sent" in body


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------

_YAML_CENTRAL = {
    "escalation": {"red_flag_recipients": ["ops@firm.example"]},
    "scope": {"inbound_allow_from": ["@firm.example"]},
}


def _yaml_matter_staff(fallback=("fallback@firm.example",), grants=("@firm.example",)):
    return {
        "escalation": {
            "red_flag_recipients": ["ops@firm.example"],
            "case_alert_routing": {
                "mode": "matter_staff",
                "fallback_recipients": list(fallback),
            },
        },
        "scope": {"inbound_allow_from": list(grants)},
    }


def test_central_default_routes_everything_to_red_flag():
    result = routing.resolve_case_alert_routing(_YAML_CENTRAL, {}, ["m-1", "m-2"])
    assert result.unroutable == ()
    for matter in ("m-1", "m-2"):
        assert result.routed[matter].emails == ("ops@firm.example",)
        assert result.routed[matter].routing_leg == "central"


def test_central_with_no_red_flag_recipients_is_unroutable():
    result = routing.resolve_case_alert_routing({}, {}, ["m-1"])
    assert result.routed == {}
    assert result.unroutable == ("m-1",)


def test_matter_staff_routes_responsible_attorney():
    staff = {"m-1": {"responsible": {"email": "amy@firm.example", "enabled": True}, "assisting": []}}
    result = routing.resolve_case_alert_routing(_yaml_matter_staff(), staff, ["m-1"])
    assert result.routed["m-1"].emails == ("amy@firm.example",)
    assert result.routed["m-1"].routing_leg == "matter_staff_responsible"


def test_disabled_or_former_staff_take_fallback():
    staff = {
        "m-1": {"responsible": {"email": "amy@firm.example", "enabled": False}, "assisting": []},
        "m-2": {"responsible": {"email": "bob@firm.example", "former": True}, "assisting": []},
    }
    result = routing.resolve_case_alert_routing(_yaml_matter_staff(), staff, ["m-1", "m-2"])
    for matter in ("m-1", "m-2"):
        assert result.routed[matter].emails == ("fallback@firm.example",)
        assert result.routed[matter].routing_leg == "fallback"


def test_roster_hard_rule_ungranted_address_is_unresolvable():
    staff = {"m-1": {"responsible": {"email": "amy@elsewhere.example"}, "assisting": []}}
    result = routing.resolve_case_alert_routing(_yaml_matter_staff(), staff, ["m-1"])
    assert result.routed["m-1"].routing_leg == "fallback"


def test_assisting_leg_fires_only_without_responsible():
    staff = {
        "m-1": {
            "responsible": None,
            "assisting": [{"email": "para@firm.example"}, {"email": "out@nowhere.example"}],
        }
    }
    result = routing.resolve_case_alert_routing(_yaml_matter_staff(), staff, ["m-1"])
    assert result.routed["m-1"].emails == ("para@firm.example",)
    assert result.routed["m-1"].routing_leg == "matter_staff_assisting"


def test_fail_closed_floor_no_fallback_authored():
    staff = {"m-1": {"responsible": None, "assisting": []}}
    result = routing.resolve_case_alert_routing(_yaml_matter_staff(fallback=()), staff, ["m-1"])
    assert "m-1" not in result.routed
    assert result.unroutable == ("m-1",)


def test_unknown_matter_routes_central_and_never_a_staff_leg():
    """The sentinel names no real matter: no staff resolution, no memo target.
    It delivers to the central triage recipients (else fallback)."""
    result = routing.resolve_case_alert_routing(_yaml_matter_staff(), {}, ["unknown-matter"])
    assert result.routed["unknown-matter"].emails == ("ops@firm.example",)
    assert result.routed["unknown-matter"].routing_leg == "central"
    no_red_flag = _yaml_matter_staff()
    no_red_flag["escalation"].pop("red_flag_recipients")
    result = routing.resolve_case_alert_routing(no_red_flag, {}, ["unknown-matter"])
    assert result.routed["unknown-matter"].routing_leg == "fallback"


def test_staff_pull_lives_in_the_vendored_routing_module():
    """Finding 2: one pull, one resolution, shared by both vendoring skills."""
    assert callable(routing.pull_matter_staff)
    assert routing.staff_lookup_budget({}) == routing.DEFAULT_STAFF_LOOKUP_BUDGET
    assert routing.staff_lookup_budget({"escalation": {"staff_lookup_budget": 0}}) == 0
    assert routing.staff_lookup_budget({"escalation": {"staff_lookup_budget": True}}) == (
        routing.DEFAULT_STAFF_LOOKUP_BUDGET
    )


def test_grant_matching_mirrors_classifier_semantics():
    grants = ["@Firm.Example", "solo@other.example"]
    assert routing._granted("Amy@firm.example", grants)
    assert routing._granted("solo@other.example", grants)
    assert not routing._granted("amy@notfirm.example", grants)
    assert not routing._granted("Name <amy@firm.example>", grants)


# ---------------------------------------------------------------------------
# dispatch envelope
# ---------------------------------------------------------------------------


def _mk_deadline(pre_run, matter_id, task_id, matter_number="2026-PI-101"):
    return pre_run.MatterDeadline(
        matter_id=matter_id,
        authored_date=date(2026, 8, 29),
        label="task-deadline",
        task_id=task_id,
        matter_number=matter_number,
    )


def test_build_and_write_envelope_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH", raising=False)
    pre_run = _load("pre_run.py", "escalator_pre_run_for_envelope")
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "escalation:\n  red_flag_recipients:\n    - ops@firm.example\n"
        "scope:\n  inbound_allow_from:\n    - '@firm.example'\n"
    )
    deadlines = [_mk_deadline(pre_run, "m-1", "t-1")]
    today = date(2026, 8, 31)
    digest = pre_run.project_digest(deadlines, pre_run.EscalationWindows(), ledger, today=today)
    meta = envelope.build_and_write(
        digest=digest,
        deadlines=deadlines,
        states={},
        ledger=ledger,
        today=today,
        ack_snooze_days=7,
        customer_yaml_path=str(yaml_path),
        staff_pull=lambda ids, budget: {},
    )
    assert meta["dispatch_expected"] is True
    assert meta["render_mode"] == "templated"
    assert meta["dispatch_count"] == 1
    written = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json").read_text())
    assert written["skill"] == "deadline-miss-escalator"
    assert written["render_mode"] == "templated"
    [dispatch] = written["dispatches"]
    assert dispatch["recipients"] == ["ops@firm.example"]
    assert dispatch["routing_leg"] == "central"
    # The stamps ARE the canonical hash of the bodies (the hash-join contract).
    assert dispatch["body_sha256_full"] == render.canonical_body_sha256(dispatch["full_body"])
    assert dispatch["body_sha256_skeleton"] == render.canonical_body_sha256(dispatch["skeleton_body"])
    assert meta["body_sha256"] == [
        {
            "body_sha256_full": dispatch["body_sha256_full"],
            "body_sha256_skeleton": dispatch["body_sha256_skeleton"],
        }
    ]
    # One fired append for the one firing item, keyed by the vendored ledger.
    [append] = dispatch["appends"]
    expected_key = ledger.item_key("m-1", "t-1", "task-deadline", "2026-08-29")
    assert append["item_key"] == expected_key
    assert append["event"] == "fired"
    assert append["attempt"] == 1
    assert append["token"] == ledger.token_for(expected_key)
    assert meta["items"] == [{"item_key": expected_key, "ack_code": append["token"]}]
    # The wake line's failure-note template rides for the in-turn check.
    assert written["in_turn"][0]["template"] == render.FAILURE_NOTE


def test_matter_staff_split_one_dispatch_per_recipient_set(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pre_run = _load("pre_run.py", "escalator_pre_run_for_split")
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "escalation:\n"
        "  red_flag_recipients:\n    - ops@firm.example\n"
        "  case_alert_routing:\n"
        "    mode: matter_staff\n"
        "    fallback_recipients:\n      - fallback@firm.example\n"
        "scope:\n  inbound_allow_from:\n    - '@firm.example'\n"
    )
    deadlines = [
        _mk_deadline(pre_run, "m-1", "t-1", "2026-PI-101"),
        _mk_deadline(pre_run, "m-2", "t-2", "2026-PI-102"),
    ]
    today = date(2026, 8, 31)
    digest = pre_run.project_digest(deadlines, pre_run.EscalationWindows(), ledger, today=today)
    staff = {"m-1": {"responsible": {"email": "amy@firm.example", "enabled": True}, "assisting": []}}
    meta = envelope.build_and_write(
        digest=digest,
        deadlines=deadlines,
        states={},
        ledger=ledger,
        today=today,
        ack_snooze_days=7,
        customer_yaml_path=str(yaml_path),
        staff_pull=lambda ids, budget: staff,
    )
    assert meta["dispatch_count"] == 2
    written = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json").read_text())
    by_leg = {d["routing_leg"]: d for d in written["dispatches"]}
    assert by_leg["matter_staff_responsible"]["recipients"] == ["amy@firm.example"]
    assert "2026-PI-101" in by_leg["matter_staff_responsible"]["full_body"]
    assert "2026-PI-102" not in by_leg["matter_staff_responsible"]["full_body"]
    assert by_leg["fallback"]["recipients"] == ["fallback@firm.example"]
    assert "2026-PI-102" in by_leg["fallback"]["full_body"]
    # A fallback-delivered matter is a memo duty for the woken turn.
    assert written["memo_matters"] == ["m-2"]
    # Each subject counts ONLY its own needs-you band (Law 11).
    assert by_leg["fallback"]["subject"].startswith("[Deadlines] 1 need you")


def test_unknown_matter_never_reaches_memo_or_unroutable_lists(tmp_path, monkeypatch):
    """Finding 5: the sentinel's items still ship (central dispatch), but no
    memo duty and no unroutable row may name a matter nobody can open."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pre_run = _load("pre_run.py", "escalator_pre_run_for_sentinel")
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "escalation:\n"
        "  red_flag_recipients:\n    - ops@firm.example\n"
        "  case_alert_routing:\n"
        "    mode: matter_staff\n"
        "    fallback_recipients:\n      - fallback@firm.example\n"
        "scope:\n  inbound_allow_from:\n    - '@firm.example'\n"
    )
    deadlines = [
        pre_run.MatterDeadline(
            matter_id="unknown-matter",
            authored_date=date(2026, 8, 29),
            label="court-date",
            task_id="ev-1",
            matter_number=None,
            matter_number_absent="no_matter_link",
        )
    ]
    today = date(2026, 8, 31)
    digest = pre_run.project_digest(deadlines, pre_run.EscalationWindows(), ledger, today=today)
    meta = envelope.build_and_write(
        digest=digest,
        deadlines=deadlines,
        states={},
        ledger=ledger,
        today=today,
        ack_snooze_days=7,
        customer_yaml_path=str(yaml_path),
        staff_pull=lambda ids, budget: {},
    )
    assert meta["dispatch_count"] == 1
    written = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json").read_text())
    [dispatch] = written["dispatches"]
    assert dispatch["routing_leg"] == "central"  # sentinel -> central triage
    assert written["memo_matters"] == []
    assert written["unroutable"] == []


def test_dispatch_cap_overflow_is_loud_not_silent(tmp_path, monkeypatch):
    """Finding 8: over-cap recipient groups land in unroutable + memo lists."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(envelope, "_MAX_DISPATCHES", 1)
    pre_run = _load("pre_run.py", "escalator_pre_run_for_overflow")
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "escalation:\n"
        "  red_flag_recipients:\n    - ops@firm.example\n"
        "  case_alert_routing:\n"
        "    mode: matter_staff\n"
        "    fallback_recipients:\n      - fallback@firm.example\n"
        "scope:\n  inbound_allow_from:\n    - '@firm.example'\n"
    )
    deadlines = [
        _mk_deadline(pre_run, "m-1", "t-1", "2026-PI-101"),
        _mk_deadline(pre_run, "m-2", "t-2", "2026-PI-102"),
    ]
    today = date(2026, 8, 31)
    digest = pre_run.project_digest(deadlines, pre_run.EscalationWindows(), ledger, today=today)
    staff = {
        "m-1": {"responsible": {"email": "amy@firm.example", "enabled": True}, "assisting": []},
        "m-2": {"responsible": {"email": "bob@firm.example", "enabled": True}, "assisting": []},
    }
    meta = envelope.build_and_write(
        digest=digest,
        deadlines=deadlines,
        states={},
        ledger=ledger,
        today=today,
        ack_snooze_days=7,
        customer_yaml_path=str(yaml_path),
        staff_pull=lambda ids, budget: staff,
    )
    assert meta["dispatch_count"] == 1
    written = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json").read_text())
    overflow = [u for u in written["unroutable"] if u["reason"] == "dispatch_cap_exceeded"]
    assert len(overflow) == 1
    assert overflow[0]["matter_id"] in written["memo_matters"]


def test_failure_note_in_skill_md_matches_the_renderer(tmp_path):
    """Finding 9: the SKILL.md quoted failure line IS render.FAILURE_NOTE —
    the in-turn conformance check accepts exactly that text, so drift between
    the two would block the turn's only legitimate send."""
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    collapsed = " ".join(skill_md.split())
    assert " ".join(render.FAILURE_NOTE.split()) in collapsed


def test_legacy_rekey_count():
    class _State:
        attempts = 1
        acked = False

    pre_run = _load("pre_run.py", "escalator_pre_run_for_rekey")
    d = pre_run.MatterDeadline(
        matter_id="unknown-matter",
        authored_date=date(2026, 8, 29),
        label="court-date",
        task_id="ev-1",
    )
    legacy_key = ledger.item_key("ev-1", "ev-1", "court-date", date(2026, 8, 29))
    assert envelope.legacy_rekey_count([d], {legacy_key: _State()}, ledger) == 1
    assert envelope.legacy_rekey_count([d], {}, ledger) == 0


def test_bare_id_no_longer_resolves_matter(tmp_path):
    pre_run = _load("pre_run.py", "escalator_pre_run_for_id_regression")
    # A calendar-shape item whose only id is its own: previously the bare "id"
    # fallback made it the matter id; now it is the unknown-matter sentinel and
    # the item is blanket-ack only.
    assert pre_run._matter_id_of({"id": "ev-1"}) == "unknown-matter"
    assert pre_run._matter_id_of({"matterId": "m-1", "id": "ev-1"}) == "m-1"
