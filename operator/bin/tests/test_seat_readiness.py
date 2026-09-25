"""Tests for seat-readiness.py.

The point of this file is narrow and it is NOT "the readiness tool runs". The
tool exists because a readiness check that enumerated two of three required
secrets reported READY and could not have said anything else. So the tests that
matter here are the ones that prove each check CAN FAIL, and that an
unanswerable check never reads as ready.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seat_readiness", _BIN / "seat-readiness.py")
sr = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# Registered BEFORE exec: @dataclass resolves annotations via
# sys.modules[cls.__module__], which is None for a module that only exists as a
# local variable.
sys.modules["seat_readiness"] = sr
_spec.loader.exec_module(sr)


def _report() -> "sr.Report":
    return sr.Report("fixture")


SMOKEBALL_CFG = {
    "connectors": {
        "PracticeManagement": {
            "backend": "mcp:smokeball",
            "enabled": True,
            "environment": "production",
        }
    }
}

# The real declared set, read from the shipped manifest rather than retyped —
# retyping it here would rebuild the exact defect this tool exists to catch.
DECLARED = {
    s["runtime_env"]
    for s in __import__("tomllib").loads((_BIN.parents[1] / "operator/connectors/smokeball/manifest.toml").read_text())[
        "connector"
    ]["required_secrets"]
}


def test_manifest_declares_more_than_the_two_that_were_checked() -> None:
    """The 2026-07-29 readiness note verified CLIENT_ID + CLIENT_SECRET only.

    If the manifest ever declared just those two, the credential check below
    could not fail on the third and this whole file would be theatre.
    """
    assert "SMOKEBALL_API_KEY" in DECLARED
    assert len(DECLARED) >= 3


def test_credentials_fail_when_a_declared_secret_is_missing() -> None:
    """The A&P case, reproduced: client id + secret staged, API key absent."""
    rep = _report()
    sr.check_connector_credentials(rep, SMOKEBALL_CFG, {"SMOKEBALL_CLIENT_ID", "SMOKEBALL_CLIENT_SECRET"})
    row = next(r for r in rep.rows if r.section == "credentials")
    assert row.status == sr.FAIL
    assert "SMOKEBALL_API_KEY" in row.detail
    assert rep.blocking


def test_credentials_pass_when_every_declared_secret_is_present() -> None:
    """The control. Without this, a check that always FAILs would look correct."""
    rep = _report()
    sr.check_connector_credentials(rep, SMOKEBALL_CFG, set(DECLARED))
    row = next(r for r in rep.rows if r.section == "credentials")
    assert row.status == sr.PASS
    assert not rep.blocking


def test_credentials_unknown_when_the_seat_cannot_be_probed() -> None:
    """An unreadable seat must not pass. `None` (could not ask) and `set()` (asked,
    nothing there) are deliberately different inputs."""
    rep = _report()
    sr.check_connector_credentials(rep, SMOKEBALL_CFG, None)
    row = next(r for r in rep.rows if r.section == "credentials")
    assert row.status == sr.UNKNOWN
    assert rep.blocking, "UNKNOWN must block; an unanswerable check is not a ready one"


def test_empty_seat_is_a_failure_not_an_unknown() -> None:
    rep = _report()
    sr.check_connector_credentials(rep, SMOKEBALL_CFG, set())
    assert next(r for r in rep.rows if r.section == "credentials").status == sr.FAIL


def test_disabled_connector_is_not_checked() -> None:
    cfg = {
        "connectors": {"PracticeManagement": {**SMOKEBALL_CFG["connectors"]["PracticeManagement"], "enabled": False}}
    }
    rep = _report()
    sr.check_connector_credentials(rep, cfg, set())
    assert not [r for r in rep.rows if r.status == sr.FAIL]


def test_channel_fails_with_no_email_connector() -> None:
    """A&P today: every initiation-card command is something a person says, and
    there is nobody to say it to."""
    rep = _report()
    sr.check_channel(rep, {"escalation": {"red_flag_recipients": ["a@b.com"]}})
    statuses = {r.check: r.status for r in rep.rows}
    assert statuses["inbound conversational channel authored"] == sr.FAIL
    assert statuses["case alerts deliverable"] == sr.FAIL


def test_channel_passes_with_an_enabled_email_connector() -> None:
    rep = _report()
    sr.check_channel(
        rep,
        {
            "connectors": {"Email": {"adapter": "msgraph", "enabled": True}},
            "escalation": {"red_flag_recipients": ["a@b.com"]},
        },
    )
    assert all(r.status == sr.PASS for r in rep.rows)


def test_channel_fails_when_the_connector_is_authored_but_disabled() -> None:
    """Authored-but-off is the shape that reads as configured and is not."""
    rep = _report()
    sr.check_channel(rep, {"connectors": {"Email": {"adapter": "msgraph", "enabled": False}}})
    assert rep.rows[0].status == sr.FAIL


def test_routines_off_requires_both_schedules_and_webhooks_quiet() -> None:
    """'Routines are off' is only true when BOTH halves are off. The webhook half
    is the one that fires on the firm's own activity."""
    cfg = {
        "personas": [
            {
                "skills": [
                    {"name": "memo", "enabled": True, "initiation": {"webhook": True}},
                ]
            }
        ],
        "webhook_triggers": [{"source": "smokeball", "event_type": "matter.updated", "skill": "memo"}],
    }
    rep = _report()
    sr.check_routines(rep, cfg, "# schedule: '0 7 * * *'\n")
    by = {r.check: r for r in rep.rows}
    assert by["scheduled routines off"].status == sr.PASS, "a commented schedule is off"
    assert by["webhook-initiated routines off"].status == sr.FAIL
    assert "memo" in by["webhook-initiated routines off"].detail


