"""Tests for the terminal-state reconciler (ss#2388).

THE FIXTURES ARE CAPTURED, NOT AUTHORED. Both incident fixtures reproduce the
ledger rows quoted verbatim in the issues, with provenance recorded in each
JSON file's `_source` / `_captured_block` keys:

  * fixtures/ss2367-held-reply.json     -- REPLY_HELD at 21:21:53.988, then
    LLM_TURN_COMPLETED at 21:22:16.691, and nothing after. The Operator filed a
    correct demand letter and told nobody.
  * fixtures/ss2136-claimed-cron-runs.json -- two `hermes cron run` triggers
    that printed "Ran now: succeeded." with zero audit rows after them, on a
    seat whose audit write path was demonstrably healthy that hour.

Where a test needs the counterfactual (the run that ENDED properly), the
delivering row is marked as such in the test body: it is the outcome the
authored recovery prescribes, added to the captured rows, and it exists to prove
the control does not cry wolf. That distinction is stated rather than blurred,
because a fixture nobody can trace is how a control ends up measuring its
author's expectations.

Both failure modes are fatal in the same way as the sibling reconciler's
(ss#2258): miss the incident and the control is decoration; fire on the happy
path and it is muted within a week. Both directions are pinned here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

_BIN = Path(__file__).resolve().parents[1]
_OPERATOR = _BIN.parent
_FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(_BIN / "lib"))
sys.path.insert(0, str(_OPERATOR))

_spec = importlib.util.spec_from_file_location("reconcile_outcomes", _BIN / "reconcile-outcomes.py")
rec = importlib.util.module_from_spec(_spec)
# Register BEFORE exec: @dataclass resolves its own module out of sys.modules.
sys.modules["reconcile_outcomes"] = rec
_spec.loader.exec_module(rec)

from adapter.audit_log import ACCEPTED_ACTION_TYPES  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

CONTRACT = rec.load_contract()

SS2367 = json.loads((_FIXTURES / "ss2367-held-reply.json").read_text(encoding="utf-8"))
SS2136 = json.loads((_FIXTURES / "ss2136-claimed-cron-runs.json").read_text(encoding="utf-8"))

# Well after both incidents, so every window has elapsed and nothing is pending.
AFTER = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)


def _at(ts: str) -> datetime:
    return rec.parse_ts(ts)


def _row(ts: str, action_type: str, *, skill=None, **metadata) -> dict:
    return {
        "id": f"row-{ts}",
        "ts": ts,
        "action_type": action_type,
        "actor": "operator",
        "actor_role": "agent",
        "skill_name": skill,
        "matter_ref": None,
        "metadata": metadata,
    }


def _analyze(rows, *, claims=None, now=AFTER, slug="pilot-smokeball"):
    return rec.analyze(CONTRACT, slug, rows, now=now, claims=claims or [])


def _findings(obligations):
    return [o for o in obligations if o.is_finding]


# ---------------------------------------------------------------------------
# ss#2367 -- the held reply nobody was told about
# ---------------------------------------------------------------------------


def test_ss2367_held_reply_with_no_notice_is_a_finding():
    """The captured incident, end to end. One run, no terminal state."""
    found = _findings(_analyze(SS2367["rows"]))
    assert len(found) == 1
    assert found[0].shape == rec.SHAPE_HELD_WITHOUT_NOTICE
    assert found[0].routine_class == "reply_to_person"
    assert found[0].opened_at == _at("2026-08-13T21:21:53.988Z")


def test_ss2367_turn_completed_is_not_an_ending():
    """The row that made the incident look finished. `LLM_TURN_COMPLETED` says
    the model stopped, never that anybody was told."""
    turn = SS2367["rows"][2]
    assert turn["action_type"] == "LLM_TURN_COMPLETED"
    assert rec._terminal_state_of(CONTRACT, turn) is None


def test_ss2367_the_filed_document_does_not_close_the_reply_obligation():
    """The whole shape of the incident: the letter WAS filed, correctly, through
    the checked seam. An internal write cannot answer a person who is waiting on
    a reply, so a filed artifact after the hold must still read as silence."""
    rows = SS2367["rows"] + [
        # Counterfactual: the same internal_write the run already did, repeated
        # AFTER the hold, so the test cannot pass merely on ordering.
        _row(
            "2026-08-13T21:22:30.000Z",
            "TOOL_CALL_COMPLETED",
            skill="demand-letter-drafter",
            tool="mcp_smokeball_file_upload",
            action_class="internal_write",
            outcome="ok",
        )
    ]
    found = _findings(_analyze(rows))
    assert [f.shape for f in found] == [rec.SHAPE_HELD_WITHOUT_NOTICE]


@pytest.mark.parametrize(
    "terminal",
    [
        # The authored recovery: redraft once, and if refused twice deliver the
        # minimal factual note (operator/skills/demand-letter-drafter/SKILL.md).
        _row(
            "2026-08-13T21:22:40.000Z",
            "REPLY_SENT",
            skill="demand-letter-drafter",
            recipient="ss-probe-admin@agentmail.to",
            sent_message_id="<counterfactual>",
        ),
        # Or the escalation path, which is equally terminal.
        _row("2026-08-13T21:22:40.000Z", "ESCALATION_FIRED", skill="demand-letter-drafter"),
        # Or the tool-path send, which records no reply event at all.
        _row(
            "2026-08-13T21:22:40.000Z",
            "TOOL_CALL_COMPLETED",
            skill="demand-letter-drafter",
            tool="mcp_agentmail_send_message",
            action_class="external_send_internal",
            outcome="ok",
        ),
    ],
    ids=["reply_sent", "escalated", "tool_path_send"],
)
def test_the_control_does_not_cry_wolf_when_the_recovery_ran(terminal):
    """The captured incident plus the ending it should have had. No finding."""
    obligations = _analyze(SS2367["rows"] + [terminal])
    assert _findings(obligations) == []
    assert [o.closed_by for o in obligations] != [None]


def test_a_failed_send_does_not_close_the_obligation():
    """An attempt that errored delivered nothing. Counting it would let the
    failure close the obligation it created."""
    rows = SS2367["rows"] + [
        _row(
            "2026-08-13T21:22:40.000Z",
            "TOOL_CALL_COMPLETED",
            skill="demand-letter-drafter",
            tool="mcp_agentmail_send_message",
            action_class="external_send_internal",
            outcome="error",
        )
    ]
    assert len(_findings(_analyze(rows))) == 1


def test_inbound_then_hold_then_reply_is_one_run_and_passes():
    """The authored happy path with a hold in the middle. Two trigger events,
    ONE run: grading it as two would fire on every correct recovery."""
    rows = [
        _row("2026-08-13T21:20:00.000Z", "INBOUND_RECEIVED", skill="email-reply"),
        _row("2026-08-13T21:21:53.988Z", "REPLY_HELD", skill="email-reply", reason="tier1"),
        _row("2026-08-13T21:22:40.000Z", "REPLY_SENT", skill="email-reply", recipient="a@b.c"),
    ]
    obligations = _analyze(rows)
    assert len(obligations) == 1
    assert obligations[0].closed_by == "delivered"


def test_a_reply_delivered_before_the_hold_does_not_answer_for_it():
    """Order matters: a reply sent, then an output held, leaves the hold open.
    Without this a run could bank an early delivery against a later refusal."""
    rows = [
        _row("2026-08-13T21:20:00.000Z", "INBOUND_RECEIVED", skill="email-reply"),
        _row("2026-08-13T21:20:30.000Z", "REPLY_SENT", skill="email-reply", recipient="a@b.c"),
        _row("2026-08-13T21:21:53.988Z", "REPLY_HELD", skill="email-reply", reason="tier1"),
    ]
    found = _findings(_analyze(rows))
    assert [f.shape for f in found] == [rec.SHAPE_HELD_WITHOUT_NOTICE]


def test_one_delivery_cannot_close_two_silent_runs():
    """The absorption failure, borrowed from the unaudited-send reconciler: a
    matched terminal row is consumed, so one reply cannot launder two runs."""
    rows = [
        _row("2026-08-13T21:00:00.000Z", "INBOUND_RECEIVED", skill="email-reply"),
        _row("2026-08-13T21:05:00.000Z", "INBOUND_RECEIVED", skill="email-reply"),
        _row("2026-08-13T21:06:00.000Z", "REPLY_SENT", skill="email-reply", recipient="a@b.c"),
    ]
    obligations = _analyze(rows)
    assert len(obligations) == 2
    assert len(_findings(obligations)) == 1


# ---------------------------------------------------------------------------
# ss#2136 -- the trigger that reported success and executed nothing
# ---------------------------------------------------------------------------


def test_ss2136_claimed_cron_runs_with_no_events_are_findings():
    """Both captured claims. `Ran now: succeeded.` with zero rows after it."""
    obligations = _analyze(SS2136["rows"], claims=SS2136["claims"])
    found = _findings(obligations)
    assert len(found) == 2
    assert {f.shape for f in found} == {rec.SHAPE_NO_RUN_EVENTS}
    assert {f.skill_name for f in found} == {
        "deadline-miss-escalator",
        "medical-chronology-maintainer",
    }


def test_ss2136_a_healthy_ledger_before_the_trigger_does_not_launder_the_claim():
    """The boot self-check wrote rows at 21:02, 21:32 and 21:40Z. They prove the
    emitter worked; they say nothing about the 21:40:15Z run, and the reconciler
    must not let them."""
    assert len(SS2136["rows"]) == 3
    escalator = [c for c in SS2136["claims"] if c["routine"] == "deadline-miss-escalator"]
    found = _findings(_analyze(SS2136["rows"], claims=escalator))
    assert len(found) == 1
    assert found[0].rows_in_window == 0


def test_ss2136_a_claim_backed_by_a_real_run_passes():
    """The counterfactual: the same claim, with the escalation the job exists to
    produce. No finding."""
    escalator = [c for c in SS2136["claims"] if c["routine"] == "deadline-miss-escalator"]
    rows = SS2136["rows"] + [_row("2026-08-01T21:41:02.000Z", "ESCALATION_FIRED", skill="deadline-miss-escalator")]
    obligations = _analyze(rows, claims=escalator)
    assert _findings(obligations) == []
    assert obligations[-1].closed_by == "escalated"


# ---------------------------------------------------------------------------
# routine classes -- what counts as an ending depends on who is waiting
# ---------------------------------------------------------------------------


def test_an_internal_only_routine_is_closed_by_its_artifact():
    """medical-chronology-maintainer is declared `outbound: none` in
    output-classes.yaml: its chronology row IS the outcome. Requiring a send
    here would fire on every correct run of every memo writer."""
    rows = [
        _row("2026-08-01T09:00:00.000Z", "EMITTED_WAKE", skill="medical-chronology-maintainer"),
        _row(
            "2026-08-01T09:00:30.000Z",
            "TOOL_CALL_COMPLETED",
            skill="medical-chronology-maintainer",
            tool="mcp_smokeball_memo_create",
            action_class="internal_write",
            outcome="ok",
        ),
    ]
    obligations = _analyze(rows)
    assert obligations[0].routine_class == "scheduled_internal"
    assert obligations[0].closed_by == "internal_artifact_landed"


def test_an_outbound_routine_is_not_closed_by_an_internal_write():
    """deadline-miss-escalator is `outbound: derived`. A memo on the matter is
    not the escalation, which is the ss#2151 shape: escalations landing in memos
    nobody watches."""
    rows = [
        _row("2026-08-01T09:00:00.000Z", "EMITTED_WAKE", skill="deadline-miss-escalator"),
        _row(
            "2026-08-01T09:00:30.000Z",
            "TOOL_CALL_COMPLETED",
            skill="deadline-miss-escalator",
            tool="mcp_smokeball_memo_create",
            action_class="internal_write",
            outcome="ok",
        ),
    ]
    found = _findings(_analyze(rows))
    assert [f.shape for f in found] == [rec.SHAPE_ENDED_WITHOUT_OUTCOME]


def test_a_recorded_suppression_is_a_terminal_state():
    """SUPPRESSED_WAKE is a decision not to act, WITH its basis on the record
    (ADR 0021 Stream B). It opens no obligation and it closes one."""
    assert "SUPPRESSED_WAKE" not in CONTRACT.trigger_events
    rows = [
        _row("2026-08-01T09:00:00.000Z", "EMITTED_WAKE", skill="deadline-miss-escalator"),
        _row(
            "2026-08-01T09:00:01.000Z",
            "SUPPRESSED_WAKE",
            skill="deadline-miss-escalator",
            decision_basis="delta_under_threshold",
        ),
    ]
    assert _analyze(rows)[0].closed_by == "gate_suppressed"


def test_an_unknown_skill_holds_rather_than_passing():
    """Cannot-evaluate must never render as permitted. An unclassified run is
    neither a finding nor clean: it is reported on its own line."""
    rows = [_row("2026-08-01T09:00:00.000Z", "EMITTED_WAKE", skill="not-a-real-skill")]
    obligations = _analyze(rows)
    assert obligations[0].is_hold and not obligations[0].is_finding
    assert "UNCLASSIFIED" in rec.render([_report(obligations)])


def _report(obligations):
    report = rec.SeatReport(slug="pilot-smokeball")
    report.obligations = obligations
    return report


def test_a_run_still_inside_its_window_is_pending_not_a_finding():
    """Work in flight is not silence. A control that pages on in-flight runs is
    muted within a week."""
    opened = "2026-08-13T21:21:53.988Z"
    now = _at(opened) + timedelta(seconds=60)
    obligations = _analyze(SS2367["rows"], now=now)
    assert obligations[0].pending is True
    assert _findings(obligations) == []


# ---------------------------------------------------------------------------
# the contract itself -- a typo here would make the control inert
# ---------------------------------------------------------------------------


def test_every_contract_event_is_a_real_audit_action_type():
    """A misspelled action_type matches no row, and the control silently
    measures nothing. Pinned against the closed vocabulary in
    operator/adapter/audit_log.py."""
    named = set(CONTRACT.trigger_events) | set(CONTRACT.terminal_events)
    assert named <= set(ACCEPTED_ACTION_TYPES), sorted(named - set(ACCEPTED_ACTION_TYPES))


def test_every_accepted_state_exists_and_the_two_incidents_are_named():
    spec = yaml.safe_load((_OPERATOR / "contracts" / "terminal-states.yaml").read_text())
    declared = set(spec["terminal_states"])
    for entry in spec["routine_classes"].values():
        assert set(entry["accepts"]) <= declared
    non_terminal = {e["event"] for e in spec["non_terminal"]}
    # The two events that made ss#2367 look finished.
    assert {"LLM_TURN_COMPLETED", "REPLY_HELD"} <= non_terminal
    incidents = {s.get("incident") for s in spec["non_terminal_sequences"]}
    assert {"ss#2367", "ss#2136"} <= incidents


def test_the_reply_lane_rejects_an_internal_artifact_by_contract():
    """Stated in the contract, not only in code: this is the ss#2367 rule."""
    assert not CONTRACT.accepts("reply_to_person", "internal_artifact_landed")
    assert CONTRACT.accepts("scheduled_internal", "internal_artifact_landed")


