"""Tests for the unaudited-send reconciler (ss#2258).

The control exists because 9 of 117 real sends from the pilot inbox had no audit
record, four of them to a real client. Its two failure modes are equally fatal:
cry wolf on every legitimate send and it gets muted within a week; absorb
everything and it measures nothing. Both are pinned here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BIN / "lib"))
_spec = importlib.util.spec_from_file_location("reconcile_sends", _BIN / "reconcile-sends.py")
rec = importlib.util.module_from_spec(_spec)
# Register BEFORE exec: @dataclass resolves its own module out of sys.modules,
# and a spec-loaded module that is not there fails at class-creation time.
sys.modules["reconcile_sends"] = rec
_spec.loader.exec_module(rec)


def _msg(mid, ts, to="scott@smd.services", subject="s"):
    return {"message_id": mid, "timestamp": ts, "to": [to], "subject": subject, "labels": ["sent"]}


def _reply_row(ts, mid):
    return {"ts": ts, "action_type": "REPLY_SENT", "metadata": {"sent_message_id": mid}}


def _tool_row(ts, outcome="ok", action_class="external_send"):
    return {
        "ts": ts,
        "action_type": "TOOL_CALL_COMPLETED",
        "metadata": {
            "action_class": action_class,
            "outcome": outcome,
            "tool": "mcp_agentmail_send_message",
        },
    }


# ---------------------------------------------------------------------------
# pass 1 — exact message-id join
# ---------------------------------------------------------------------------


def test_exact_match_on_recorded_message_id():
    sent = [_msg("<a>", "2026-08-01T10:00:00.000Z")]
    rows = [_reply_row("2026-08-01T10:00:00.100Z", "<a>")]
    exact, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert (exact, tool, unaccounted) == (1, 0, [])


def test_exact_match_ignores_clock_skew_entirely():
    """The id join has no time component, so an audit row written minutes later
    still matches. This is why pass 1 is preferred over pass 2."""
    sent = [_msg("<a>", "2026-08-01T10:00:00.000Z")]
    rows = [_reply_row("2026-08-01T10:47:00.000Z", "<a>")]
    exact, _, _broker, unaccounted = rec.reconcile(sent, rows)
    assert exact == 1 and unaccounted == []


# ---------------------------------------------------------------------------
# pass 2 — tool path, tight window, one-to-one
# ---------------------------------------------------------------------------


def test_tool_path_matches_within_the_window():
    """Real observed skew was 341ms (2026-08-01)."""
    sent = [_msg("<a>", "2026-08-01T10:00:00.285Z")]
    rows = [_tool_row("2026-08-01T10:00:00.626Z")]
    exact, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert (exact, tool, unaccounted) == (0, 1, [])


def test_tool_path_does_not_match_outside_the_window():
    sent = [_msg("<a>", "2026-08-01T10:00:00.000Z")]
    rows = [_tool_row("2026-08-01T10:00:30.000Z")]  # 30s -> way outside
    _, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert tool == 0 and len(unaccounted) == 1


def test_one_audit_row_cannot_cover_two_messages():
    """The absorption failure. Two sends a second apart with only ONE audit row
    must leave exactly one unaccounted -- otherwise a single legitimate row
    launders every neighbouring unaudited send."""
    sent = [_msg("<a>", "2026-08-01T10:00:00.000Z"), _msg("<b>", "2026-08-01T10:00:01.000Z")]
    rows = [_tool_row("2026-08-01T10:00:00.500Z")]
    _, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert tool == 1
    assert len(unaccounted) == 1


def test_errored_tool_call_does_not_cover_a_send():
    """A send tool that ERRORED did not deliver, so it cannot account for a
    message that demonstrably left the mailbox."""
    sent = [_msg("<a>", "2026-08-01T10:00:00.000Z")]
    rows = [_tool_row("2026-08-01T10:00:00.100Z", outcome="error")]
    _, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert tool == 0 and len(unaccounted) == 1


def test_non_send_tool_call_does_not_cover_a_send():
    sent = [_msg("<a>", "2026-08-01T10:00:00.000Z")]
    rows = [_tool_row("2026-08-01T10:00:00.100Z", action_class="read")]
    _, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert tool == 0 and len(unaccounted) == 1


# ---------------------------------------------------------------------------
# the incident, and the no-false-positive property
# ---------------------------------------------------------------------------


def test_the_2026_08_11_incident_is_reported():
    """A message that left the mailbox with no id in the ledger and no send row
    near it is exactly the case this control was built for."""
    sent = [
        _msg("<legit>", "2026-08-11T14:00:00.000Z"),
        _msg("<incident>", "2026-08-11T14:03:14.543Z", subject="Escalator woke but..."),
    ]
    rows = [_reply_row("2026-08-11T14:00:00.100Z", "<legit>")]
    exact, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert exact == 1 and tool == 0
    assert [m["message_id"] for m in unaccounted] == ["<incident>"]


def test_a_busy_legitimate_mailbox_produces_no_finding():
    """The mute-it-within-a-week failure. Mixed transports, all accounted."""
    sent = [_msg(f"<r{i}>", f"2026-08-01T10:0{i}:00.000Z") for i in range(5)]
    sent += [_msg(f"<t{i}>", f"2026-08-01T11:0{i}:00.000Z") for i in range(4)]
    rows = [_reply_row(f"2026-08-01T10:0{i}:00.200Z", f"<r{i}>") for i in range(5)]
    rows += [_tool_row(f"2026-08-01T11:0{i}:00.300Z") for i in range(4)]
    exact, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert (exact, tool, unaccounted) == (5, 4, [])


# ---------------------------------------------------------------------------
# fail-closed the OTHER way: a broken instrument holds, it does not accuse
# ---------------------------------------------------------------------------


def test_seam_failure_holds_and_is_not_a_finding():
    def _boom(_slug):
        raise RuntimeError("seam unreachable")

    report = rec.reconcile_inbox(
        "pilot-smokeball@agentmail.to",
        ["pilot-smokeball"],
        "key",
        None,
        opener=_fake_opener({"messages": [_msg("<a>", "2026-08-01T10:00:00.000Z")]}),
        client_factory=lambda slug: (_ for _ in ()).throw(RuntimeError("x")) if False else _Boom(),
    )
    assert report.held is not None
    assert report.is_finding is False


def test_empty_audit_read_holds_rather_than_accusing():
    """A literally empty ledger on a seat that demonstrably sent mail is
    unmeasurable, not clean. Reading it as clean would be the connector-ledger
    'absent means healthy' bug all over again."""
    report = rec.reconcile_inbox(
        "pilot-smokeball@agentmail.to",
        ["pilot-smokeball"],
        "key",
        None,
        opener=_fake_opener({"messages": [_msg("<a>", "2026-08-01T10:00:00.000Z")]}),
        client_factory=lambda slug: _Empty(),
    )
    assert report.held is not None and report.is_finding is False


def test_unowned_unauthored_inbox_is_the_loudest_signal():
    """A mailbox nobody owns and nobody authored: the shape of a decommissioned
    seat still sending. The case a seat-side check could never represent."""
    report = rec.reconcile_inbox(
        "mystery-inbox@agentmail.to",
        ["pilot-smokeball", "ashton-price"],
        "key",
        None,
        opener=_fake_opener({"messages": [_msg("<a>", "2026-08-01T10:00:00.000Z")]}),
        client_factory=lambda slug: _Empty(),
    )
    assert report.slug is None
    assert report.is_finding is True
    assert len(report.unaccounted) == 1


def test_authored_non_seat_inbox_is_not_a_finding():
    """Our own rigs (probe harnesses, the opposing-counsel simulator) have no
    seat ledger to appear in. The first live run flagged 135 such sends across
    six inboxes -- a control that loud gets muted in a week."""
    inbox = next(iter(rec.KNOWN_NON_SEAT_INBOXES))
    report = rec.reconcile_inbox(
        inbox,
        ["pilot-smokeball"],
        "key",
        None,
        opener=_fake_opener({"messages": [_msg("<a>", "2026-08-01T10:00:00.000Z")]}),
        client_factory=lambda slug: _Empty(),
    )
    assert report.non_seat_reason
    assert report.is_finding is False


def test_allowlist_is_not_a_blanket_skip_of_seatless_inboxes():
    """The allowlist must stay an allowlist. If it ever becomes 'any inbox
    without a seat is fine', the loudest signal goes silent."""
    assert "mystery-inbox@agentmail.to" not in rec.KNOWN_NON_SEAT_INBOXES
    assert all("@" in k and v for k, v in rec.KNOWN_NON_SEAT_INBOXES.items())


