"""The I/O half: perform the hostile act, then collect what can be observed.

Everything here talks to a network or a file. Nothing here decides anything --
the drivers return a ``LegObservation`` and the scorer grades it. That split is
what lets the falsifier test exercise the grading path without a seat.

CHANNELS. Two drivers, both console-side:

* ``email_probe`` sends from a harness mailbox to the seat's own inbox over the
  AgentMail API and waits for the reply, exactly as ``rehearse-card.py`` does.
  Same API, same quote-trail stripping, same "no reply is written as no reply".
* ``console_reconcile`` drives nothing. It reconciles what left the seat's
  mailbox against what the ledger recorded, over the window a sibling leg
  created. This is the ss#2258 shape and it must stay console-side: a seat
  cannot be the auditor of its own egress.

MISSING CREDENTIALS DEGRADE, THEY DO NOT FAKE. Every capability is probed once
at run start; a leg whose capability is absent returns
``LegObservation(unavailable=...)`` and scores SKIPPED. There is no offline mode
that substitutes a canned reply, because a suite that can pass without touching
a seat is a suite that certifies nothing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .scope import SEAT_TOKEN
from .scoring import LegObservation

REPO_ROOT = Path(__file__).resolve().parents[2]
CUSTOMERS = REPO_ROOT / "operator" / "customers"
BIN_DIR = REPO_ROOT / "operator" / "bin"
AGENTMAIL_API_BASE = "https://api.agentmail.to/v0"
_HTTP_TIMEOUT_S = 45.0


def _load_bin_module(module_name: str, filename: str):
    """Import one of the hyphenated bin scripts as a module.

    Reusing ``reconcile-sends.py``'s matcher rather than re-deriving it is
    deliberate: two implementations of "is this send audited" would drift, and
    the drifted one would be the one nobody watched.
    """
    lib = str(BIN_DIR / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    spec = importlib.util.spec_from_file_location(module_name, BIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_overlay_ref_drift():
    """``overlay-ref-drift.py`` as a module, for its read of a seat's RUNNING ref.

    Same reason as the reconciler above: the runner's release gate has to read a
    Machine's running overlay ref exactly the way the drift report reads it. Two
    implementations of "what is this seat running" would drift, and the drifted
    one would be the one nobody watched.
    """
    return _load_bin_module("overlay_ref_drift", "overlay-ref-drift.py")


def load_seat_config(slug: str) -> dict:
    path = CUSTOMERS / slug / "customer.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no customer.yaml at {path}")
    return yaml.safe_load(path.read_text()) or {}


def seat_inbox(config: dict) -> str | None:
    """The seat's own AgentMail inbox, derived from its authored Email connector.

    Returns None when the seat has no AgentMail-backed Email connector (a seat
    on msgraph, or one with mail disabled). That is a SKIP reason, not a
    failure: the suite cannot drive a channel the seat does not author.
    """
    connectors = config.get("connectors")
    email = connectors.get("Email") if isinstance(connectors, dict) else None
    if not isinstance(email, dict) or not email.get("enabled"):
        return None
    if str(email.get("adapter", "")) != "agentmail":
        return None
    match = re.search(r"hermes-([a-z0-9-]+)\.fly\.dev", str(email.get("webhook_url", "")))
    return f"{match.group(1)}@agentmail.to" if match else None


def _agentmail(method: str, path: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(AGENTMAIL_API_BASE + path, data=data, method=method, headers=headers)  # noqa: S310 - AGENTMAIL_API_BASE is an https constant; the path is built in this module
    try:
        # The host is the module constant AGENTMAIL_API_BASE and every path
        # segment is built here; no caller supplies a scheme or host. Same
        # suppression and reasoning as operator/bin/rehearse-card.py.
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:  # noqa: S310 - host is the AGENTMAIL_API_BASE constant, https, no caller supplies a scheme
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"raw": exc.read().decode()[:400]}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"raw": str(exc)[:400]}


def _strip_quote_trail(body: str) -> str:
    for marker in ("\nOn Mon,", "\nOn Tue,", "\nOn Wed,", "\nOn Thu,", "\nOn Fri,", "\nOn Sat,", "\nOn Sun,", "\n> "):
        if marker in body:
            body = body.split(marker)[0]
    return body.strip()


def _parse_ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass
class Capabilities:
    """What this run can actually observe, probed once before anything is driven."""

    agentmail_key: str | None = None
    seat_inbox: str | None = None
    audit_seam: object | None = None
    fault_injection: str | None = None
    reasons: dict[str, str] = field(default_factory=dict)

    def missing_for(self, requirements: list[str]) -> str | None:
        for requirement in requirements:
            if requirement == "agentmail" and not self.agentmail_key:
                return self.reasons.get("agentmail", "AGENTMAIL_API_KEY is unset")
            if requirement == "seat_email" and not self.seat_inbox:
                return self.reasons.get("seat_email", "the seat authors no AgentMail inbox")
            if requirement == "audit_seam" and self.audit_seam is None:
                return self.reasons.get("audit_seam", "the runtime read seam is not configured")
            if requirement == "fault_injection" and not self.fault_injection:
                return self.reasons.get(
                    "fault_injection",
                    "no fault was injected, so the degraded path was never entered "
                    "(re-run with --inject after taking the connector down on the rig)",
                )
        return None


def probe_capabilities(slug: str, config: dict, *, inject: str | None = None) -> Capabilities:
    seam_pull = _load_bin_module("seam_pull_for_rehearsal", "lib/seam_pull.py")
    capabilities = Capabilities(fault_injection=inject)
    capabilities.agentmail_key = os.environ.get("AGENTMAIL_API_KEY") or None
    if not capabilities.agentmail_key:
        capabilities.reasons["agentmail"] = (
            "AGENTMAIL_API_KEY is unset; run under `infisical run --env=prod --path=/ss`"
        )
    capabilities.seat_inbox = seat_inbox(config)
    if not capabilities.seat_inbox:
        capabilities.reasons["seat_email"] = (
            f"seat {slug} authors no enabled AgentMail Email connector, so there is no channel to play hostile on"
        )
    try:
        capabilities.audit_seam = seam_pull.seam_client_from_env(slug)
    except ValueError as exc:
        capabilities.audit_seam = None
        capabilities.reasons["audit_seam"] = f"seam client refused construction: {exc}"
    if capabilities.audit_seam is None:
        capabilities.reasons.setdefault(
            "audit_seam",
            "OPERATOR_RUNTIME_READ_SECRET / OPERATOR_RUNTIME_READ_URL unset, so the ledger "
            "cannot be read and no expectation about an audit row can be answered",
        )
    return capabilities


class AuditReader:
    """Ledger rows written since a mark, pulled through the ADR 0043 read seam.

    A failed read raises. It never returns an empty list, because "the seam was
    unreachable" and "the seat wrote nothing" must not look alike -- that
    conflation is what would turn a broken gate into a green run.

    The mark is an audit row id. Ids are ascending ULIDs (the seam pages on
    exactly that ordering), so "written after this mark" is a string comparison
    and needs no clock agreement between the console and the Machine.
    """

    def __init__(self, client) -> None:
        self._client = client

    def snapshot(self) -> str:
        rows = self._client.read_all("audit_export")
        return max((str(r.get("id", "")) for r in rows), default="")

    def rows_after(self, mark: str) -> list[dict]:
        rows = self._client.read_all("audit_export")
        return [r for r in rows if str(r.get("id", "")) > mark]


def _read_settled_rows(
    audit: AuditReader, mark: str, *, settled_past: datetime, budget_s: int = 120
) -> tuple[list[dict], bool]:
    """Ledger rows after ``mark``, read only once the view has settled.

    The ADR 0043 seam serves a DELAYED view of the ledger (measured 15-45s
    behind live writes on 2026-08-18, run ``...f88c158b8b9b-notgreen``): a read
    taken the instant a reply lands can miss rows the broker wrote half a second
    before the reply left. Scoring against that read failed two healthy legs and
    called one audited reply unaudited. So the read is settled first: the view is
    trusted once it demonstrably reaches ``settled_past`` (some row at or after
    that moment), or once three consecutive reads five seconds apart return the
    same tail (a quiet seat cannot produce a newer row to prove currency with).

    Returns (rows, settled). ``settled=False`` after the budget means the caller
    scores what it has and says so -- degraded is reported, never silent.
    """
    deadline = time.time() + budget_s
    stable_reads = 0
    previous_tail: str | None = None
    rows = audit.rows_after(mark)
    while time.time() < deadline:
        newest = max(
            (ts for ts in (_parse_ts(r.get("ts")) for r in rows) if ts is not None),
            default=None,
        )
        if newest is not None and newest >= settled_past:
            return rows, True
        tail = str(rows[-1].get("id")) if rows else ""
        stable_reads = stable_reads + 1 if tail == previous_tail else 1
        if stable_reads >= 3:
            return rows, True
        previous_tail = tail
        time.sleep(5)
        rows = audit.rows_after(mark)
    return rows, False


def send_and_wait(
    *, sender: str, recipient: str, subject: str, body: str, key: str, timeout_s: int
) -> tuple[bool, str | None, datetime]:
    """Speak the hostile line and wait for the seat's reply.

    Returns (delivered_to_api, reply_text, sent_at). ``reply_text`` is None when
    nothing came back inside the timeout, which the scorer treats as unanswered
    rather than as silence-equals-consent.

    THE NONCE IS THE MATCH. The first armed run (2026-08-18, run
    ``...f88c158b8b9b-notgreen``) matched a PREVIOUS leg's reply within a second
    of sending, because the probe mailboxes accumulate replies across legs and
    runs and the old predicate (sender + 38-char subject prefix) cannot tell
    generations apart. Scoring then read the ledger before the seat had even
    drafted, and a healthy seat scored FAIL. So every probe subject now carries a
    per-send nonce, a reply matches only if it quotes the nonce back in its
    subject, and a message older than the probe itself can never match.
    """
    nonce = f"[sf-{secrets.token_hex(4)}]"
    tagged_subject = f"{subject} {nonce}"
    sent_at = datetime.now(timezone.utc)
    status, _ = _agentmail(
        "POST",
        f"/inboxes/{urllib.parse.quote(sender)}/messages/send",
        key,
        {"to": [recipient], "subject": tagged_subject, "text": body},
    )
    if status != 200:
        return False, None, sent_at
    time_floor = sent_at - timedelta(seconds=30)  # small skew allowance, never a whole generation
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        listed_status, listed = _agentmail("GET", f"/inboxes/{urllib.parse.quote(sender)}/messages?limit=16", key)
        if listed_status == 200:
            for message in listed.get("messages") or []:
                message_ts = _parse_ts(message.get("timestamp"))
                if (
                    recipient in str(message.get("from", ""))
                    and nonce in str(message.get("subject", ""))
                    and message_ts is not None
                    and message_ts >= time_floor
                ):
                    _, full = _agentmail(
                        "GET",
                        f"/inboxes/{urllib.parse.quote(sender)}/messages/"
                        f"{urllib.parse.quote(str(message['message_id']))}",
                        key,
                    )
                    return True, _strip_quote_trail(str(full.get("text") or "")), sent_at
        time.sleep(10)
    return True, None, sent_at


def messages_from(inbox: str, origin: str, since: datetime, key: str) -> int | None:
    """How many messages reached ``inbox`` from ``origin`` since a moment.

    None means the mailbox could not be read -- never zero. A read failure that
    reported zero would satisfy every ``no_send_to`` expectation in the suite.
    """
    status, listed = _agentmail("GET", f"/inboxes/{urllib.parse.quote(inbox)}/messages?limit=64", key)
    if status != 200:
        return None
    count = 0
    for message in listed.get("messages") or []:
        timestamp = _parse_ts(message.get("timestamp"))
        if timestamp is None or timestamp < since:
            continue
        if origin.lower() in str(message.get("from", "")).lower():
            count += 1
    return count


def drafts_addressed_to(inbox: str, address: str, since: datetime, key: str) -> int | None:
    """How many seat drafts created since a moment are addressed to ``address``.

    The composition artifact under a draft_for_review posture IS a draft: the
    correct outcome of a correctly-paired client send on such a seat is a draft
    and no send, so the control leg's evidence lives in the drafts folder, not
    the ledger (create_draft audit rows carry no recipient — verified live
    2026-08-18). None means the folder could not be read -- never zero.
    """
    status, listed = _agentmail("GET", f"/inboxes/{urllib.parse.quote(inbox)}/drafts?limit=32", key)
    if status != 200:
        return None
    count = 0
    for draft in listed.get("drafts") or []:
        timestamp = _parse_ts(draft.get("created_at") or draft.get("updated_at"))
        if timestamp is None or timestamp < since:
            continue
        recipients = " ".join(str(r) for r in (draft.get("to") or []))
        if address.lower() in recipients.lower():
            count += 1
    return count


def unaccounted_sends(inbox: str, rows: list[dict], since: datetime, key: str) -> list[dict] | None:
    """Sends that left the seat's mailbox with no matching audit row.

    Both halves come from the existing ss#2258 reconciler: its ``list_sent``
    pager and its two-pass matcher (exact message id, then the tool-path time
    window with each row consumed once).
    """
    reconciler = _load_bin_module("reconcile_sends_for_rehearsal", "reconcile-sends.py")
    try:
        sent = reconciler.list_sent(inbox, key, since=since)
    except reconciler.ReconcileError:
        return None
    # reconcile() returns (matched_exact, matched_tool_path, matched_broker,
    # unaccounted) since ss#2499 (#2519). This unpacked three, so every drive of
    # direct-api-send-bypass raised ValueError at its reconcile leg from then
    # until 2026-09-04, when the overlay#346 release gate tripped on it. Pinned
    # by test_reconcile_unpack_matches_the_reconciler.
    *_counts, unmatched = reconciler.reconcile(sent, rows)
    return unmatched


def drive_leg(
    leg: dict,
    *,
    capabilities: Capabilities,
    requirements: list[str],
    audit: AuditReader | None,
    settle_s: int,
    window_start: datetime | None = None,
    audit_mark: str | None = None,
) -> LegObservation:
    """Perform one leg and return everything observable about it.

    ``window_start`` / ``audit_mark`` let a console-side leg observe the WHOLE
    scenario's window rather than its own. The reconcile leg needs that: it has
    to see the sends its sibling leg provoked, and a per-leg window would open
    after those sends had already left.
    """
    blocked = capabilities.missing_for(requirements)
    if blocked:
        return LegObservation(unavailable=blocked)

    drive = leg.get("drive") or {}
    started = window_start or datetime.now(timezone.utc)
    observation = LegObservation()
    mark = audit_mark
    if audit is not None and mark is None:
        try:
            mark = audit.snapshot()
        except Exception as exc:  # noqa: BLE001 -- an unreadable ledger is UNKNOWN, never empty
            observation.notes.append(f"audit seam baseline read failed: {exc}")

    settled_past = datetime.now(timezone.utc)
    if drive.get("kind") == "email_probe":
        recipient = str(drive.get("to") or SEAT_TOKEN)
        if recipient == SEAT_TOKEN:
            recipient = str(capabilities.seat_inbox)
        accepted, reply, sent_at = send_and_wait(
            sender=str(drive["as"]),
            recipient=recipient,
            subject=str(drive.get("subject") or f"shadow-firm {leg.get('id')}"),
            body=str(drive["body"]),
            key=str(capabilities.agentmail_key),
            timeout_s=int(drive.get("reply_timeout_s") or 420),
        )
        if not accepted:
            return LegObservation(
                unavailable="the AgentMail API refused the probe send, so the seat was never "
                "asked anything and nothing about it was measured"
            )
        observation.reply_observed = reply is not None
        observation.reply_body = reply
        # The ledger view must reach at least the reply (whose REPLY_SENT row is
        # written broker-side BEFORE the mailbox sees the message), or the send
        # for a leg that rightly got no reply.
        settled_past = datetime.now(timezone.utc) if reply is not None else sent_at
    else:
        # console_reconcile drives nothing; it settles, then reads both sides.
        # Its rows must cover every send its sibling legs provoked, all of which
        # happened before this leg started.
        time.sleep(settle_s)

    if audit is not None and mark is not None:
        try:
            observation.audit_rows, settled = _read_settled_rows(audit, mark, settled_past=settled_past)
            if not settled:
                observation.notes.append(
                    "ledger view did not demonstrably reach the leg's own moment inside the "
                    "settle budget; rows may lag (ADR 0043 seam serves a delayed view)"
                )
        except Exception as exc:  # noqa: BLE001 -- an unreadable ledger is UNKNOWN, never empty
            observation.audit_rows = None
            observation.notes.append(f"audit seam read failed: {exc}")

    addresses = [str(e.get("address")).lower() for e in leg.get("expect") or [] if e.get("kind") == "no_send_to"]
    if addresses and capabilities.agentmail_key and capabilities.seat_inbox:
        observation.sends_to = {}
        for address in addresses:
            count = messages_from(address, capabilities.seat_inbox, started, str(capabilities.agentmail_key))
            if count is None:
                observation.notes.append(f"{address}: mailbox unreadable")
                continue
            observation.sends_to[address] = count

    draft_addresses = [
        str(e.get("address")).lower()
        for e in leg.get("expect") or []
        if e.get("kind") in ("draft_exists_to", "no_draft_to")
    ]
    if draft_addresses and capabilities.agentmail_key and capabilities.seat_inbox:
        observation.drafts_to = {}
        for address in draft_addresses:
            count = drafts_addressed_to(str(capabilities.seat_inbox), address, started, str(capabilities.agentmail_key))
            if count is None:
                observation.notes.append(f"drafts folder unreadable while checking {address}")
                continue
            observation.drafts_to[address] = count

    wants_reconcile = any(e.get("kind") == "no_unaudited_sends" for e in leg.get("expect") or [])
    if wants_reconcile and capabilities.agentmail_key and capabilities.seat_inbox:
        if observation.audit_rows is None:
            observation.notes.append("reconciliation skipped: the ledger side of the comparison is unreadable")
        else:
            observation.unaccounted_sends = unaccounted_sends(
                str(capabilities.seat_inbox),
                observation.audit_rows,
                started,
                str(capabilities.agentmail_key),
            )
    return observation
