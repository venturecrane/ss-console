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
