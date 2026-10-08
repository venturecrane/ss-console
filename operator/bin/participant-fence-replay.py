#!/usr/bin/env python3
"""Replay the participant fence offline over a seat's recent sends. READ-ONLY.

WHY (2026-10-07 plan, section 6). Before the fence ships, every send the seat
made in the last N days is run through the exact decision code that will
enforce it (``workspace_broker/participant_fence.py``), with the anchor and lane
the NEW overlay would pass, and every would-be refusal is listed by job. Each
one must be the incident class (a firm person who was not on the request) or be
fixed in the same change. A refusal nobody looked at before merge is a client
who stops hearing from the Operator after the reprovision.

WHAT IT READS, and nothing else:
  * the audit ledger (``CONFIRM_SEND_*``, the job event rows, ``INBOUND_RECEIVED``,
    ``REPLY_SENT``) and the job / pending-rule tables, opened ``mode=ro``;
  * customer.yaml (the authored people and lanes);
  * the mailbox, through GETs on the READ credential only: the anchor emails'
    participants, and each sent message's actual To/Cc/Bcc.
It never POSTs, never writes a file, and prints no subject or body: firm staff
addresses that would be refused are printed (they are the answer), every
outside recipient is printed as ``outside``.

HOW THE ANCHOR IS RECONSTRUCTED (what the new overlay would have passed):
  * a reply answers its own email, and a bound reply its verified one: allowed
    without a lookup, exactly as the broker decides it;
  * a routine's code-rendered send (``skill_name`` on the row) rides its lane:
    ``skill:<name>`` when the skill authors ``settings.recipient``, else
    ``escalation``;
  * a send in a job's completion wake (a job event row just before the
    session's first row) anchors on that job;
  * a ``[rule ...]`` request to the administrators rides ``rule_dispatch``; a
    rule's outcome letter anchors on its row (whose origin an old row lacks);
  * any other send in a session anchors on the email that opened it, INFERRED
    as the ``INBOUND_RECEIVED`` row just before the session's first row, and
    the output says so;
  * a cron session has no anchor.

USAGE (on a seat, as root, after uploading this file and participant_fence.py):
    /opt/workspace-broker/.venv/bin/python participant-fence-replay.py \\
        --fence-module /tmp/pfreplay/participant_fence.py --days 30 \\
        --audit-db /opt/data/audit/audit.db \\
        --customer /var/lib/smd-workspace-broker/customer.yaml \\
        --msgraph-read /var/lib/smd-workspace-broker/msgraph-read.json   # Graph seats
        --agentmail-cred /var/lib/smd-workspace-broker/agentmail.json    # AgentMail seats
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
import urllib.parse
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

SEND_VERBS = frozenset({"msgraph_send", "agentmail_send"})
REPLY_VERBS = frozenset({"msgraph_reply", "agentmail_reply", "msgraph_reply_bound"})
_JOB_EVENT = re.compile(r"^(DEMAND|MEDCHRON|DRAFTING)_JOB_[A-Z_]+$")
_JOB_KIND = {"DEMAND": "demand_job", "MEDCHRON": "medchron_job", "DRAFTING": "drafting_job"}
JOB_TABLE = {"demand_job": "demand_jobs", "medchron_job": "medchron_jobs", "drafting_job": "drafting_jobs"}
_RULE_TAG = re.compile(r"\[rule ([0-9a-f]{6,32})\]")
_OPS_TAG = re.compile(r"\[ops ([0-9a-f]{6,32})\]")
_WAKE_WINDOW = timedelta(seconds=180)
_INBOUND_WINDOW = timedelta(seconds=600)
_PARTICIPANT_SELECT = "id,from,sender,toRecipients,ccRecipients,conversationId,isDraft,internetMessageId"
_SENT_SELECT = "id,subject,toRecipients,ccRecipients,bccRecipients"


def load_fence(path: Path | None) -> ModuleType:
    """The participant fence module: the repo's own, or an uploaded copy loaded
    INTO the seat's ``workspace_broker`` package so its relative imports resolve
    against the seat's ``recipient_policy``."""
    if path is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from workspace_broker import participant_fence

        return participant_fence
    spec = importlib.util.spec_from_file_location("workspace_broker.participant_fence", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value[:19]).replace(tzinfo=timezone.utc)


@dataclass
class Send:
    ts: str
    action: str
    verb: str
    recipients: list[str]
    skill: str = ""
    session_id: str = ""
    message_id: str = ""
    graph_message_id: str = ""