def test_active_schedule_line_is_detected() -> None:
    rep = _report()
    sr.check_routines(rep, {}, "      schedule: '0 7 * * *'\n")
    assert next(r for r in rep.rows if r.check == "scheduled routines off").status == sr.FAIL


def test_webhook_trigger_to_a_disabled_skill_is_off() -> None:
    cfg = {
        "personas": [{"skills": [{"name": "memo", "enabled": False, "initiation": {"webhook": True}}]}],
        "webhook_triggers": [{"source": "smokeball", "event_type": "x", "skill": "memo"}],
    }
    rep = _report()
    sr.check_routines(rep, cfg, "")
    assert next(r for r in rep.rows if r.check == "webhook-initiated routines off").status == sr.PASS


def test_every_row_states_a_falsifier() -> None:
    """Law 12, enforced on the tool itself: a row that cannot say what would have
    made it false is a row that proves nothing."""
    rep = _report()
    sr.check_connector_credentials(rep, SMOKEBALL_CFG, set())
    sr.check_channel(rep, {"connectors": {"Email": {"adapter": "x", "enabled": True}}})
    sr.check_routines(rep, {}, "")
    assert rep.rows
    for r in rep.rows:
        assert r.falsifier.strip(), f"row {r.check!r} carries no falsifier"


def _grid_cfg(initiation: dict, *, channel: bool) -> dict:
    cfg: dict = {
        "personas": [{"skills": [{"name": "discovery-served-watch", "enabled": True, "initiation": initiation}]}]
    }
    if channel:
        cfg["connectors"] = {"Email": {"adapter": "msgraph", "enabled": True}}
    return cfg


def test_person_invoked_with_no_channel_is_not_runnable() -> None:
    """Regression: the first cut derived 'blocked' from `can_run_today.startswith("NO")`,
    so 'person-invoked only — but NO channel to invoke it on' was counted as
    RUNNABLE. It undercounted A&P's blocked routines 2 vs 8. A checker that reads
    greener than the world is the defect, not a cosmetic bug."""
    rows = sr.coverage_rows("ashton-price", _grid_cfg({"manual": True}, channel=False))
    assert rows, "ashton-price must have grid rows for this test to mean anything"
    served = next(r for r in rows if r["skills"] == ["discovery-served-watch"])
    assert served["runnable"] is False
    assert "NO" in served["can_run_today"]