def test_slug_for_inbox_matches_local_part():
    slugs = ["pilot-smokeball", "ashton-price"]
    assert rec.slug_for_inbox("ashton-price@agentmail.to", slugs) == "ashton-price"
    assert rec.slug_for_inbox("ss-probe-admin@agentmail.to", slugs) is None


def test_window_is_tight_enough_to_be_meaningful():
    """Pinned so nobody widens it into uselessness: past a few seconds one row
    starts absorbing neighbouring sends."""
    assert 0 < rec.TOOL_PATH_WINDOW_S <= 10


# ---------------------------------------------------------------------------
# the baseline (ss#2386): a watchdog that remembers what it already reported
#
# Every fixture below is CAPTURED, not authored: fixtures/unaudited-sends-
# 2026-08-17.json is the verbatim --json output of this reconciler against the
# live pilot inbox, and its 11 finds are the ones re-reported by #2344, #2373,
# #2380, #2381 and #2382.
# ---------------------------------------------------------------------------

_CAPTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "unaudited-sends-2026-08-17.json").read_text())
_CAPTURED_INBOX = _CAPTURE["reports"][0]["inbox"]
_CAPTURED_FINDS = _CAPTURE["reports"][0]["unaccounted"]


def _captured_sends():
    """The 11 real finds, shaped as AgentMail sent-message records."""
    return [dict(m, labels=["sent"]) for m in _CAPTURED_FINDS]


def _shipped_baseline():
    return rec.load_baseline(rec.DEFAULT_BASELINE_PATH)


def test_the_shipped_baseline_covers_every_historical_find():
    """The 11 sends re-reported five times over are in the file, so the next
    scheduled run has nothing to say about them."""
    baseline = _shipped_baseline()
    assert len(_CAPTURED_FINDS) == 11
    missing = [m["message_id"] for m in _captured_sends() if rec.fingerprint(_CAPTURED_INBOX, m) not in baseline]
    assert missing == []


def test_a_run_with_only_historical_finds_is_silent():
    """The whole point: yesterday's report is not today's alert."""
    fresh, already = rec.split_baselined(_CAPTURED_INBOX, _captured_sends(), _shipped_baseline())
    assert (fresh, already) == ([], 11)


def test_a_planted_send_absent_from_the_baseline_still_raises():
    """THE FALSIFIER (Law 12). Quieting a watchdog is only safe if you have first
    proven it can still fire. A new unaudited send, among 11 baselined ones, is
    the entire report."""
    planted = _msg(
        "<planted-0100019fffffffff@email.amazonses.com>",
        "2026-08-17T09:00:00.000Z",
        to="client-principal@firm.example",
        subject="[Deadlines] planted, absent from the baseline",
    )
    report = rec.reconcile_inbox(
        _CAPTURED_INBOX,
        ["pilot-smokeball"],
        "key",
        None,
        opener=_fake_opener({"messages": _captured_sends() + [planted]}),
        client_factory=lambda slug: _Rows([_reply_row("2026-01-01T00:00:00.000Z", "<unrelated>")]),
        baseline=_shipped_baseline(),
    )
    assert report.is_finding is True
    assert [m["message_id"] for m in report.unaccounted] == [planted["message_id"]]
    assert report.baselined == 11