@dataclass
class Verdict:
    send: Send
    path: str
    anchor: dict[str, str] | None
    lane: str | None
    decision: str = "allowed"
    refused: list[str] = field(default_factory=list)
    note: str = ""


class Mail(Protocol):
    def participants(self, message_id: str) -> dict[str, Any]: ...
    def find_by_imid(self, imid: str) -> str | None: ...
    def sent_message(self, send: Send) -> dict[str, Any] | None: ...


@dataclass
class Ledger:
    """Everything the replay reads from the audit DB, read once."""

    sends: list[Send]
    session_start: dict[str, datetime]
    job_events: list[tuple[datetime, str, str]]
    inbound: list[tuple[datetime, str]]
    jobs: dict[tuple[str, str], dict[str, Any]]
    rules: dict[str, dict[str, Any]]
    tokens: set[str] = field(default_factory=set)


def _meta(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def read_ledger(conn: sqlite3.Connection, since: str) -> Ledger:
    conn.row_factory = sqlite3.Row
    sends: list[Send] = []
    starts: dict[str, datetime] = {}
    events: list[tuple[datetime, str, str]] = []
    inbound: list[tuple[datetime, str]] = []
    tokens: set[str] = set()
    rows = conn.execute(
        "SELECT ts, action_type, skill_name, metadata FROM audit_log WHERE ts >= ? ORDER BY ts", (since,)
    )
    for row in rows:
        meta, at, when = _meta(row["metadata"]), str(row["action_type"]), _ts(str(row["ts"]))
        session = str(meta.get("session_id") or "")
        if session and session not in starts:
            starts[session] = when
        if (
            at in ("CONFIRM_SEND_DISPATCHED", "CONFIRM_SEND_FAILED")
            and str(meta.get("verb")) in SEND_VERBS | REPLY_VERBS
        ):
            if meta.get("audit_row_token"):
                tokens.add(str(meta["audit_row_token"]))
            sends.append(
                Send(
                    ts=str(row["ts"]),
                    action=at,
                    verb=str(meta.get("verb")),
                    recipients=[str(r) for r in meta.get("recipients") or []],
                    skill=str(row["skill_name"] or ""),
                    session_id=session,
                    message_id=str(meta.get("message_id") or ""),
                    graph_message_id=str(meta.get("graph_message_id") or ""),
                )
            )
        elif _JOB_EVENT.match(at) and isinstance(meta.get("job_id"), str):
            events.append((when, _JOB_KIND[at.split("_", 1)[0]], meta["job_id"]))
        elif at == "INBOUND_RECEIVED" and isinstance(meta.get("vendor_message_id"), str):
            inbound.append((when, meta["vendor_message_id"]))
    jobs = {
        (kind, str(r["id"])): dict(r)
        for kind, table in JOB_TABLE.items()
        for r in _table(conn, f"SELECT id, requester, request_ref FROM {table}")  # noqa: S608 - table names come from the closed JOB_TABLE map, never input
    }
    # The proposal rows the sweep has since deleted live on in their own audit
    # rows (who asked, by id); the table's row wins where it still exists.
    proposed = _table(
        conn,
        "SELECT metadata FROM audit_log WHERE action_type IN ('RULE_PROPOSED','ACT_PROPOSED','OPS_REQUEST_RECORDED')",
    )
    rules = {
        str(m["proposal_id"]): {"instructed_by": m.get("instructed_by")}
        for m in (_meta(r["metadata"]) for r in proposed)
        if isinstance(m.get("proposal_id"), str)
    }
    rules.update({str(r["proposal_id"]): dict(r) for r in _table(conn, "SELECT * FROM pending_rules")})
    return Ledger(sends, starts, events, inbound, jobs, rules, tokens)


def routine_lane(fence: ModuleType, facts: Any, skill: str) -> str:
    key = fence.LANE_SKILL_PREFIX + skill
    return key if key in facts.lanes else fence.LANE_ESCALATION


def _latest_before(items: list[tuple[datetime, Any]], start: datetime, window: timedelta) -> Any:
    found = [v for when, v in items if start - window <= when <= start]
    return found[-1] if found else None


def derive(
    fence: ModuleType, facts: Any, ledger: Ledger, send: Send, subject: str, channel_kind: str
) -> tuple[str, dict[str, str] | None, str | None]:
    """(path, anchor, lane) the new overlay would pass for this send."""
    if send.verb in REPLY_VERBS:
        return ("bound_reply" if send.verb.endswith("_bound") else "reply"), None, None
    if send.skill:
        return f"routine:{send.skill}", None, routine_lane(fence, facts, send.skill)
    if _OPS_TAG.search(subject):
        return "ops_request", None, None
    tag = _RULE_TAG.search(subject)
    if tag and not subject.lower().startswith("a rule for the firm"):
        return "rule_outcome", {"kind": "rule", "proposal_id": tag.group(1)}, None
    lane = fence.LANE_RULE_DISPATCH if tag else None
    if send.session_id.startswith("cron_"):
        return "cron", None, lane
    start = ledger.session_start.get(send.session_id)
    if start is not None:
        wake = _latest_before([(w, (k, j)) for w, k, j in ledger.job_events], start, _WAKE_WINDOW)
        if wake is not None:
            return f"job_wake:{wake[0]}", {"kind": wake[0], "job_id": wake[1]}, lane
        email = _latest_before(ledger.inbound, start, _INBOUND_WINDOW)
        if email is not None:
            key = "graph_message_id" if channel_kind == "graph_message" else "message_id"
            path = "rule_dispatch" if tag else "email_turn(inferred)"
            return path, {"kind": channel_kind, key: email}, lane
    return ("rule_dispatch" if tag else "unanchored"), None, lane


def resolver(fence: ModuleType, mail: Mail, ledger: Ledger, channel_kind: str) -> Callable[[Any], Any]:
    """The broker's anchor resolution, offline: mailbox GETs + the ledger tables."""

    def unverifiable(why: str) -> Exception:
        return fence.FenceRefused(fence.FENCE_UNVERIFIABLE, why)

    def message(kind: str, ident: str) -> Any:
        if kind != channel_kind:
            raise unverifiable(f"a {kind} anchor on the {channel_kind} channel")
        try:
            found = mail.participants(ident)
        except Exception as exc:  # every read failure is unverifiable, as in the broker
            raise unverifiable(f"read failed: {type(exc).__name__}") from exc
        return fence.Participants(
            sender=found["sender"],
            to=tuple(found.get("to") or ()),
            cc=tuple(found.get("cc") or ()),
            conversation_id=str(found.get("conversation_id") or ""),
            message_id=ident,
        )

    def resolve(anchor: Any) -> Any:
        if anchor.kind in fence.MESSAGE_KINDS:
            return message(anchor.kind, anchor.ident)
        if anchor.kind == fence.RULE_KIND:
            row = ledger.rules.get(anchor.ident) or {}
            requester = _address(row.get("instructed_by"))
            origin = _meta(row.get("origin_json"))
            if not origin:
                # As the broker decides it (participant_lookup._rule): a row with
                # no recorded email reaches only the requester it recorded.
                if "@" not in requester:
                    raise unverifiable("the rule records neither its email nor who asked")
                return fence.Participants(sender=requester)
            parsed = fence.parse_anchor(origin, kinds=fence.MESSAGE_KINDS)
            home = message(parsed.kind, parsed.ident)
            if home.sender != requester:
                raise unverifiable("the rule's origin was sent by someone else")
            return home
        row = ledger.jobs.get((anchor.kind, anchor.ident)) or {}
        ref, requester = str(row.get("request_ref") or ""), str(row.get("requester") or "").strip().lower()
        if not ref or "@" not in requester:
            raise unverifiable("the job records no request email or no requester address")
        graph_id = mail.find_by_imid(ref) if "@" in ref else ref
        if not graph_id:
            raise unverifiable("the job's request email is not in the mailbox")
        home = message(channel_kind, graph_id)
        if home.sender != requester:
            raise unverifiable("the request email was not sent by the job's requester")
        return home

    return resolve


def judge(fence: ModuleType, facts: Any, ledger: Ledger, mail: Mail, send: Send, channel_kind: str) -> Verdict:
    sent = mail.sent_message(send) if send.verb in SEND_VERBS else None
    subject = str((sent or {}).get("subject") or "")
    recipients = list((sent or {}).get("recipients") or send.recipients)
    path, raw_anchor, lane = derive(fence, facts, ledger, send, subject, channel_kind)
    verdict = Verdict(send, path, raw_anchor, lane)
    if send.verb in REPLY_VERBS:
        verdict.note = "answers its own email (the reply target is the anchor)"
        return verdict
    resolve = resolver(fence, mail, ledger, channel_kind)
    anchor = fence.parse_anchor(raw_anchor) if raw_anchor else None
    try:
        fence.check_send(facts, recipients, lane=lane, anchor=anchor, resolve=resolve)
    except fence.FenceRefused as exc:
        verdict.decision = exc.fence
        firm = [r.lower() for r in recipients if facts.classify(r) == "firm"]
        covered = facts.lanes.get(lane or "", frozenset())
        try:
            allowed = fence.allowed_by_anchor(facts, resolve(anchor)) if anchor else frozenset()
        except fence.FenceRefused:
            allowed = frozenset()
        verdict.refused = sorted(a for a in firm if a not in covered and a not in allowed)
        verdict.note = str(exc).split(fence.DECISION)[0].strip()[:200]
    return verdict


def _shown(facts: Any, address: str) -> str:
    return address if facts.classify(address) in ("firm", "smd") else "outside"


def report(facts: Any, verdicts: list[Verdict]) -> dict[str, Any]:
    by_job = Counter((v.path, v.decision) for v in verdicts)
    refusals = [
        {
            "ts": v.send.ts,
            "verb": v.send.verb,
            "path": v.path,
            "anchor": (v.anchor or {}).get("kind", ""),
            "lane": v.lane or "",
            "fence": v.decision,
            "refused": v.refused,
            "recipients": sorted({_shown(facts, r) for r in v.send.recipients}),
            "why": v.note,
        }
        for v in verdicts
        if v.decision != "allowed"
    ]
    return {
        "sends": len(verdicts),
        "by_path": [{"path": p, "decision": d, "count": n} for (p, d), n in sorted(by_job.items())],
        "would_refuse": refusals,
    }


class GraphMail:
    """Read-only Graph: GET on the read credential, and nothing else."""

    def __init__(self, ops: Any, read_path: Path) -> None:
        self._ops, self._read = ops, read_path

    def _get(self, path: str) -> dict[str, Any]:
        return self._ops._request(path, "GET", None, credential_path=self._read, role="read")

    def participants(self, message_id: str) -> dict[str, Any]:
        m = self._get(self._ops._mail_path("messages", message_id) + f"?$select={_PARTICIPANT_SELECT}")
        sender = _address(m.get("from") or m.get("sender"))
        if m.get("isDraft") is True or not sender or sender == self._ops.mailbox().lower():
            raise LookupError("a draft, an unsigned message, or this mailbox's own")
        return {
            "sender": sender,
            "to": [_address(x) for x in m.get("toRecipients") or []],
            "cc": [_address(x) for x in m.get("ccRecipients") or []],
            "conversation_id": str(m.get("conversationId") or ""),
        }

    def find_by_imid(self, imid: str) -> str | None:
        wanted = "<" + imid.strip().strip("<>") + ">"
        flt = urllib.parse.quote("internetMessageId eq '" + wanted.replace("'", "''") + "'", safe="")
        page = self._get(self._ops._mail_path("messages") + f"?$select=id,from,isDraft&$top=10&$filter={flt}")
        mine = self._ops.mailbox().lower()
        for m in page.get("value") or []:
            if isinstance(m, dict) and m.get("isDraft") is not True and _address(m.get("from")) != mine:
                return str(m.get("id") or "") or None
        return None

    def sent_message(self, send: Send) -> dict[str, Any] | None:
        if not send.graph_message_id:
            return None
        m = self._get(self._ops._mail_path("messages", send.graph_message_id) + f"?$select={_SENT_SELECT}")
        fields = ("toRecipients", "ccRecipients", "bccRecipients")
        return {"subject": m.get("subject"), "recipients": [_address(x) for f in fields for x in m.get(f) or []]}

    def sent_items(self, since: str) -> list[dict[str, str]]:
        """Every Sent Items message since ``since``: who it was from, and the
        audit header the broker stamps on its own sends (empty when absent)."""
        flt = urllib.parse.quote(f"sentDateTime ge {since}Z", safe="")
        path = self._ops._mail_path("mailFolders", "sentitems", "messages")
        path += f"?$select=id,from,internetMessageHeaders&$top=50&$filter={flt}"
        found: list[dict[str, str]] = []
        for _page in range(40):
            page = self._get(path)
            for m in page.get("value") or []:
                headers = m.get("internetMessageHeaders") or []
                token = next((str(h.get("value") or "") for h in headers if _is_audit_header(h)), "")
                found.append({"from": _address(m.get("from")), "token": token})
            nxt = page.get("@odata.nextLink")
            if not isinstance(nxt, str) or not nxt.startswith(self._ops._graph_base + "/"):
                break
            path = nxt[len(self._ops._graph_base) :]
        return found


def _is_audit_header(header: Any) -> bool:
    return isinstance(header, dict) and str(header.get("name") or "").lower() == "x-smd-audit-row"


def sent_items_summary(mail: Any, ledger: Ledger, since: str) -> dict[str, Any]:
    """Sent Items the audit rows do not account for, by who they were from. The
    fence governs only broker transmits; anything else is listed, not judged."""
    lister = getattr(mail, "sent_items", None)
    if lister is None:
        return {"read": False}
    items = lister(since)
    mailbox = str(mail._ops.mailbox()).lower()
    unaudited = [i for i in items if not i["token"] or i["token"] not in ledger.tokens]
    by_from = Counter("operator mailbox" if i["from"] == mailbox else "staff send-as" for i in unaudited)
    return {"read": True, "total": len(items), "unaudited": len(unaudited), "unaudited_from": dict(by_from)}


class AgentMailMail:
    """Read-only AgentMail: GET a message in the seat's own inbox."""

    def __init__(self, ops: Any) -> None:
        self._ops = ops

    def _get(self, message_id: str) -> dict[str, Any]:
        return self._ops._request(self._ops._path("messages", message_id), "GET", None)

    def participants(self, message_id: str) -> dict[str, Any]:
        m = self._get(message_id)
        sender = _address(m.get("from"))
        if not sender or sender == str(self._ops.inbox_id()).lower():
            raise LookupError("unsigned, or this inbox's own message")
        return {"sender": sender, "to": _addresses(m.get("to")), "cc": _addresses(m.get("cc"))}

    def find_by_imid(self, imid: str) -> str | None:
        return None

    def sent_message(self, send: Send) -> dict[str, Any] | None:
        if not send.message_id:
            return None
        m = self._get(send.message_id)
        recipients = [a for f in ("to", "cc", "bcc") for a in _addresses(m.get(f))]
        return {"subject": m.get("subject"), "recipients": recipients}


def _address(value: Any) -> str:
    if isinstance(value, dict):
        value = (value.get("emailAddress") or {}).get("address") if "emailAddress" in value else value.get("address")
    text = str(value or "").strip()
    found = re.search(r"<([^<>@\s]+@[^<>\s]+)>", text)
    return (found.group(1) if found else text).lower()


def _addresses(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    return [a for a in (_address(v) for v in items) if "@" in a]


def _mail(args: argparse.Namespace) -> tuple[Mail, str]:
    if args.msgraph_read:
        from workspace_broker.msgraph_ops import MsGraphOps

        read = Path(args.msgraph_read)
        ops = MsGraphOps(read, Path(args.customer), read_credential_path=read)
        return GraphMail(ops, read), "graph_message"
    from workspace_broker.agentmail_ops import AgentMailOps

    ops = AgentMailOps(Path(args.agentmail_cred), Path(args.customer), args.slug)
    return AgentMailMail(ops), "agentmail_message"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--audit-db", required=True)
    p.add_argument("--customer", required=True)
    p.add_argument("--fence-module", default=None)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--msgraph-read", default="")
    p.add_argument("--agentmail-cred", default="")
    p.add_argument("--slug", default="")
    p.add_argument("--broker-root", default="/opt/workspace-broker", help="the seat's broker package root")
    args = p.parse_args(argv)
    if bool(args.msgraph_read) == bool(args.agentmail_cred):
        p.error("pass exactly one of --msgraph-read or --agentmail-cred")
    if Path(args.broker_root).is_dir():
        sys.path.insert(0, args.broker_root)
    fence = load_fence(Path(args.fence_module) if args.fence_module else None)
    facts = fence.seat_facts(Path(args.customer))
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = sqlite3.connect(f"file:{args.audit_db}?mode=ro", uri=True)
    try:
        ledger = read_ledger(conn, since)
    finally:
        conn.close()
    mail, kind = _mail(args)
    verdicts = [judge(fence, facts, ledger, mail, s, kind) for s in ledger.sends]
    summary = {"since": since, **report(facts, verdicts), "sent_items": sent_items_summary(mail, ledger, since)}
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