# ---------------------------------------------------------------------------
# exit-code contract -- what the scheduled workflow keys off
# ---------------------------------------------------------------------------


def _run_main(tmp_path, rows, *, claims=None, now=AFTER, monkeypatch=None):
    rows_path = tmp_path / "rows.json"
    rows_path.write_text(json.dumps(rows), encoding="utf-8")
    argv = ["--rows", str(rows_path), "--slug", "pilot-smokeball", "--now", now.isoformat()]
    if claims is not None:
        claims_path = tmp_path / "claims.json"
        claims_path.write_text(json.dumps(claims), encoding="utf-8")
        argv += ["--claims", str(claims_path)]
    return rec.main(argv)


def test_exit_1_on_a_finding(tmp_path, capsys):
    assert _run_main(tmp_path, SS2367["rows"]) == rec.EXIT_FINDING
    assert rec.SHAPE_HELD_WITHOUT_NOTICE in capsys.readouterr().out


def test_exit_0_on_a_clean_extract(tmp_path):
    rows = SS2367["rows"] + [
        _row("2026-08-13T21:22:40.000Z", "REPLY_SENT", skill="demand-letter-drafter", recipient="a")
    ]
    assert _run_main(tmp_path, rows) == rec.EXIT_CLEAN


def test_exit_3_when_the_credential_is_missing(monkeypatch, capsys):
    """The reconcile-sends lesson, inverted. That reconciler exits 0 on a missing
    key, so a rotated secret reads as a green control until somebody looks. A
    control that cannot run has not passed."""
    monkeypatch.delenv("OPERATOR_RUNTIME_READ_SECRET", raising=False)
    assert rec.main(["--slug", "pilot-smokeball"]) == rec.EXIT_HOLD
    assert "HOLD" in capsys.readouterr().err