def test_the_baseline_quiets_only_the_message_id_it_names():
    """A baselined send does not silence its own routine. Same inbox, same
    recipient, same subject, same second of the day -- a DIFFERENT message id is
    a different send, and stays a finding."""
    twin = dict(_captured_sends()[-1], message_id="<a-different-id@email.amazonses.com>")
    fresh, already = rec.split_baselined(_CAPTURED_INBOX, [twin], _shipped_baseline())
    assert already == 0
    assert [m["message_id"] for m in fresh] == ["<a-different-id@email.amazonses.com>"]


def test_the_baseline_cannot_reach_across_inboxes():
    """An entry naming the pilot inbox says nothing about anyone else's mail."""
    fresh, already = rec.split_baselined("ashton-price@agentmail.to", _captured_sends(), _shipped_baseline())
    assert already == 0 and len(fresh) == 11


def test_a_missing_or_corrupt_baseline_reports_everything():
    """Fail LOUD. A deleted or malformed baseline must not read as 'all clear' --
    that would make deleting a file the way to silence the control."""
    assert rec.load_baseline("/nonexistent/reconcile-sends-baseline.json") == set()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        handle.write("{ this is not json")
    assert rec.load_baseline(handle.name) == set()
    fresh, already = rec.split_baselined(_CAPTURED_INBOX, _captured_sends(), set())
    assert already == 0 and len(fresh) == 11


def test_a_hold_is_never_quieted_by_the_baseline():
    """HOLD is not a finding, so 'already reported' can never apply to it. A seam
    failure stays loud on every run, forever, which is the ss#2258 contract."""
    report = rec.reconcile_inbox(
        _CAPTURED_INBOX,
        ["pilot-smokeball"],
        "key",
        None,
        opener=_fake_opener({"messages": _captured_sends()}),
        client_factory=lambda slug: _Boom(),
        baseline=_shipped_baseline(),
    )
    assert report.held is not None
    assert report.baselined == 0
    assert report.is_finding is False
    assert rec.render([report]).startswith("HOLD  ")


def test_an_unowned_inbox_is_baselined_like_any_other():
    """The unowned-mailbox path builds its finding separately, so it needs its
    own proof that the memory applies there too."""
    sends = _captured_sends()
    report = rec.reconcile_inbox(
        "mystery-inbox@agentmail.to",
        ["pilot-smokeball"],
        "key",
        None,
        opener=_fake_opener({"messages": sends}),
        client_factory=lambda slug: _Empty(),
        baseline={rec.fingerprint("mystery-inbox@agentmail.to", m) for m in sends[:-1]},
    )
    assert report.baselined == 10
    assert report.is_finding is True
    assert len(report.unaccounted) == 1


# ---------------------------------------------------------------------------
# exit codes: a hold is not a pass
#
# ss#2258 said a control must not page on its own blips, and that stands -- a
# hold files no issue. It does NOT mean a hold may report success: an
# unevaluated control that goes green is indistinguishable from a healthy one,
# which is how a watchdog sits inert for weeks. Standardized with the sibling
# watchdogs (control-probes.py, reconcile-outcomes.py).
# ---------------------------------------------------------------------------


def test_a_hold_exits_non_zero():
    held = rec.InboxReport(inbox=_CAPTURED_INBOX, slug="pilot-smokeball", held="seam unreachable")
    assert rec.exit_code([held]) == rec.EXIT_HOLD
    assert rec.EXIT_HOLD != rec.EXIT_CLEAN


def test_a_clean_run_exits_zero():
    clean = rec.InboxReport(inbox=_CAPTURED_INBOX, slug="pilot-smokeball", sent_total=3)
    assert rec.exit_code([clean]) == rec.EXIT_CLEAN


def test_a_finding_outranks_a_hold_so_the_issue_still_files():
    """The workflow files an issue on exit 1 and reddens the run off the HOLD
    lines in the report, so a run that holds on one inbox and finds on another
    does both. Neither may swallow the other."""
    held = rec.InboxReport(inbox="ashton-price@agentmail.to", slug="ashton-price", held="no seam")
    found = _finding_report(_captured_sends())
    assert rec.exit_code([held, found]) == rec.EXIT_FINDING
    assert "HOLD  ashton-price@agentmail.to" in rec.render([held, found])


def test_missing_credentials_exit_non_zero(monkeypatch):
    """The case the review named: a scheduled run with no key measured nothing,
    and must not be reported as a clean mailbox.

    Scoped to one channel because ``main`` now runs two, and this test is about
    the AgentMail key. Running both here would have this assertion depend on
    whichever Graph secrets happen to be in the caller's environment -- and, far
    worse, would put a live read of a client's mailbox inside a unit test."""
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    assert rec.main(["--channel", "agentmail"]) == rec.EXIT_HOLD


def test_a_missing_agentmail_key_no_longer_silences_the_other_channel(monkeypatch):
    """ss#2499. This used to return EXIT_HOLD before anything else ran, so one
    absent secret would take the control off the PAYING seat, whose mail is not
    on AgentMail at all. The AgentMail half holds; the msgraph half still runs.

    FALSIFIER: restore the early ``return EXIT_HOLD`` and ``scanned`` is 1."""
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    scanned: list[str] = []
    monkeypatch.setattr(rec, "_reconcile_msgraph", lambda *_a: scanned.append("msgraph") or [])
    monkeypatch.setattr(rec, "_reconcile_agentmail", lambda *_a: scanned.append("agentmail") or [])
    rec.main([])
    # BOTH, asserted by name. "the other channel still ran" is only half the
    # property — a default that quietly dropped either half is the same bug.
    assert sorted(scanned) == ["agentmail", "msgraph"]