def test_person_invoked_with_a_channel_is_runnable() -> None:
    rows = sr.coverage_rows("ashton-price", _grid_cfg({"manual": True}, channel=True))
    served = next(r for r in rows if r["skills"] == ["discovery-served-watch"])
    assert served["runnable"] is True


def test_unbound_skill_is_not_runnable() -> None:
    rows = sr.coverage_rows("ashton-price", {"personas": []})
    assert rows
    assert all(r["runnable"] is False for r in rows)
    assert all(r["unbound"] for r in rows if r["skills"])


def test_coverage_has_one_row_per_promised_routine() -> None:
    """Completeness is structural: the rows come from the compiled letter-07 grid,
    so a promised capability cannot be missing from the table."""
    grid = yaml.safe_load((sr.CUSTOMERS / "ashton-price" / "routine-grid.yaml").read_text())
    rows = sr.coverage_rows("ashton-price", sr.load_customer("ashton-price"))
    assert len(rows) == len(grid["rows"]) == 19


def test_proving_is_never_invented() -> None:
    """Nothing on this machine knows whether a routine was demonstrated to the
    firm, so every generated row must say so rather than leave a blank that reads
    as a pass."""
    rows = sr.coverage_rows("ashton-price", sr.load_customer("ashton-price"))
    assert rows and all(r["proving"] == "(none recorded)" for r in rows)


@pytest.mark.parametrize("slug", ["ashton-price", "pilot-smokeball"])
def test_real_seats_parse(slug: str) -> None:
    """The shipped configs load and produce rows — catches a schema change that
    would silently drop a section."""
    cfg = sr.load_customer(slug)
    raw = (sr.CUSTOMERS / slug / "customer.yaml").read_text()
    rep = sr.Report(slug)
    sr.check_connector_credentials(rep, cfg, set())
    sr.check_routines(rep, cfg, raw)
    sr.check_channel(rep, cfg)
    sr.check_initiation_card(rep, slug, cfg)
    assert {r.section for r in rep.rows} >= {"credentials", "routines", "channel", "card"}


# ------------------------------------------------------------ deadline replies


def _digest_cfg(*, posture="autonomous", red=("ops@firm.example",), fallback=(), grants=("@firm.example",), cron=True):
    persona = {
        "name": "Operator",
        "entitlements": {"exposure": {"external_send_internal": posture}},
        "cron": [{"skill": "deadline-miss-escalator", "schedule": "0 7 * * *"}] if cron else [],
    }
    esc: dict = {"red_flag_recipients": list(red)}
    if fallback:
        esc["case_alert_routing"] = {"mode": "matter_staff", "fallback_recipients": list(fallback)}
    return {"personas": [persona], "escalation": esc, "scope": {"inbound_allow_from": list(grants)}}


def _deadline_rows(cfg: dict) -> dict:
    rep = _report()
    sr.check_deadline_replies(rep, cfg)
    return {r.check: r for r in rep.rows}


def test_a_granted_autonomous_digest_passes() -> None:
    rows = _deadline_rows(_digest_cfg())
    assert [r.status for r in rows.values()] == [sr.PASS, sr.PASS]


@pytest.mark.parametrize("field", ["red", "fallback"])
def test_a_recipient_who_cannot_reply_fails(field: str) -> None:
    """The digest invites a reply; an ungranted recipient's reply is refused and
    the item keeps firing. Both recipient lists count."""
    kwargs = {"red": ("ops@firm.example",), "fallback": ("ops@firm.example",)}
    kwargs[field] = ("outsider@elsewhere.example",)
    row = _deadline_rows(_digest_cfg(**kwargs))["every deadline recipient may reply"]
    assert row.status == sr.FAIL and row.blocker
    assert "outsider@elsewhere.example" in row.detail