def test_exit_3_when_every_seat_fails_to_read(monkeypatch, capsys):
    """Read failures everywhere means nothing was measured. Unmeasured and clean
    must not print the same exit code."""
    monkeypatch.setenv("OPERATOR_RUNTIME_READ_SECRET", "x")
    monkeypatch.setattr(rec.seam_pull, "seam_client_from_env", lambda slug: None)
    assert rec.main(["--slug", "pilot-smokeball"]) == rec.EXIT_HOLD
    assert "HOLD" in capsys.readouterr().out


# ======================================================================
# ss#2582 -- one condition, one issue
#
# The reconciler filed a fresh prio:P1 every scheduled run: seven issues in
# seven days for one condition. The obvious fix -- fingerprint the findings and
# stay silent when they repeat -- is WRONG here and was nearly shipped. The
# finding set grew on 5 of 6 day-over-day transitions (350 -> 361 -> 376 -> 380
# -> 388 -> 389 -> 389). The control is firing for cause (ss#2581); a dedupe
# would have suppressed 1 of the 7 and taught the reader to skim the rest.
#
# So the marker that finds the open issue is CONSTANT for this control, and the
# digest of the finding set is a separate value whose only job is to answer
# "did the number move since last time".
# ======================================================================


def test_the_series_marker_is_constant_across_different_finding_sets():
    """This is what lets a run FIND the issue it already opened. If it varied
    with the findings -- the tempting design -- a changed set would fail to
    match yesterday's issue and file a second one, which is the defect."""
    a = _report(_analyze([_row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED")]))
    b = _report(
        _analyze(
            [
                _row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED"),
                _row("2026-08-14T09:00:00.000Z", "INBOUND_RECEIVED"),
            ]
        )
    )

    def marker_line(report):
        hits = [ln for ln in rec.render([report]).split("\n") if ln.startswith("reconcile-series:")]
        assert len(hits) == 1, f"expected exactly one marker line, got {hits}"
        return hits[0]

    # Equality, not `in`. Found by mutation testing 2026-08-24: appending the
    # finding count to the marker -- the precise wrong design this test exists
    # to forbid -- left `SERIES_MARKER in rendered` true, because the constant
    # is a prefix of the varying string. A substring assertion cannot see it.
    assert marker_line(a) == rec.SERIES_MARKER
    assert marker_line(b) == rec.SERIES_MARKER
    assert marker_line(a) == marker_line(b)


def test_the_digest_ignores_rows_read():
    """rows_read moves every run -- 2045 -> 2089 on ashton-price between two
    consecutive days -- while the findings stood still. A digest over the
    report TEXT would change daily and the control would comment every day
    about nothing. Falsifier for the pair below."""
    obligations = _analyze([_row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED")])
    quiet = _report(obligations)
    quiet.rows_read = 2045
    busy = _report(obligations)
    busy.rows_read = 2089
    assert rec.finding_digest([quiet]) == rec.finding_digest([busy])


def test_the_digest_changes_when_a_genuine_finding_appears():
    """The other half. Without this the digest could be a constant and every
    test above would still pass."""
    one = _report(_analyze([_row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED")]))
    two = _report(
        _analyze(
            [
                _row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED"),
                _row("2026-08-14T09:00:00.000Z", "INBOUND_RECEIVED"),
            ]
        )
    )
    assert len(two.findings) > len(one.findings)
    assert rec.finding_digest([one]) != rec.finding_digest([two])


def test_the_digest_is_order_independent():
    """Seats are read in whatever order the fleet list returns them."""
    a = _report(_analyze([_row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED")]))
    b = _report(
        _analyze([_row("2026-08-14T09:00:00.000Z", "INBOUND_RECEIVED")], slug="scott"),
    )
    b.slug = "scott"
    assert rec.finding_digest([a, b]) == rec.finding_digest([b, a])


def test_a_finding_key_never_carries_a_count():
    """rows_in_window is recomputed against whatever window this run pulled;
    pending depends on `now`; routine_class is mutated in place when a hold
    folds into an enclosing run. None of them identify the finding, and any of
    them in the key reintroduces the daily churn."""
    obligation = _findings(_analyze([_row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED")]))[0]
    key = rec.finding_key("pilot-smokeball", obligation)
    assert str(obligation.rows_in_window) not in key.split("|")
    assert obligation.routine_class not in key
    assert "pending" not in key


def test_a_claimed_finding_keys_off_the_report_slug_not_its_own():
    """_claimed_obligations takes slug=str(claim.get("slug") or ""), which can
    be the empty string. Keyed off the obligation, two seats' claimed findings
    would collide on `|ts:...`."""
    claimed = rec.Obligation(
        slug="",
        opened_at=rec.parse_ts("2026-08-13T21:21:53.988Z"),
        opened_by="external claim",
        trigger_kind="claimed",
        routine_class="scheduled_internal",
        shape=rec.SHAPE_NO_RUN_EVENTS,
    )
    left = rec.finding_key("pilot-smokeball", claimed)
    right = rec.finding_key("ashton-price", claimed)
    assert left != right
    assert left.startswith("pilot-smokeball|")


def test_the_series_marker_sits_at_column_zero():
    """The workflow greps `^reconcile-series: ` with sed. Finding detail lines
    are indented eight spaces; if the marker ever drifts into an indented
    block the search silently finds nothing and the control files a second
    issue every day again."""
    out = rec.render([_report(_analyze([_row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED")]))])
    marker_lines = [ln for ln in out.split("\n") if rec.SERIES_MARKER in ln]
    assert marker_lines, "no marker line emitted"
    assert all(not ln.startswith(" ") for ln in marker_lines)


def test_json_mode_carries_no_marker():
    """--json is consumed by machines that parse the whole payload; a bare
    marker line would break the parse, and the sibling reconciler omits it
    there for the same reason."""
    out = rec.as_json([_report(_analyze([_row("2026-08-13T21:21:53.988Z", "INBOUND_RECEIVED")]))])
    assert rec.SERIES_MARKER not in out


def test_a_clean_run_still_emits_the_marker_so_the_issue_can_be_updated():
    """A day with nothing to report must still be able to find the open issue
    and record that the count went to zero. Silence is what this whole control
    exists to remove."""
    assert rec.SERIES_MARKER in rec.render([_report([])])


# ----------------------------------------------------------------------
# Workflow conformance. The renderer and the workflow are coupled by a grep,
# and a grep that stops matching fails silently -- the control would file a
# second issue every day again and look healthy doing it. The sibling suite
# pins its own workflow the same way.
# ----------------------------------------------------------------------

_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "terminal-state-reconcile.yml"


def test_the_workflow_greps_the_marker_the_renderer_actually_emits():
    text = _WORKFLOW.read_text()
    assert "sed -n 's/^reconcile-series: //p'" in text
    assert rec.SERIES_MARKER.split(":")[0] + ":" == "reconcile-series:"


def test_the_workflow_refuses_to_file_without_a_marker():
    """A findings run that emits no marker means the renderer and the grep have
    drifted. Filing anyway would open a fresh un-findable issue daily, which is
    the original defect wearing the fix's clothes."""
    assert "findings reported with no reconcile-series line" in _WORKFLOW.read_text()


def test_the_hold_step_is_not_gated_on_findings():
    """pilot-law was dark for seven days and only the daily P1 said so. The HOLD
    surface must not be reachable only through the findings path, or collapsing
    to one issue hides it."""
    text = _WORKFLOW.read_text()
    hold_step = text[text.index("A held seat is always said out loud") :]
    condition = hold_step[: hold_step.index("run: |")]
    assert "steps.reconcile.outputs.status == '1'" not in condition
    assert "GITHUB_STEP_SUMMARY" in hold_step


# ----------------------------------------------------------------------
# ss#2582 follow-on: a machine that was never stood up is not a dark seat.
#
# seat_slugs() enumerates AUTHORED directories. pilot-law has been authored and
# unprovisioned since 2026-06-05 (audit-chain-watch.py:29), so every run holds
# on it with "Name or service not known" -- DNS failing to resolve a host that
# does not exist. Reported as HOLD it would fire a warning every day forever
# about a known non-seat, which is the same noise this issue set out to remove.
#
# The seat descriptor refuses to carry a lifecycle field on purpose ("a claim an
# agent can write is one that rots"), so the discriminator is derived from the
# probe: name does not resolve -> the machine is ABSENT; anything else ->
# genuinely HELD. Named either way, so the denominator stays visible (#2366).
# ----------------------------------------------------------------------


def _boom(exc):
    class _Client:
        def read_all(self, _kind):
            raise exc

    return lambda _slug: _Client()


def test_a_hostname_that_does_not_resolve_is_absent_not_held():
    import socket
    import urllib.error

    err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    report = rec.reconcile_seat(CONTRACT, "pilot-law", now=AFTER, client_factory=_boom(err))
    assert report.absent, "an unresolvable host must be marked absent"
    assert not report.held, "and must not also be held, or it warns daily forever"


def test_a_live_seat_that_fails_to_read_is_still_held():
    """The falsifier. If everything became 'absent' the warning could never fire
    and a genuinely dark seat would go silent -- the exact failure the HOLD
    surface exists to prevent."""
    report = rec.reconcile_seat(CONTRACT, "pilot-smokeball", now=AFTER, client_factory=_boom(TimeoutError("timed out")))
    assert report.held, "a reachable-but-failing seat is held"
    assert not report.absent


def test_absent_seats_are_named_in_the_report_not_filtered_away():
    """#2366: a control that quietly drops rows reports a denominator it did not
    measure."""
    report = rec.SeatReport(slug="pilot-law")
    report.absent = "no machine at that name"
    out = rec.render([report])
    assert "pilot-law" in out
    assert "SKIP" in out
    assert not any(ln.startswith("HOLD") for ln in out.split("\n"))


# ---------------------------------------------------------------------------
# ss#2581 -- WHO a silent reply-lane run is about
#
# On 2026-09-02 two silent inbounds on the paying client's seat were reported
# as client silence and took an hour of a person's time to attribute to the
# SMD operator's own test emails. The row had carried `sender_key` since
# ss#2497; the report never said so. These pin that it does, in both
# directions: the firm's people are named as such, and nothing else is ever
# mistaken for them.
# ---------------------------------------------------------------------------

from recipient_policy import sender_key as _sk  # noqa: E402 - mid-module import beside the tests that use it; the shim at the top puts lib on the path

_FIRM = "christa@example-firm.test"
_SMD = "operator@smd.services"
_PROBE = "ss-probe-admin@agentmail.test"
_ROSTER = {
    _sk(_FIRM): rec.SENDER_FIRM,
    _sk(_SMD): rec.SENDER_SMD,
    _sk(_PROBE): rec.SENDER_PROBE,
}
_T0 = "2026-08-19T06:08:31.865Z"  # a real silent-inbound timestamp from #2593
# Every window here has elapsed by this moment; AFTER (08-17) predates the rows.
_NOW = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)


def _silent_inbound(sender=None, *, ts=_T0, action="INBOUND_RECEIVED"):
    meta = {"sender_key": _sk(sender)} if sender else {}
    return _row(ts, action, **meta)


def _only_finding(rows, roster=_ROSTER, slug="ashton-price"):
    found = _findings(rec.analyze(CONTRACT, slug, rows, now=_NOW, roster=roster))
    assert len(found) == 1, found
    return found[0]


def test_a_firm_rostered_sender_is_named_as_the_firm():
    f = _only_finding([_silent_inbound(_FIRM)])
    assert f.sender_class == rec.SENDER_FIRM
    assert f.sender_key_via == "row"


def test_the_smd_operators_own_email_is_never_reported_as_the_firm():
    """The falsifier for the whole feature: the case that cost an hour."""
    f = _only_finding([_silent_inbound(_SMD)])
    assert f.sender_class == rec.SENDER_SMD
    assert f.sender_class != rec.SENDER_FIRM


def test_a_probe_sender_is_a_probe_even_though_it_is_rostered():
    f = _only_finding([_silent_inbound(_PROBE)])
    assert f.sender_class == rec.SENDER_PROBE


def test_a_keyed_sender_on_nobodys_roster_is_unknown_not_firm():
    """Somebody wrote in and we cannot say who. That is a different sentence
    from 'the firm wrote in', and the report must not merge them."""
    f = _only_finding([_silent_inbound("stranger@elsewhere.test")])
    assert f.sender_class == rec.SENDER_UNKNOWN


def test_a_row_from_before_ss2497_is_unrecorded_not_unknown():
    f = _only_finding([_silent_inbound(None)])
    assert f.sender_class == rec.SENDER_UNRECORDED
    assert f.sender_key is None


def test_a_cron_wake_and_a_bare_hold_have_no_sender_class():
    """Not `unknown`: inventing a sender for a scheduled run would make a cron
    silence read as an unidentified person."""
    hold = _findings(_analyze(SS2367["rows"]))[0]
    assert hold.trigger_kind == "hold"
    assert hold.sender_class is None
    wake = _row("2026-08-13T09:00:00Z", "EMITTED_WAKE", skill="deadline-miss-escalator")
    found = _findings(rec.analyze(CONTRACT, "pilot-smokeball", [wake], now=_NOW, roster=_ROSTER))
    assert found and found[0].sender_class is None


def test_webhook_routed_adopts_the_key_of_the_inbound_it_routed_and_says_so():
    """The captured shape: WEBHOOK_ROUTED at .865, INBOUND_RECEIVED at .867,
    two obligations for one message. Both must name the same person."""
    routed = _silent_inbound(None, ts="2026-08-19T06:08:31.865Z", action="WEBHOOK_ROUTED")
    routed["skill_name"] = "matter-inbox-router"
    received = _silent_inbound(_SMD, ts="2026-08-19T06:08:31.867Z")
    found = _findings(rec.analyze(CONTRACT, "ashton-price", [routed, received], now=_NOW, roster=_ROSTER))
    by_type = {f.opened_by: f for f in found}
    assert by_type["INBOUND_RECEIVED"].sender_key_via == "row"
    assert by_type["WEBHOOK_ROUTED"].sender_key_via == "sibling"
    assert by_type["WEBHOOK_ROUTED"].sender_class == rec.SENDER_SMD


def test_a_sibling_key_is_not_adopted_across_a_different_message():
    """The falsifier for adoption: an unkeyed inbound minutes before a keyed one
    is a different message, and must stay unrecorded rather than borrow."""
    earlier = _silent_inbound(None, ts="2026-08-19T06:00:00Z", action="WEBHOOK_ROUTED")
    later = _silent_inbound(_FIRM, ts="2026-08-19T06:08:31.867Z")
    found = _findings(rec.analyze(CONTRACT, "ashton-price", [earlier, later], now=_NOW, roster=_ROSTER))
    by_type = {f.opened_by: f for f in found}
    assert by_type["WEBHOOK_ROUTED"].sender_class == rec.SENDER_UNRECORDED


def test_the_report_line_and_header_carry_the_class_and_never_an_address():
    report = rec.SeatReport(slug="ashton-price")
    report.obligations = rec.analyze(
        CONTRACT,
        "ashton-price",
        [_silent_inbound(_FIRM), _silent_inbound(_SMD, ts="2026-08-19T07:00:00Z")],
        now=_NOW,
        roster=_ROSTER,
    )
    out = rec.render([report])
    assert "silent=2 (firm-rostered=1 smd-operator=1)" in out
    assert "sender=firm-rostered" in out and "sender=smd-operator" in out
    for addr in (_FIRM, _SMD, _PROBE):
        assert addr not in out, "a raw address must never enter the report"
    assert _sk(_FIRM) not in out, "nor the hash -- the class is the whole point"


def test_sender_class_does_not_change_a_findings_identity():
    """A roster edit must never make yesterday's finding look new."""
    f = _only_finding([_silent_inbound(_FIRM)])
    key_as_firm = rec.finding_key("ashton-price", f)
    f.sender_class = rec.SENDER_UNKNOWN
    assert rec.finding_key("ashton-price", f) == key_as_firm


def test_load_roster_reads_the_authored_seat_and_classes_by_domain(tmp_path):
    seat = tmp_path / "some-seat"
    seat.mkdir()
    (seat / "customer.yaml").write_text(
        "users:\n"
        f"  - email: {_FIRM}\n    role: staff\n"
        f"  - email: {_SMD}\n    role: principal\n"
        f"  - email: {_PROBE}\n    role: staff\n",
        encoding="utf-8",
    )
    roster = rec.load_roster("some-seat", customers_dir=tmp_path)
    assert roster == _ROSTER
    assert not any("@" in k for k in roster), "keys are hashes, never addresses"


def test_an_unauthored_seat_has_an_empty_roster_and_classes_unknown(tmp_path):
    assert rec.load_roster("never-provisioned", customers_dir=tmp_path) == {}
    f = _only_finding([_silent_inbound(_FIRM)], roster={})
    assert f.sender_class == rec.SENDER_UNKNOWN


def test_the_live_ashton_price_roster_classes_the_operator_as_smd():
    """Against the real authored file, so the classing rule cannot drift from
    the seat it is for. The address itself stays out of this test."""
    roster = rec.load_roster("ashton-price")
    assert rec.SENDER_SMD in roster.values()
    assert rec.SENDER_FIRM in roster.values()