# ---------------------------------------------------------------------------
# ss#2499 — the msgraph half
#
# The control covered ZERO msgraph seats, and the paying firm sends through
# msgraph. Its exact key is stronger than AgentMail's because the broker mints
# it: an X-SMD-Audit-Row header on the message, and the same value on the row.
# ---------------------------------------------------------------------------

_MSG_MAILBOX = "operator@firm.example"
_MSG_SEAT = rec.MsGraphSeat(
    slug="a-seat",
    mailbox=_MSG_MAILBOX,
    tenant_id="11111111-1111-1111-1111-111111111111",
    client_id="22222222-2222-2222-2222-222222222222",
)


def _graph_message(
    *,
    token="",
    mid="<a@firm.example>",
    gid="AAMk1=",
    ts="2026-08-20T10:00:00Z",
    to="scott@smd.services",
    subject="s",
    header_name=rec.AUDIT_ROW_HEADER,
):
    headers = [{"name": header_name, "value": token}] if token else []
    return {
        "id": gid,
        "internetMessageId": mid,
        "sentDateTime": ts,
        "subject": subject,
        "toRecipients": [{"emailAddress": {"address": to}}],
        "internetMessageHeaders": headers,
    }


def _audited_row(ts, **meta):
    return {"ts": ts, "action_type": "CONFIRM_SEND_DISPATCHED", "metadata": meta}


def test_the_audit_header_is_an_exact_match(tmp_path):
    """The join the broker mints. It lives ON THE MESSAGE, so it holds even when
    the broker could not read its own vendor id back after the 202."""
    sent = [rec.normalize_graph_message(_graph_message(token="01ABC"))]
    rows = [_audited_row("2026-08-20T10:00:05Z", audit_row_token="01ABC")]
    exact, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert (exact, tool, unaccounted) == (1, 0, [])


def test_the_vendor_id_is_also_an_exact_match(tmp_path):
    """The second key: when the lookup DID work, the row carries the RFC2822 id
    and this joins on that alone -- so a run is not hostage to the header."""
    sent = [rec.normalize_graph_message(_graph_message(mid="<b@firm.example>"))]
    rows = [_audited_row("2026-08-20T10:00:05Z", vendor_message_id="<b@firm.example>")]
    exact, _tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert exact == 1 and unaccounted == []


def test_a_send_with_no_header_and_no_row_is_the_finding(tmp_path):
    """The kill test's shape, and the whole point of the control: a message in
    Sent Items that did not come through the broker."""
    sent = [rec.normalize_graph_message(_graph_message(subject="[UNAUDITED-KILLTEST-2258] x"))]
    rows = [_audited_row("2026-08-20T10:00:05Z", audit_row_token="SOMETHINGELSE")]
    exact, tool, _broker, unaccounted = rec.reconcile(sent, rows)
    assert (exact, tool) == (0, 0)
    assert unaccounted[0]["subject"].startswith("[UNAUDITED-KILLTEST-2258]")


@pytest.mark.parametrize(
    "wire_name",
    ["X-SMD-Audit-Row", "x-smd-audit-row", "X-SMD-AUDIT-ROW", "x-SMD-audit-ROW"],
)
def test_the_header_is_read_case_insensitively(wire_name):
    """RFC5322 says header names are case-insensitive and Exchange re-cases them.
    A case-sensitive compare would report every broker send as unaudited -- a
    broken instrument that looks exactly like a mailbox full of foreign mail.

    EVERY casing, not one: a single lowercase case is satisfied by comparing
    against a lowercase constant with no normalization at all, so it would pin
    the fixture rather than the property.

    FALSIFIER: drop the ``.lower()`` in ``_audit_token_of`` and the mixed-case
    rows here fail."""
    message = _graph_message(token="01ABC", header_name=wire_name)
    assert rec.normalize_graph_message(message)[rec._AUDIT_TOKEN_KEY] == "01ABC"


def test_a_foreign_header_is_not_mistaken_for_the_audit_one(tmp_path):
    other = _graph_message(token="01ABC", header_name="x-ms-exchange-something")
    assert rec.normalize_graph_message(other)[rec._AUDIT_TOKEN_KEY] == ""


def test_bcc_recipients_are_named_in_a_finding(tmp_path):
    """A finding that lists only the visible recipients describes the wrong set
    of people, and a confidently wrong finding is worse than a vague one."""
    message = _graph_message()
    message["bccRecipients"] = [{"emailAddress": {"address": "quiet@firm.example"}}]
    assert "quiet@firm.example" in rec.normalize_graph_message(message)["to"]


def test_the_message_id_is_the_rfc2822_one_not_the_graph_one(tmp_path):
    """It is what the broker records on the row, and what survives outside this
    mailbox -- in a bounce, or in whatever a firm forwards asking "did you send
    this?". The mailbox-local Graph id rides along separately."""
    normalized = rec.normalize_graph_message(_graph_message(mid="<c@firm.example>", gid="AAMkZ="))
    assert normalized["message_id"] == "<c@firm.example>"
    assert normalized["graph_id"] == "AAMkZ="


def test_the_baseline_quiets_a_reported_msgraph_send_and_only_that_one(tmp_path):
    """#2345 inherited rather than reinvented: the msgraph half uses the same
    file, the same fingerprint and the same arithmetic. A triaged send stops
    being re-reported; a NEW one from the same mailbox still is."""
    reported = rec.normalize_graph_message(_graph_message(mid="<old@firm.example>"))
    fresh = rec.normalize_graph_message(_graph_message(mid="<new@firm.example>"))
    baseline = {rec.fingerprint(_MSG_MAILBOX, reported)}
    remaining, quieted = rec.split_baselined(_MSG_MAILBOX, [reported, fresh], baseline)
    assert quieted == 1
    assert [m["message_id"] for m in remaining] == ["<new@firm.example>"]