def test_grant_semantics_are_the_routing_modules() -> None:
    """A domain grant covers the domain exactly; a lookalike domain is not it."""
    ok = _deadline_rows(_digest_cfg(red=("Amy@Firm.Example",)))["every deadline recipient may reply"]
    assert ok.status == sr.PASS
    bad = _deadline_rows(_digest_cfg(red=("amy@notfirm.example",)))["every deadline recipient may reply"]
    assert bad.status == sr.FAIL


@pytest.mark.parametrize("posture", ["confirm", "draft_for_review"])
def test_a_digest_held_or_drafted_fails(posture: str) -> None:
    """Held or drafted, the digest goes out with no fired rows behind its numbers."""
    row = _deadline_rows(_digest_cfg(posture=posture))["deadline digest sends with rows behind its numbers"]
    assert row.status == sr.FAIL and posture in row.detail


def test_no_escalator_cron_is_informational_only() -> None:
    rows = _deadline_rows(_digest_cfg(cron=False, red=("outsider@elsewhere.example",), posture="confirm"))
    assert list(rows) == ["every deadline recipient may reply"]
    row = rows["every deadline recipient may reply"]
    assert row.status == sr.INFO and not row.blocker


def test_a_blank_schedule_is_not_a_digest() -> None:
    cfg = _digest_cfg(posture="confirm")
    cfg["personas"][0]["cron"][0]["schedule"] = ""
    assert _deadline_rows(cfg)["every deadline recipient may reply"].status == sr.INFO


def _casework_cfg(*, skill="date-prep-brief", block=None, mode="matter_staff", posture="autonomous") -> dict:
    cfg = _digest_cfg(posture=posture, cron=False)
    cfg["personas"][0]["cron"] = [{"skill": skill, "schedule": "5 8-11 * * 1-5"}]
    cfg["escalation"]["case_alert_routing"] = {"mode": mode}
    if block is not None:
        cfg["case_manager"] = block
    return cfg


def test_no_casework_cron_adds_no_rows() -> None:
    assert list(_deadline_rows(_digest_cfg())) == [
        "every deadline recipient may reply",
        "deadline digest sends with rows behind its numbers",
    ]


def test_an_armed_casework_routine_with_its_job_passes() -> None:
    rows = _deadline_rows(_casework_cfg(block={"date_prep": {"level": "prepares", "window_days": 14}}))
    casework = {k: v for k, v in rows.items() if k != "every deadline recipient may reply"}
    assert set(casework) == {
        "every armed case-manager routine has a job authored",
        "the date-prep brief reaches the matter's own staff",
        "case-manager messages send with rows behind their numbers",
    }
    assert all(r.status == sr.PASS for r in casework.values())


def test_an_armed_casework_routine_with_no_job_fails() -> None:
    """Armed with its job off: every tick suppresses, and nobody would know."""
    row = _deadline_rows(_casework_cfg(skill="task-list-keeper", block={"date_prep": {"level": "prepares"}}))[
        "every armed case-manager routine has a job authored"
    ]
    assert row.status == sr.FAIL and row.blocker and "task-list-keeper" in row.detail


def test_a_date_prep_brief_needs_matter_staff_routing() -> None:
    row = _deadline_rows(_casework_cfg(block={"date_prep": {}}, mode="central"))[
        "the date-prep brief reaches the matter's own staff"
    ]
    assert row.status == sr.FAIL and "central" in row.detail


@pytest.mark.parametrize("posture", ["confirm", "draft_for_review"])
def test_a_casework_message_held_or_drafted_fails(posture: str) -> None:
    row = _deadline_rows(_casework_cfg(block={"date_prep": {}}, posture=posture))[
        "case-manager messages send with rows behind their numbers"
    ]
    assert row.status == sr.FAIL and posture in row.detail


@pytest.mark.parametrize("slug", ["ashton-price", "pilot-smokeball"])
def test_real_seats_pass_the_deadline_reply_checks(slug: str) -> None:
    rep = sr.Report(slug)
    sr.check_deadline_replies(rep, sr.load_customer(slug))
    assert not rep.blocking, [(r.check, r.detail) for r in rep.blocking]
