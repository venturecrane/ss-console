#!/usr/bin/env python3
"""Reconcile what left an Operator's mailbox against what its audit log recorded.

WHY THIS EXISTS (ss#2258). The audit log is what a law firm is offered as the
record of what its Operator did. On 2026-08-11 a message left the pilot seat's
inbox with no audit row at all, and reconciling the whole mailbox found it was
not alone: 9 of 117 sends had no record, four of them to a real client. Nothing
was watching, because nothing compared the two sides.

The comparison runs CONSOLE-SIDE on purpose. The seat cannot be the auditor of
its own egress: a seat-side check can only see its own inbox, so a send from an
inbox belonging to a decommissioned seat -- or to no seat at all -- is exactly
the case it could never represent. From here, an inbox with no owning seat is the
LOUDEST signal rather than an invisible one.

Both halves already exist and needed no new plumbing:
  * AgentMail  -- ``GET /v0/inboxes`` and per-inbox messages (account-wide key)
  * msgraph    -- ``GET /users/{mailbox}/mailFolders/sentitems/messages`` per seat
  * audit rows -- the ADR 0043 runtime-read seam (``operator/bin/lib/seam_pull.py``)

TWO CHANNELS, ONE MATCHER (ss#2499). This control covered ZERO msgraph seats
until now, and the paying firm sends through msgraph: the service agreement puts
its mail in its own Microsoft 365 tenant, and AgentMail is not used for it at
all. So a foreign send from that mailbox would have met a ledger whose silence
nobody could read either way. The msgraph half reads the seat's own Sent Items
folder, READ-ONLY and scoped to the one mailbox the seat is authored to send
from (agreement 4.6), normalizes each message into the same shape the AgentMail
half produces, and hands it to the SAME two-pass matcher. One matcher on
purpose: two hand-maintained copies of an audit comparison drift silently, and
in the direction of the copy nobody is reading.

MATCHING, three passes:
  1. EXACT. Every audited send records the AgentMail message id -- REPLY_SENT
     carries ``sent_message_id``, REPLY_HELD carries ``message_id`` (121 of 121
     rows observed). This pass needs no tolerance and cannot drift.
     On msgraph the exact key is stronger still, because the broker MINTS it:
     every message it transmits carries an ``X-SMD-Audit-Row`` internet header
     whose value is written onto the same audit row (ss#2499). That join lives on
     the message itself, so it survives the case where the broker could not read
     its own vendor id back -- the header is in the mailbox whether or not the
     lookup worked. A message in Sent Items with NO such header did not come
     through the broker, which is precisely the finding this control is for.
  2. TOOL PATH. ``mcp_agentmail_send_message`` audits as TOOL_CALL_COMPLETED with
     action_class=external_send and records NO message id, so time is the only
     available key. Observed skew is sub-second (341 ms on 2026-08-01), so the
     window is deliberately tight and each audit row is CONSUMED once -- two
     messages can never claim the same row.
  3. BROKER DISPATCH (ss#2499, second live run). The msgraph half's first real
     run reported 14 of 14 sends unaccounted on the paying seat, and all 14 were
     the Operator's own audited replies: the seat predates the header, so its
     rows carry ``sent_message_id: '(sent via msgraph, id unavailable)'`` and
     ``message_id: ''`` -- nothing for pass 1 to join on. Pass 2 could not reach
     them either, because msgraph sends are dispatched by the BROKER and never
     produce a TOOL_CALL_COMPLETED/external_send row. So every legitimate,
     fully-audited msgraph send read as unaudited, which is a control that
     accuses the Operator of everything it did.
     This pass takes the broker's own dispatch rows -- CONFIRM_SEND_DISPATCHED
     with ``outcome == "sent"``, and REPLY_SENT, whose action type IS its
     outcome -- as time candidates, on the SAME window as pass 2 and with the
     same one-row-one-message consumption. A confirm row and the reply row for
     the same send are ONE event seconds apart, so the pair is folded to a
     single candidate before matching; otherwise one reply would account for two
     messages.
     Candidacy is gated on the row carrying NO usable exact key, which is what
     keeps this pass from weakening pass 1. An AgentMail dispatch row records a
     real vendor id and is therefore never a time candidate; a post-header
     msgraph row records ``audit_row_token`` and is not one either. The reported
     ``broker=N`` is the count of sends matched by TIME rather than identity, and
     it is expected to fall to zero on a seat once its overlay stamps the header
     -- it stays reachable only for rows that predate it and for the id lookup
     that could not run (recorded on the row as ``lookup: failed``, ss#2514).

FAIL-CLOSED, THE OTHER WAY. A failed seam read must never read as "zero audit
rows", which would mark every send unaccounted and mute this within a week. A
transport failure HOLDS: it accuses nobody and files no issue, and only a
successful read with unmatched sends is a finding. Same tri-state as
connector_check: absence is a hold, corruption is a page.

A HOLD IS NOT A PASS (ss#2386 review). It exits 2 and reddens the run. The
ss#2258 lesson is that a control must not page on its own blips, which is why a
hold files no issue -- but a hold that exits 0 leaves an unevaluated control
looking identical to a healthy one, and that is how a watchdog sits inert for
weeks. Exit codes: 0 clean, 1 findings, 2 nothing measured, anything else the
control itself broke. The sibling watchdogs hold the same posture
(control-probes.py exits 2 on hold, reconcile-outcomes.py exits 3).

MEMORY (ss#2386). A watchdog with no memory re-reports its own history: this one
filed a fresh P1 every scheduled run for the same 11 finds until five copies of
one finding were open at once, which trains the reader to skim exactly the report
that will one day carry a new send. ``reconcile-sends-baseline.json`` is that
memory -- fingerprints of sends already reported, so a run alerts only on what is
absent from it. The file is COMMITTED and updated by PR because this repo takes
no pushes to main, which also makes silencing a send a reviewed act rather than a
side effect of a scheduled job. Two properties hold the safety:

  * a missing or corrupt baseline yields the EMPTY set, so the failure mode is
    over-reporting, never silence;
  * the baseline can only ever quiet a send it NAMES BY MESSAGE ID, so a new
    send from the same routine, to the same recipient, with the same subject is
    still a finding.

AND THE MSGRAPH HALF INHERITS THAT MEMORY RATHER THAN GROWING ITS OWN (#2345).
It uses the same baseline file, the same fingerprint function and the same
"already reported" arithmetic, keyed on (mailbox, message id) exactly as the
AgentMail half is keyed on (inbox, message id). This is the whole reason the
msgraph adapter could not simply copy today's shape: an acknowledgement-free
watchdog on the PAYING seat would carry the cry-wolf to the mailbox where it
matters most, and the day it carried a real leak it would look like the thirty
days before it.

A FOURTH PHASE VERIFIES THE ACCOUNTED SENDS (outbound-quality track, 2026-08).
Passes 1-3 ask whether every send is accounted for; the fourth asks, of the
accounted ones, whether each body is the one the routine AUTHORED. The logic
lives in ``operator/bin/lib/send_verify.py`` (this file is under the operator
module-size ratchet); the per-skill declaration it reads is
``operator/contracts/send-render.yaml``; the cross-run invariants' committed
expectations are ``operator/bin/send-invariants.json``. Body divergence is a
finding on the same exit-1 path; a missing stamp is a HOLD line -- red, filed
nowhere -- because stamp absence is deployment skew, not an accusation. The
msgraph transport cluster moved verbatim to ``lib/msgraph_channel.py`` in the
same change (re-exported below, so callers and tests read unchanged).

Usage:
    infisical run --env=prod --path=/ss -- python3 operator/bin/reconcile-sends.py
    ... --since 2026-08-01 --json
    ... --no-baseline          # report everything, baseline ignored
    ... --channel msgraph      # one channel only (default: both)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import seam_pull
import send_verify

#: ``_usable_ids`` -- every exact join key a row's metadata offers -- moved to
#: lib/send_attribution.py (claims review 2026-09-04, B7) so the verifier's
#: identity join and this script's exact pass read the SAME keys off a row. The
#: constants it reads (``ID_KEY_SUBSTRING``, ``AUDIT_TOKEN_KEY``,
#: ``UNRESOLVED_ID_PREFIX``) live there too, documented beside it; this script
#: keeps only ``_AUDIT_TOKEN_KEY`` (from msgraph_channel) for its own exact pass.
from send_attribution import _usable_ids

#: ss#2499 -- the msgraph half, factored verbatim into lib/msgraph_channel.py
#: (module-size ratchet). Re-exported here so tests and callers read unchanged.
from msgraph_channel import (
    _GRAPH_MAX_PAGES,  # noqa: F401 — re-export: tests and callers read it from this module (tests pin the page cap)
    AUDIT_ROW_HEADER,  # noqa: F401 — re-export: tests and callers read it from this module
    MsGraphSeat,
    ReconcileError,
    fetch_graph_body,
    graph_token,
    list_sent_msgraph,
    msgraph_seats,
    normalize_graph_message,  # noqa: F401 — re-export: tests and callers read it from this module (tests drive the matcher through it)
)
from msgraph_channel import AUDIT_TOKEN_KEY as _AUDIT_TOKEN_KEY

AGENTMAIL_API_BASE = "https://api.agentmail.to/v0"
_HTTP_TIMEOUT_S = 30.0
_PAGE_LIMIT = 100

#: How far apart an AgentMail send and its tool-path audit row may be and still
#: be the same event. Observed skew is sub-second; this is 5s of headroom, not a
#: tuning knob. Widening it past a send's own cadence would let one row absorb a
#: neighbouring message, so it is asserted in tests rather than left to taste.
TOOL_PATH_WINDOW_S = 5.0

#: Which metadata keys carry a usable exact id (``*message_id*`` and the audit
#: token) and which recorded value is NOT an id (the overlay's "(no id
#: available)" note) are declared beside ``_usable_ids`` in
#: lib/send_attribution.py, the one place both consumers read them.

#: Audit action types the BROKER writes when it has dispatched a message itself
#: (ss#2499). CONFIRM_SEND_DISPATCHED is written for both sends and replies and
#: carries its own outcome; REPLY_SENT is written by the reply plugin and its
#: action type IS the outcome (the failure is a different type, REPLY_FAILED).
_BROKER_DISPATCH_TYPES = ("CONFIRM_SEND_DISPATCHED", "REPLY_SENT")

#: The only ``outcome`` a CONFIRM row may carry and still account for a message
#: that demonstrably left the mailbox. ``refused`` and ``transport_error`` rows
#: exist precisely because the send did NOT go, and a refusal must never be
#: readable as a send.
_DISPATCH_OUTCOME_SENT = "sent"

#: Inboxes that deliberately have no Operator seat behind them, and why. These
#: are OUR OWN rigs on the shared account -- test harnesses, an opposing-counsel
#: simulator, other ventures' mailboxes -- so their sends have no seat ledger to
#: reconcile against and reporting them is noise. The first live run flagged 135
#: such sends across six inboxes; a control that loud gets muted in a week.
#:
#: This is an ALLOWLIST, not a skip-list: an inbox that is NOT here and has no
#: owning seat stays the loudest signal in the report, because that is the shape
#: of a decommissioned seat still sending or a mailbox nobody authored. Adding an
#: entry costs a PR, which is the review gate.
KNOWN_NON_SEAT_INBOXES: dict[str, str] = {
    "ss-probe-admin@agentmail.to": "SS probe harness (inbound driver for seat rehearsals)",
    "ss-probe-runner@agentmail.to": "SS probe harness (runner)",
    "ap-records-standin@agentmail.to": "A&P rehearsal: records-vendor stand-in",
    "ap-client-standin@agentmail.to": "A&P rehearsal: client stand-in",
    "sim-opposing-counsel@agentmail.to": "Halloran Sload LLP opposing-counsel simulator",
    "agentcrane@agentmail.to": "Crane venture mailbox, not an SMD Operator seat",
    "smdcrane@agentmail.to": "Crane SMD Services mailbox, not an Operator seat",
}

#: Exit codes, and the whole contract of this script.
#:
#: EXIT_HOLD is NON-ZERO on purpose (ss#2386 review). A hold still never files an
#: issue and still never accuses anyone -- that half of ss#2258 is unchanged --
#: but it must not be reported as a pass, because an unevaluated control that
#: looks green is how a control sits inert for weeks with nobody noticing. Same
#: posture as the sibling watchdogs: control-probes.py exits 2 on hold,
#: reconcile-outcomes.py exits 3.
EXIT_CLEAN = 0
EXIT_FINDING = 1
EXIT_HOLD = 2

#: Sends this control has already reported, so a scheduled run alerts only on
#: what is new. Committed and PR-updated on purpose (ss#2386, see module header).
DEFAULT_BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconcile-sends-baseline.json")


@dataclass
class InboxReport:
    inbox: str
    slug: str | None
    #: Which mailbox provider this row was read from. Rendered so a reader can
    #: tell at a glance that BOTH halves ran -- a channel that silently stopped
    #: being scanned is the failure this control cannot afford, and an absent
    #: line is much harder to notice than a wrong one.
    channel: str = "agentmail"
    sent_total: int = 0
    matched_exact: int = 0
    matched_tool_path: int = 0
    #: Sends matched by TIME against a broker dispatch row rather than by id
    #: (ss#2499). Reported as its own bucket so a reader can see how much of a
    #: clean run rests on proximity instead of identity -- and so it can be
    #: watched falling to zero as seats pick up the audit header.
    matched_broker: int = 0
    unaccounted: list[dict] = field(default_factory=list)
    baselined: int = 0  # unaccounted, but already reported (ss#2386)
    held: str | None = None  # set when we could not evaluate
    non_seat_reason: str | None = None  # authored as seat-less on purpose
    #: Fourth phase (lib/send_verify.py): body-hash verdicts + cross-run
    #: invariant findings/proposals for THIS inbox's accounted sends. Hashes
    #: only, by construction -- the dataclasses carry no body field. Proposals
    #: are first-seen values for a reviewed send-invariants.json PR and never
    #: count toward is_finding.
    body_verdicts: list = field(default_factory=list)
    invariant_findings: list = field(default_factory=list)
    invariant_proposals: list = field(default_factory=list)

    @property
    def is_finding(self) -> bool:
        if self.held is not None:
            return False
        if self.unaccounted:
            return True
        return send_verify.has_findings(self.body_verdicts, self.invariant_findings)


def _parse_ts(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _agentmail_get(path: str, api_key: str, *, opener=None) -> dict:
    request = urllib.request.Request(AGENTMAIL_API_BASE + path, headers={"Authorization": f"Bearer {api_key}"})  # noqa: S310 - AGENTMAIL_API_BASE is an https constant; the path is built in this module
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=_HTTP_TIMEOUT_S) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReconcileError(f"agentmail GET {path} failed: {exc}") from exc


def list_inboxes(api_key: str, *, opener=None) -> list[str]:
    parsed = _agentmail_get("/inboxes", api_key, opener=opener)
    inboxes = parsed.get("inboxes") if isinstance(parsed, dict) else None
    if not isinstance(inboxes, list):
        raise ReconcileError("agentmail /inboxes returned no list")
    return [i["inbox_id"] for i in inboxes if isinstance(i, dict) and i.get("inbox_id")]


def list_sent(inbox: str, api_key: str, *, since: datetime | None = None, opener=None) -> list[dict]:
    """Every message this inbox has SENT, newest-first, paged."""
    out: list[dict] = []
    token: str | None = None
    while True:
        path = f"/inboxes/{urllib.parse.quote(inbox)}/messages?limit={_PAGE_LIMIT}"
        if token:
            path += "&page_token=" + urllib.parse.quote(token)
        page = _agentmail_get(path, api_key, opener=opener)
        messages = page.get("messages") or []
        for message in messages:
            if "sent" not in (message.get("labels") or []):
                continue
            if since and _parse_ts(message.get("timestamp")) < since:
                continue
            out.append(message)
        token = page.get("next_page_token")
        if not token or not messages:
            return out


def reconcile_mailbox(
    seat: MsGraphSeat,
    since,
    *,
    opener=None,
    client_factory=seam_pull.seam_client_from_env,
    baseline: set[str] | None = None,
    secret: str | None = None,
    verifier: "send_verify.SendVerifier | None" = None,
) -> InboxReport:
    """The msgraph twin of ``reconcile_inbox``, on the same tri-state.

    One difference, and it is a simplification: the owning seat is KNOWN here.
    An msgraph mailbox is reached only because a customer.yaml names it, so the
    "inbox with no owning seat" case the AgentMail half treats as its loudest
    signal cannot arise on this channel -- there is no account-wide key that
    could show us somebody else's mailbox.
    """
    report = InboxReport(inbox=seat.mailbox or seat.slug, slug=seat.slug, channel="msgraph")
    if not (seat.mailbox and seat.tenant_id and seat.client_id):
        report.held = (
            f"{seat.slug}: customer.yaml authors adapter msgraph but not a complete "
            "msgraph_auth (mailbox/tenant_id/client_id)"
        )
        return report
    key = secret if secret is not None else os.environ.get(seat.secret_env)
    if not key:
        report.held = f"{seat.slug}: {seat.secret_env} unset (run under infisical)"
        return report
    try:
        token = graph_token(seat, key, opener=opener)
        sent = list_sent_msgraph(seat, token, since=since, opener=opener)
    except ReconcileError as exc:
        report.held = str(exc)
        return report
    report.sent_total = len(sent)
    if not sent:
        return report

    client = client_factory(seat.slug)
    if client is None:
        report.held = f"no runtime-read client for {seat.slug} (seam env incomplete)"
        return report
    try:
        rows = client.read_all("audit_export")
    except Exception as exc:  # noqa: BLE001 — any transport failure HOLDS
        report.held = f"audit_export read failed for {seat.slug}: {exc}"
        return report
    if not rows:
        report.held = f"audit_export returned no rows for {seat.slug}"
        return report

    exact, tool_path, broker, unaccounted = reconcile(sent, rows)
    report.matched_exact = exact
    report.matched_tool_path = tool_path
    report.matched_broker = broker
    report.unaccounted, report.baselined = split_baselined(report.inbox, unaccounted, baseline or set())
    if verifier is not None:
        # Phase 4 (lib/send_verify.py). The body fetcher is per-message, only
        # ever invoked for hash-verified routines, and its result flows into
        # canonical_body_sha256 and nowhere else.
        def _fetch(message: dict):
            return fetch_graph_body(seat, token, str(message.get("graph_id") or ""), opener=opener)

        report.body_verdicts, report.invariant_findings, report.invariant_proposals = verifier.verify_inbox(
            sent, rows, _fetch
        )
    return report


def _metadata(row: dict) -> dict:
    raw = row.get("metadata")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _is_broker_dispatch(row: dict, meta: dict) -> bool:
    """Did the BROKER transmit a message for this row, with no id to prove it?

    Two halves, and both matter. The action type says a message went out: a
    CONFIRM row must additionally say ``outcome: sent``, because the same type's
    siblings record a refusal and a transport error and neither of those sent
    anything. REPLY_SENT needs no outcome -- its type is the outcome, and its
    failure is a different type entirely.

    The second half is what keeps this pass from weakening the exact one: a row
    that recorded ANY usable id is not a time candidate. That row is already
    joinable by identity, so letting it also be claimed by proximity would let a
    legitimate send launder an unaudited neighbour. It also means this pass
    empties itself out: once a seat's overlay stamps the audit header, its rows
    carry a usable key and stop being candidates at all.
    """
    action = row.get("action_type")
    if action not in _BROKER_DISPATCH_TYPES:
        return False
    if action == "CONFIRM_SEND_DISPATCHED" and meta.get("outcome") != _DISPATCH_OUTCOME_SENT:
        return False
    return not _usable_ids(meta)


def _fold_broker_pairs(candidates: list[dict]) -> list[dict]:
    """One send, one candidate.

    A msgraph reply writes TWO rows -- the broker's CONFIRM_SEND_DISPATCHED and
    the reply plugin's REPLY_SENT -- describing one event seconds apart. Left
    unfolded they would account for two messages, so one legitimate reply could
    absorb an unaudited send beside it, which is the absorption failure this
    control cannot have.

    Folding is CROSS-TYPE only, and each row may be folded once: two confirms in
    the same second are two sends, not one, and must stay two candidates.
    """
    kept: list[dict] = []
    for candidate in sorted(candidates, key=lambda c: c["ts"]):
        twin = next(
            (
                other
                for other in kept
                if other["kind"] != candidate["kind"]
                and not other["paired"]
                and abs((candidate["ts"] - other["ts"]).total_seconds()) <= TOOL_PATH_WINDOW_S
            ),
            None,
        )
        if twin is not None:
            twin["paired"] = True
            continue
        kept.append(candidate)
    return kept


def index_audit(rows: list[dict]) -> tuple[set[str], list[dict], list[dict]]:
    """Split audit rows into (exact keys, tool-path send rows, broker dispatches).

    The key set is deliberately a UNION rather than a per-channel switch. It
    holds every vendor message id a row recorded (any metadata key containing
    ``message_id``) and, since ss#2499, every ``audit_row_token`` -- the value of
    the header the broker stamped on the message itself. A msgraph send matches
    on either, and matching on either is what makes the join survive a broker
    that transmitted fine and could not read its own vendor id back afterwards.
    The two key spaces cannot collide: one is an RFC2822/vendor id, the other a
    26-character ULID.

    The third list is the ss#2499 second-run fix. A msgraph send is dispatched by
    the broker and produces no TOOL_CALL_COMPLETED row, so on a seat whose rows
    predate the audit header there is nothing for either existing pass to reach
    and every audited send read as unaudited. The broker's own dispatch rows are
    that seat's only evidence, and time is the only key they offer.
    """
    known_ids: set[str] = set()
    tool_sends: list[dict] = []
    broker_sends: list[dict] = []
    for row in rows:
        meta = _metadata(row)
        known_ids |= _usable_ids(meta)
        if (
            row.get("action_type") == "TOOL_CALL_COMPLETED"
            and meta.get("action_class") == "external_send"
            and meta.get("outcome") == "ok"
        ):
            tool_sends.append({"ts": _parse_ts(row["ts"]), "claimed": False})
        elif _is_broker_dispatch(row, meta):
            broker_sends.append(
                {
                    "ts": _parse_ts(row["ts"]),
                    "kind": row.get("action_type"),
                    "paired": False,
                    "claimed": False,
                }
            )
    return known_ids, tool_sends, _fold_broker_pairs(broker_sends)


def _claim(candidates: list[dict], stamp: datetime) -> bool:
    """Consume the oldest unclaimed candidate within the window, if any.

    CONSUMED, not merely matched: two messages can never claim the same row, so
    one audited send never accounts for an unaudited one beside it.
    """
    claim = next(
        (
            candidate
            for candidate in candidates
            if not candidate["claimed"] and abs((candidate["ts"] - stamp).total_seconds()) <= TOOL_PATH_WINDOW_S
        ),
        None,
    )
    if claim is None:
        return False
    claim["claimed"] = True
    return True


def reconcile(sent: list[dict], rows: list[dict]) -> tuple[int, int, int, list[dict]]:
    """Return (matched_exact, matched_tool_path, matched_broker, unaccounted)."""
    known_ids, tool_sends, broker_sends = index_audit(rows)

    remaining = [
        m for m in sent if m.get("message_id") not in known_ids and (m.get(_AUDIT_TOKEN_KEY) or "\x00") not in known_ids
    ]
    matched_exact = len(sent) - len(remaining)

    unaccounted: list[dict] = []
    matched_tool = 0
    matched_broker = 0
    # Oldest-first so the pairing is deterministic regardless of page order.
    for message in sorted(remaining, key=lambda m: str(m.get("timestamp") or "")):
        stamp = _parse_ts(message.get("timestamp"))
        if _claim(tool_sends, stamp):
            matched_tool += 1
        elif _claim(broker_sends, stamp):
            matched_broker += 1
        else:
            unaccounted.append(message)
    return matched_exact, matched_tool, matched_broker, unaccounted


def fingerprint(inbox: str, message: dict) -> str:
    """Stable identity of one send.

    The AgentMail message id is assigned by the sending infrastructure and is
    never reused, so it alone would identify the send; the inbox rides along so a
    baseline entry can never reach across mailboxes. Deliberately NOT derived
    from subject, recipient or day: every one of those repeats on the next run of
    the same routine, and a fingerprint that repeats is a fingerprint that
    silences a new send.
    """
    message_id = message.get("message_id")
    if message_id:
        return f"{inbox}|{message_id}"
    # No id at all has never been observed on a sent message. Fall back to the
    # exact millisecond timestamp rather than to anything coarser, and keep the
    # marker so the two key spaces can never collide.
    return f"{inbox}|ts:{message.get('timestamp')}"


def load_baseline(path: str | None = None) -> set[str]:
    """Fingerprints of sends already reported.

    A missing, unreadable or malformed baseline returns the EMPTY set ON PURPOSE.
    This file's only failure mode must be over-reporting: a baseline that fails
    open would turn a deleted or corrupted JSON file into a silent watchdog,
    which is the exact failure ss#2258 exists to prevent.
    """
    try:
        with open(path or DEFAULT_BASELINE_PATH, encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError):
        return set()
    entries = parsed.get("dispositioned") if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list):
        return set()
    return {
        fingerprint(entry["inbox"], entry)
        for entry in entries
        if isinstance(entry, dict) and entry.get("inbox") and (entry.get("message_id") or entry.get("timestamp"))
    }


def split_baselined(inbox: str, messages: list[dict], baseline: set[str]) -> tuple[list[dict], int]:
    """Return (sends to report, count already reported)."""
    if not baseline:
        return list(messages), 0
    fresh = [m for m in messages if fingerprint(inbox, m) not in baseline]
    return fresh, len(messages) - len(fresh)


def finding_digest(reports: list[InboxReport]) -> str:
    """A stable key for THIS SET of findings, carried in the issue body so the
    workflow can recognise its own report and decline to file it twice.

    The baseline only quiets a find once its PR has merged; between the first
    report and that merge, this is what keeps the second, third and fifth issue
    from being opened. It is derived from the fingerprints themselves, so one
    genuinely new send changes the digest and a new issue still opens.
    """
    keys = sorted(
        [
            fingerprint(report.inbox, message)
            for report in reports
            if report.is_finding
            for message in report.unaccounted
        ]
        # Phase 4: body/invariant finding keys join the same fingerprint, so
        # the existing issue-dedupe machinery covers the new classes with zero
        # workflow changes.
        + [
            key
            for report in reports
            for key in send_verify.digest_keys(report.inbox, report.body_verdicts, report.invariant_findings)
        ]
    )
    if not keys:
        return ""
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def exit_code(reports: list[InboxReport]) -> int:
    """0 clean, 1 findings, 2 nothing measured.

    A finding outranks a hold so the issue still gets filed when both are true;
    the workflow reddens the run off the HOLD lines in the report, not off this
    code, so a hold can never be lost behind a finding.
    """
    if any(report.is_finding for report in reports):
        return EXIT_FINDING
    if any(report.held for report in reports):
        return EXIT_HOLD
    if any(send_verify.has_holds(report.body_verdicts) for report in reports):
        # A templated send whose stamp is missing reddens without accusing
        # (deployment skew, not a finding). The workflow fails the run off the
        # HOLD lines send_verify.render_lines printed.
        return EXIT_HOLD
    return EXIT_CLEAN


def baseline_entries(reports: list[InboxReport]) -> list[dict]:
    """The findings, shaped as baseline rows a human can paste into a PR."""
    return [
        {
            "inbox": report.inbox,
            "channel": report.channel,
            "message_id": message.get("message_id"),
            "timestamp": message.get("timestamp"),
            "to": ", ".join(message.get("to") or []),
            "subject": str(message.get("subject") or ""),
            "reported_in": None,
        }
        for report in reports
        if report.is_finding
        for message in sorted(report.unaccounted, key=lambda m: str(m.get("timestamp") or ""))
    ]


def slug_for_inbox(inbox: str, slugs: list[str]) -> str | None:
    """The seat that owns this inbox, by local part. None ⇒ nobody owns it, which
    is a finding in itself rather than a reason to skip the inbox."""
    local = inbox.split("@", 1)[0].lower()
    return next((s for s in slugs if s.lower() == local), None)


def reconcile_inbox(
    inbox: str,
    slugs: list[str],
    api_key: str,
    since,
    *,
    opener=None,
    client_factory=seam_pull.seam_client_from_env,
    baseline: set[str] | None = None,
    verifier: "send_verify.SendVerifier | None" = None,
) -> InboxReport:
    slug = slug_for_inbox(inbox, slugs)
    report = InboxReport(inbox=inbox, slug=slug)
    if slug is None:
        # Label it before any early return, so a quiet rig still reads as a rig
        # rather than as an unowned mailbox.
        report.non_seat_reason = KNOWN_NON_SEAT_INBOXES.get(inbox)
    try:
        sent = list_sent(inbox, api_key, since=since, opener=opener)
    except ReconcileError as exc:
        report.held = str(exc)
        return report
    report.sent_total = len(sent)
    if not sent:
        return report

    if slug is None:
        if report.non_seat_reason:
            # Authored as seat-less on purpose: our own rig. Counted and named in
            # the report, but not a finding -- there is no ledger it could fail
            # to appear in.
            return report
        # Nobody owns this inbox and nobody authored it as ours. No audit log can
        # account for its sends, and that is exactly the shape of a decommissioned
        # seat still sending, or a mailbox somebody stood up unrecorded. This is
        # the case a seat-side check could never represent, so it stays loud.
        report.unaccounted, report.baselined = split_baselined(inbox, sent, baseline or set())
        return report

    client = client_factory(slug)
    if client is None:
        report.held = f"no runtime-read client for {slug} (seam env incomplete)"
        return report
    try:
        rows = client.read_all("audit_export")
    except Exception as exc:  # noqa: BLE001 — any transport failure HOLDS
        report.held = f"audit_export read failed for {slug}: {exc}"
        return report
    if not rows:
        # Distinguishable from "read fine, found nothing": a seat with sends but a
        # literally empty ledger is unmeasurable, not clean.
        report.held = f"audit_export returned no rows for {slug}"
        return report

    exact, tool_path, broker, unaccounted = reconcile(sent, rows)
    report.matched_exact = exact
    report.matched_tool_path = tool_path
    report.matched_broker = broker
    # Baselining is the LAST step, applied to sends the audit log genuinely does
    # not account for. A held inbox returns above and can never be quieted by it:
    # "already reported" is a statement about a finding, and a hold is not one.
    report.unaccounted, report.baselined = split_baselined(inbox, unaccounted, baseline or set())
    if verifier is not None:
        # Phase 4 (lib/send_verify.py). Per-message fetch, hash-verified
        # routines only; the body never leaves the verify call.
        def _fetch(message: dict):
            message_id = urllib.parse.quote(str(message.get("message_id") or ""))
            parsed = _agentmail_get(
                f"/inboxes/{urllib.parse.quote(inbox)}/messages/{message_id}", api_key, opener=opener
            )
            text = parsed.get("text") if isinstance(parsed, dict) else None
            return text if isinstance(text, str) else None

        report.body_verdicts, report.invariant_findings, report.invariant_proposals = verifier.verify_inbox(
            sent, rows, _fetch
        )
    return report


def render(reports: list[InboxReport]) -> str:
    lines: list[str] = []
    findings = [r for r in reports if r.is_finding]
    held = [r for r in reports if r.held]
    for report in reports:
        if report.held:
            lines.append(f"HOLD  {report.inbox}: {report.held}")
            continue
        if report.non_seat_reason:
            lines.append(f"n/a   {report.inbox} sent={report.sent_total} — not a seat: {report.non_seat_reason}")
            continue
        owner = report.slug or "UNOWNED"
        lines.append(
            f"{'FIND' if report.is_finding else 'ok  '}  {report.inbox} [{owner}] "
            f"({report.channel}) sent={report.sent_total} exact={report.matched_exact} "
            f"tool={report.matched_tool_path} broker={report.matched_broker} "
            f"unaccounted={len(report.unaccounted)} "
            f"already-reported={report.baselined}"
        )
        for message in sorted(report.unaccounted, key=lambda m: str(m.get("timestamp") or "")):
            lines.append(
                f"        {message.get('timestamp')} -> "
                f"{','.join(message.get('to') or [])}  {str(message.get('subject'))[:72]}"
            )
        lines.extend(
            send_verify.render_lines(
                report.inbox,
                report.body_verdicts,
                report.invariant_findings,
                report.invariant_proposals,
            )
        )
    lines.append("")
    scanned_channels = ", ".join(sorted({r.channel for r in reports})) or "none"
    lines.append(
        f"{len(findings)} inbox(es) with unaccounted sends, {len(held)} held, "
        f"{len(reports)} scanned across [{scanned_channels}], "
        f"{sum(r.baselined for r in reports)} already reported "
        f"(operator/bin/reconcile-sends-baseline.json)"
    )
    digest = finding_digest(reports)
    if digest:
        # Printed so the workflow can read it back out of the report and carry it
        # into the issue body: the key that keeps one find from filing five
        # issues while its baseline PR is still open.
        lines.append(f"reconcile-fingerprint: {digest}")
        lines.append("")
        lines.append(
            "Once dispositioned, add these to operator/bin/reconcile-sends-baseline.json "
            "in a PR (fill reported_in with the issue number) so this stops being re-reported:"
        )
        lines.append(json.dumps(baseline_entries(reports), indent=2))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO date; only consider sends at/after this")
    parser.add_argument("--days", type=int, help="only consider sends in the last N days")
    parser.add_argument("--inbox", action="append", help="limit to these inboxes")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--baseline", help=f"path to the already-reported baseline (default {DEFAULT_BASELINE_PATH})")
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="report every unaccounted send, including ones already reported",
    )
    parser.add_argument(
        "--channel",
        choices=("all", "agentmail", "msgraph"),
        default="all",
        help="which mailbox provider(s) to reconcile (default: all)",
    )
    args = parser.parse_args(argv)

    since = None
    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
    elif args.since:
        since = _parse_ts(args.since if "T" in args.since else args.since + "T00:00:00Z")

    slugs = sorted(d for d in os.listdir(_customers_dir()) if not d.startswith("_") and not d.startswith("."))

    baseline = set() if args.no_baseline else load_baseline(args.baseline)
    try:
        verifier = send_verify.verifier_from_contract()
    except Exception as exc:  # noqa: BLE001 — a broken contract is the control itself broken
        # Not a hold and not a finding: exit >2 is the "control broke" lane the
        # workflow fails on directly. send-render.yaml is repo-committed and
        # CI-parsed, so this firing means the tree is inconsistent.
        print(f"send-render contract failed to load: {exc}", file=sys.stderr)
        return 4
    reports: list[InboxReport] = []

    # ONE CHANNEL'S FAILURE IS NOT THE RUN'S (ss#2499). A missing AgentMail key
    # used to return EXIT_HOLD from here, before anything else ran. With a second
    # channel that would mean an unrelated missing secret silences the control on
    # the PAYING seat, so the miss is recorded as a hold for its own channel and
    # every other mailbox is still scanned.
    if args.channel in ("all", "agentmail"):
        reports.extend(_reconcile_agentmail(args, slugs, since, baseline, verifier))
    if args.channel in ("all", "msgraph"):
        reports.extend(_reconcile_msgraph(args, since, baseline, verifier))

    if args.json:
        print(json.dumps([report_dict(r) for r in reports], indent=2))
    else:
        print(render(reports))

    return exit_code(reports)


def report_dict(r: InboxReport) -> dict:
    """--json shape for one inbox. Field-by-field on purpose: what is listed
    here is ALL that can leave the process, and the phase-4 halves come from
    send_verify.as_dicts, whose own emission is hash-only by construction."""
    body_verdicts, invariant_findings, invariant_proposals = send_verify.as_dicts(
        r.body_verdicts, r.invariant_findings, r.invariant_proposals
    )
    return {
        "inbox": r.inbox,
        "slug": r.slug,
        "channel": r.channel,
        "sent_total": r.sent_total,
        "matched_exact": r.matched_exact,
        "matched_tool_path": r.matched_tool_path,
        "matched_broker": r.matched_broker,
        "baselined": r.baselined,
        "held": r.held,
        "unaccounted": [
            {
                "message_id": m.get("message_id"),
                "timestamp": m.get("timestamp"),
                "to": m.get("to"),
                "subject": m.get("subject"),
            }
            for m in r.unaccounted
        ],
        "body_verdicts": body_verdicts,
        "invariant_findings": invariant_findings,
        "invariant_proposals": invariant_proposals,
        # Two counted metrics (claims review 2026-09-04, B3): before the overlay
        # pin that writes the skill_name column, attributed_by_hash > 0; after
        # it, attributed_by_skill > 0 and attributed_by_hash == 0. A column
        # regression is a number moving here, not a detail string going quiet.
        **send_verify.attribution_counts(r.body_verdicts),
    }


def _reconcile_agentmail(args, slugs, since, baseline: set[str], verifier=None) -> list[InboxReport]:
    """The AgentMail half, unchanged in behaviour and now able to hold alone."""
    api_key = os.environ.get("AGENTMAIL_API_KEY")
    if not api_key:
        return [
            InboxReport(
                inbox="agentmail",
                slug=None,
                held="AGENTMAIL_API_KEY unset (run under infisical)",
            )
        ]
    try:
        inboxes = args.inbox or list_inboxes(api_key)
    except ReconcileError as exc:
        return [InboxReport(inbox="agentmail", slug=None, held=str(exc))]
    return [reconcile_inbox(i, slugs, api_key, since, baseline=baseline, verifier=verifier) for i in inboxes]


def _reconcile_msgraph(args, since, baseline: set[str], verifier=None) -> list[InboxReport]:
    """The msgraph half: every seat whose authored mail adapter is msgraph.

    ``--inbox`` narrows this the same way it narrows the AgentMail half, matching
    on the mailbox address or the seat slug so an operator can aim a dispatch run
    at one seat without learning a second flag.

    NO SEATS IS A HOLD, not a pass. Zero msgraph seats means either the file
    layout moved or the parse failed, and a control that reports "all clear" on a
    channel it could not enumerate is the exact shape of the gap this closes.
    """
    seats = msgraph_seats()
    if args.inbox:
        wanted = {str(i).lower() for i in args.inbox}
        seats = [s for s in seats if s.mailbox.lower() in wanted or s.slug.lower() in wanted]
        if not seats:
            return []
    if not seats:
        return [
            InboxReport(
                inbox="msgraph",
                slug=None,
                channel="msgraph",
                held="no seat authors adapter msgraph; the channel was not evaluated",
            )
        ]
    return [reconcile_mailbox(seat, since, baseline=baseline, verifier=verifier) for seat in seats]


def _customers_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "customers")


if __name__ == "__main__":
    raise SystemExit(main())