# --- pass 3: broker dispatch, the second-live-run fix ------------------------
#
# The first live msgraph run reported 14 of 14 sends on the paying seat as
# unaudited, and all 14 were the Operator's own audited replies: the seat
# predates the audit header, so its rows carry no id at all, and msgraph sends
# are broker-dispatched and never produce a TOOL_CALL_COMPLETED row. The control
# was accusing the Operator of everything it did.

#: What the overlay writes on a msgraph REPLY_SENT row when Graph's 202 returned
#: no id to record -- 8 of 8 rows on the live seat before the header landed.
_NO_ID_NOTE = "(sent via msgraph, id unavailable)"


def _confirm_row(ts, **meta):
    """The broker's dispatch row as the live seat writes it, pre-header: the
    outcome, and an EMPTY message_id because Graph's 202 carries none."""
    return {
        "ts": ts,
        "action_type": "CONFIRM_SEND_DISPATCHED",
        "metadata": {"outcome": "sent", "message_id": "", "input_digest": "d", **meta},
    }


def _msgraph_reply_row(ts, sent_message_id=_NO_ID_NOTE):
    """The reply plugin's row as the live seat writes it, carrying the note."""
    return {
        "ts": ts,
        "action_type": "REPLY_SENT",
        "metadata": {"adapter": "msgraph", "sent_message_id": sent_message_id},
    }


def _idless_pair(sent_ts, confirm_offset=0.2, reply_offset=0.4):
    """The two rows one msgraph reply writes, seconds apart: the broker's confirm
    and the reply plugin's own row."""
    base = datetime.fromisoformat(sent_ts.replace("Z", "+00:00"))

    def at(offset):
        return (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")

    return [_confirm_row(at(confirm_offset)), _msgraph_reply_row(at(reply_offset))]


def _graph_sent(mid, ts):
    return rec.normalize_graph_message(_graph_message(mid=mid, ts=ts))


def test_an_audited_msgraph_send_with_no_id_anywhere_is_accounted_for():
    """The defect, pinned. Three sends, three audited dispatches, no ids on
    either side -- and before pass 3 all three were reported unaudited.

    FALSIFIER: delete the ``_is_broker_dispatch`` branch in ``index_audit`` and
    this goes red with broker == 0 and three unaccounted."""
    stamps = ["2026-08-21T09:00:00Z", "2026-08-21T09:10:00Z", "2026-08-21T09:20:00Z"]
    sent = [_graph_sent(f"<m{i}@firm.example>", t) for i, t in enumerate(stamps)]
    rows = [row for t in stamps for row in _idless_pair(t)]
    exact, tool, broker, unaccounted = rec.reconcile(sent, rows)
    assert (exact, tool, broker, unaccounted) == (0, 0, 3, [])


def test_a_send_whose_dispatch_row_is_missing_is_still_the_finding():
    """The same ledger minus one pair. Pass 3 must not become a blanket amnesty:
    a send the broker never recorded stays a find."""
    stamps = ["2026-08-21T09:00:00Z", "2026-08-21T09:10:00Z", "2026-08-21T09:20:00Z"]
    sent = [_graph_sent(f"<m{i}@firm.example>", t) for i, t in enumerate(stamps)]
    rows = [row for t in stamps[:2] for row in _idless_pair(t)]
    _exact, _tool, broker, unaccounted = rec.reconcile(sent, rows)
    assert broker == 2
    assert [m["message_id"] for m in unaccounted] == ["<m2@firm.example>"]


def test_the_staging_plant_is_still_a_finding_beside_audited_sends():
    """THE CANONICAL CASE, from the live run of 2026-08-21: the kill-test plant
    was correctly reported while the 14 legitimate replies beside it were not.
    Pass 3 must keep the first half of that sentence true. The plant carries the
    ids the real run observed, so this fixture is captured, not invented."""
    plant = _graph_sent(
        "<PH0PR03MB7160198B9C993AE58D51BACC97A32@PH0PR03MB7160.namprd03.prod.outlook.com>",
        "2026-08-21T09:46:57Z",
    )
    plant["subject"] = "[UNAUDITED-KILLTEST-2258] reconciler kill test mode=plant"
    audited = _graph_sent("<legit@firm.example>", "2026-08-21T09:40:00Z")
    rows = _idless_pair("2026-08-21T09:40:00Z")
    _exact, _tool, broker, unaccounted = rec.reconcile([audited, plant], rows)
    assert broker == 1
    assert [m["message_id"] for m in unaccounted] == [plant["message_id"]]
    assert unaccounted[0]["subject"].startswith("[UNAUDITED-KILLTEST-2258]")


def test_one_broker_row_cannot_cover_two_messages():
    """The absorption failure, on the new pass. Two sends ten seconds apart with
    one dispatch row: the second is still a find."""
    sent = [
        _graph_sent("<a@f.example>", "2026-08-21T09:00:00Z"),
        _graph_sent("<b@f.example>", "2026-08-21T09:00:10Z"),
    ]
    _exact, _tool, broker, unaccounted = rec.reconcile(sent, _idless_pair("2026-08-21T09:00:00Z"))
    assert broker == 1
    assert [m["message_id"] for m in unaccounted] == ["<b@f.example>"]


def test_one_broker_row_cannot_cover_two_messages_inside_the_window():
    """The ten-second case above is also outside the window, so it would pass on
    windowing alone. Here both sends are within reach of the single row, and
    CONSUMPTION is the only thing that separates them."""
    sent = [
        _graph_sent("<a@f.example>", "2026-08-21T09:00:00Z"),
        _graph_sent("<b@f.example>", "2026-08-21T09:00:02Z"),
    ]
    _exact, _tool, broker, unaccounted = rec.reconcile(sent, _idless_pair("2026-08-21T09:00:01Z"))
    assert broker == 1
    assert [m["message_id"] for m in unaccounted] == ["<b@f.example>"]


def test_a_confirm_and_its_reply_row_are_one_candidate_not_two():
    """The pair fold. One reply writes TWO rows seconds apart; unfolded they
    would account for two messages, and the send beside the reply would be
    laundered by a row describing the reply itself.

    FALSIFIER: return the candidates unfolded from ``index_audit`` and this goes
    red with broker == 2 and nothing unaccounted."""
    sent = [
        _graph_sent("<a@f.example>", "2026-08-21T09:00:00Z"),
        _graph_sent("<b@f.example>", "2026-08-21T09:00:01Z"),
    ]
    _exact, _tool, broker, unaccounted = rec.reconcile(sent, _idless_pair("2026-08-21T09:00:00Z"))
    assert broker == 1
    assert [m["message_id"] for m in unaccounted] == ["<b@f.example>"]


def test_two_confirms_in_the_same_second_stay_two_candidates():
    """Folding is CROSS-TYPE only. Two sends dispatched a second apart write two
    confirm rows, and collapsing them would turn a real second send into a find."""
    sent = [
        _graph_sent("<a@f.example>", "2026-08-21T09:00:00Z"),
        _graph_sent("<b@f.example>", "2026-08-21T09:00:01Z"),
    ]
    rows = [_confirm_row("2026-08-21T09:00:00.200Z"), _confirm_row("2026-08-21T09:00:01.200Z")]
    _exact, _tool, broker, unaccounted = rec.reconcile(sent, rows)
    assert (broker, unaccounted) == (2, [])


@pytest.mark.parametrize("outcome", ["refused", "transport_error", "failed"])
def test_a_dispatch_row_that_did_not_send_cannot_account_for_a_message(outcome):
    """A refusal and a transport error exist precisely because nothing went out.
    Reading either as a send would let the ledger's record of NOT sending account
    for a message that demonstrably left the mailbox."""
    sent = [_graph_sent("<a@f.example>", "2026-08-21T09:00:00Z")]
    rows = [_confirm_row("2026-08-21T09:00:00.200Z", outcome=outcome)]
    _exact, _tool, broker, unaccounted = rec.reconcile(sent, rows)
    assert broker == 0 and len(unaccounted) == 1


def test_a_dispatch_row_carrying_a_real_id_is_never_a_time_candidate():
    """What keeps pass 3 from weakening pass 1. An AgentMail dispatch records a
    real vendor id, so it is joinable by identity; letting it ALSO be claimed by
    proximity would let a legitimate send launder an unaudited neighbour -- which
    is the 2026-08-11 incident's own shape."""
    sent = [
        _msg("<legit>", "2026-08-11T14:00:00.000Z"),
        _msg("<incident>", "2026-08-11T14:00:01.000Z"),
    ]
    rows = [_reply_row("2026-08-11T14:00:00.100Z", "<legit>")]
    exact, _tool, broker, unaccounted = rec.reconcile(sent, rows)
    assert (exact, broker) == (1, 0)
    assert [m["message_id"] for m in unaccounted] == ["<incident>"]


def test_the_no_id_note_is_not_read_as_an_id():
    """``(sent via msgraph, id unavailable)`` is a note, not a key. Treated as an
    id it would make a row with no id look like a row that has one, which is
    exactly the row pass 3 exists to reach."""
    known, _tool, broker_rows = rec.index_audit([_msgraph_reply_row("2026-08-21T09:00:00Z")])
    assert known == set()
    assert len(broker_rows) == 1


def test_the_report_names_how_many_sends_were_matched_by_time():
    """``broker=N`` is the count of sends matched by proximity rather than
    identity. It is expected to fall to zero as seats pick up the audit header,
    and a reader cannot watch a number the report does not print."""
    rendered = rec.render(
        [
            rec.InboxReport(
                inbox=_MSG_MAILBOX,
                slug="a-seat",
                channel="msgraph",
                sent_total=3,
                matched_broker=3,
            )
        ]
    )
    assert "broker=3" in rendered


# --- the Graph read itself --------------------------------------------------


class _GraphResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeGraph:
    """Replays Graph pages and RECORDS every request, so the read-only and
    mailbox-scoped properties can be asserted from the wire rather than trusted.
    """

    def __init__(self, pages):
        self._pages = list(pages)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/token"):
            return _GraphResponse({"access_token": "tok", "expires_in": 3600})
        return _GraphResponse(self._pages.pop(0))


def test_the_reader_only_ever_gets_and_only_this_mailbox(tmp_path):
    """4.6: the read surface is one mailbox, and an instrument observes rather
    than touches. Asserted from the recorded requests, because "we only read" is
    the kind of claim that stays true until someone adds a flag."""
    http = FakeGraph([{"value": [_graph_message(token="01ABC")]}])
    token = rec.graph_token(_MSG_SEAT, "shh", opener=http)
    rec.list_sent_msgraph(_MSG_SEAT, token, opener=http)
    graph_calls = [(m, u) for m, u in http.requests if "graph.microsoft.com" in u]
    assert graph_calls and all(m == "GET" for m, _u in graph_calls)
    assert all(f"/users/{_MSG_MAILBOX}/mailFolders/sentitems/messages" in u for _m, u in graph_calls)


def test_the_read_selects_the_header_field(tmp_path):
    """internetMessageHeaders is not returned unless selected BY NAME, and
    omitting it does not error -- it yields messages with no headers, which reads
    as "nothing came through the broker" and turns this control into a machine
    for accusing the Operator of every send it made."""
    http = FakeGraph([{"value": []}])
    rec.list_sent_msgraph(_MSG_SEAT, "tok", opener=http)
    assert "internetMessageHeaders" in http.requests[-1][1]


def test_the_read_follows_pages(tmp_path):
    http = FakeGraph(
        [
            {
                "value": [_graph_message(mid="<p1@firm.example>")],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
            },
            {"value": [_graph_message(mid="<p2@firm.example>")]},
        ]
    )
    sent = rec.list_sent_msgraph(_MSG_SEAT, "tok", opener=http)
    assert [m["message_id"] for m in sent] == ["<p1@firm.example>", "<p2@firm.example>"]


def test_a_since_window_stops_paging_at_the_boundary(tmp_path):
    http = FakeGraph(
        [
            {
                "value": [
                    _graph_message(mid="<recent@firm.example>", ts="2026-08-20T10:00:00Z"),
                    _graph_message(mid="<ancient@firm.example>", ts="2026-01-01T10:00:00Z"),
                ],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
            }
        ]
    )
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    sent = rec.list_sent_msgraph(_MSG_SEAT, "tok", since=since, opener=http)
    assert [m["message_id"] for m in sent] == ["<recent@firm.example>"]


def test_a_truncated_scan_raises_rather_than_reporting_clean(tmp_path):
    """A partial scan reported as a complete one is how a control quietly stops
    covering the oldest half of a mailbox. It holds instead."""
    endless = [{"value": [], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"}] * (rec._GRAPH_MAX_PAGES + 1)
    with pytest.raises(rec.ReconcileError):
        rec.list_sent_msgraph(_MSG_SEAT, "tok", opener=FakeGraph(endless))


def test_a_next_link_off_the_graph_base_is_refused_before_the_bearer_is_sent(tmp_path):
    """The page's @odata.nextLink is the one URL here that Graph, not this
    module, writes. A link that leaves graph.microsoft.com would carry the
    bearer with it, so the reader refuses it before urlopen: exactly one
    request goes out, and the scan holds rather than reporting clean."""
    http = FakeGraph(
        [
            {"value": [], "@odata.nextLink": "https://attacker.example/v1.0/next"},
            {"value": []},
        ]
    )
    with pytest.raises(rec.ReconcileError):
        rec.list_sent_msgraph(_MSG_SEAT, "tok", opener=http)
    assert len(http.requests) == 1


def test_a_token_failure_holds_and_never_echoes_the_secret(tmp_path):
    """The token endpoint echoes request parameters back in its error bodies, and
    one of those parameters is the client secret."""

    class Boom:
        def __call__(self, request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)

    with pytest.raises(rec.ReconcileError) as exc:
        rec.graph_token(_MSG_SEAT, "super-secret-value", opener=Boom())
    assert "super-secret-value" not in str(exc.value)
    assert "401" in str(exc.value)


# --- seat discovery and the tri-state ---------------------------------------


def _seat_tree(tmp_path, **seats):
    for slug, body in seats.items():
        directory = tmp_path / slug
        directory.mkdir()
        (directory / "customer.yaml").write_text(body)
    return str(tmp_path)


_MSGRAPH_YAML = f"""
connectors:
  Email:
    adapter: msgraph
    msgraph_auth:
      tenant_id: 'tid'
      client_id: 'cid'
      mailbox: {_MSG_MAILBOX}
"""

_AGENTMAIL_YAML = """
connectors:
  Email:
    adapter: agentmail
"""


def test_seats_are_discovered_from_customer_yaml_not_a_hand_kept_list(tmp_path):
    """A hand-kept list is how a channel ends up with zero coverage and nobody
    notices, which is the state this issue found."""
    root = _seat_tree(tmp_path, graphed=_MSGRAPH_YAML, mailed=_AGENTMAIL_YAML)
    assert [s.slug for s in rec.msgraph_seats(root)] == ["graphed"]


def test_the_secret_env_is_per_seat(tmp_path):
    """ADR 0010: the firm's Graph secret is its own. A shared fallback would let
    a missing per-seat secret quietly authenticate as somebody else's app."""
    assert rec.MsGraphSeat("ashton-price", "m", "t", "c").secret_env == ("MSGRAPH_CLIENT_SECRET__ASHTON_PRICE")


def test_a_seat_with_no_secret_holds_rather_than_accusing(tmp_path, monkeypatch):
    monkeypatch.delenv("MSGRAPH_CLIENT_SECRET__A_SEAT", raising=False)
    report = rec.reconcile_mailbox(_MSG_SEAT, None)
    assert report.held and not report.is_finding


def test_an_incomplete_msgraph_auth_holds(tmp_path):
    seat = rec.MsGraphSeat(slug="half", mailbox="", tenant_id="t", client_id="c")
    report = rec.reconcile_mailbox(seat, None, secret="shh")
    assert report.held and "msgraph_auth" in report.held


def test_a_seam_failure_holds_rather_than_marking_every_send_unaccounted(tmp_path):
    """Fail-closed the other way: a failed audit read must never read as "zero
    audit rows", which would accuse the Operator of every send it made."""
    http = FakeGraph([{"value": [_graph_message(token="01ABC")]}])
    report = rec.reconcile_mailbox(_MSG_SEAT, None, opener=http, secret="shh", client_factory=lambda _slug: None)
    assert report.held and not report.is_finding


def test_an_unaudited_msgraph_send_is_a_finding_end_to_end(tmp_path):
    """The whole path: read Sent Items, read the ledger, match, report.

    FALSIFIER below is its twin -- the same call with the header recorded on the
    row comes back clean, so this cannot be an assertion that always passes."""

    class Ledger:
        def read_all(self, _table):
            return [_audited_row("2026-08-20T09:00:00Z", audit_row_token="OTHER")]

    http = FakeGraph([{"value": [_graph_message(token="01ABC")]}])
    report = rec.reconcile_mailbox(_MSG_SEAT, None, opener=http, secret="shh", client_factory=lambda _slug: Ledger())
    assert report.is_finding and report.channel == "msgraph"
    assert report.sent_total == 1 and report.matched_exact == 0


def test_an_audited_msgraph_send_is_clean_end_to_end(tmp_path):
    class Ledger:
        def read_all(self, _table):
            return [_audited_row("2026-08-20T09:00:00Z", audit_row_token="01ABC")]

    http = FakeGraph([{"value": [_graph_message(token="01ABC")]}])
    report = rec.reconcile_mailbox(_MSG_SEAT, None, opener=http, secret="shh", client_factory=lambda _slug: Ledger())
    assert not report.is_finding and report.matched_exact == 1


def test_the_report_names_which_channel_each_mailbox_came_from(tmp_path):
    """A channel that silently stops being scanned is the failure this control
    cannot afford, and an absent line is much harder to notice than a wrong one."""
    rendered = rec.render(
        [
            rec.InboxReport(inbox="a@agentmail.to", slug="s", sent_total=1),
            rec.InboxReport(inbox=_MSG_MAILBOX, slug="a-seat", sent_total=1, channel="msgraph"),
        ]
    )
    assert "(msgraph)" in rendered and "(agentmail)" in rendered
    assert "[agentmail, msgraph]" in rendered


# ---------------------------------------------------------------------------
# the fingerprint the workflow dedupes on
# ---------------------------------------------------------------------------


def _finding_report(messages):
    return rec.InboxReport(
        inbox=_CAPTURED_INBOX,
        slug="pilot-smokeball",
        sent_total=len(messages),
        unaccounted=list(messages),
    )


def test_the_same_find_set_yields_the_same_fingerprint():
    """This is what lets a run recognise its own already-open issue and decline
    to file the second, third and fifth copy."""
    first = rec.finding_digest([_finding_report(_captured_sends())])
    second = rec.finding_digest([_finding_report(list(reversed(_captured_sends())))])
    assert first and first == second


def test_one_new_send_changes_the_fingerprint():
    """The dedupe must not become the silence. A find set that grew is a
    different find set, and files a new issue even while the old one is open."""
    before = rec.finding_digest([_finding_report(_captured_sends())])
    after = rec.finding_digest([_finding_report(_captured_sends() + [_msg("<new>", "2026-08-18T09:00:00.000Z")])])
    assert after != before


def test_a_clean_run_has_no_fingerprint_and_prints_none():
    """No finding, nothing to dedupe -- and no fingerprint line in the report,
    which is what the workflow treats as 'nothing to file'."""
    clean = rec.InboxReport(inbox=_CAPTURED_INBOX, slug="pilot-smokeball", sent_total=3)
    assert rec.finding_digest([clean]) == ""
    assert "reconcile-fingerprint" not in rec.render([clean])


def test_the_report_carries_the_fingerprint_and_paste_ready_baseline_rows():
    """The workflow reads the fingerprint back out of this text, and a human
    reads the rows: disposition is one copy-paste and a PR."""
    rendered = rec.render([_finding_report(_captured_sends()[:1])])
    digest = rec.finding_digest([_finding_report(_captured_sends()[:1])])
    assert f"reconcile-fingerprint: {digest}" in rendered
    assert "operator/bin/reconcile-sends-baseline.json" in rendered
    assert _CAPTURED_FINDS[0]["message_id"] in rendered


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _Boom:
    def read_all(self, _kind):
        raise RuntimeError("seam unreachable")


class _Empty:
    def read_all(self, _kind):
        return []


class _Rows:
    """A seam that reads fine and returns rows accounting for none of the sends
    under test -- the shape of the real pilot ledger against these 11."""

    def __init__(self, rows):
        self._rows = rows

    def read_all(self, _kind):
        return self._rows


def _fake_opener(payload):
    import json as _json

    class _Resp:
        def read(self):
            return _json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(_req, timeout=None):
        return _Resp()

    return _open


# ---------------------------------------------------------------------------
# the workflow that runs this
# ---------------------------------------------------------------------------

_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "unaudited-send-reconcile.yml"


def test_every_msgraph_seat_has_its_secret_wired_into_the_workflow():
    """The one hand-kept list this design could not avoid, held to the authored
    seats by a test.

    GitHub Actions cannot enumerate secrets, so each msgraph seat's READ secret
    has to be named in the workflow env. That is exactly the shape that produced
    the gap ss#2499 closes: a channel nobody remembered to wire, reporting
    nothing and looking clean. So provisioning a seat onto Graph now fails CI
    until its secret is wired, and the failure names the variable.

    FALSIFIER: add a customer.yaml with `adapter: msgraph` and no matching env
    line and this fails; the run it protects would otherwise have HELD forever on
    a mailbox nobody noticed was unread.
    """
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    missing = [seat.secret_env for seat in rec.msgraph_seats() if f"{seat.secret_env}: " not in workflow]
    assert not missing, (
        "these msgraph seats are authored but their read secret is not wired into "
        f"unaudited-send-reconcile.yml, so the daily run cannot open their mailbox: {missing}"
    )


def test_the_scheduled_run_passes_no_channel_filter():
    """The default is BOTH. A --channel on the scheduled run would be how one
    half quietly stops being scanned."""
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    body = workflow.split("Reconcile every inbox", 1)[1]
    assert "reconcile-sends.py $ARGS" in body
    assert "--channel" not in body.split("Open an issue", 1)[0]
