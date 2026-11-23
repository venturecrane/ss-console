"""SMD records a firm rule the client asked for in writing (2026-11-23).

    rule_record_by_smd   root only. A firm rule an attorney stated in an email
                         this seat received, recorded and installed through the
                         path a confirmed "yes" takes, with no read-back and no
                         letter to anyone.
    rule_record_probe    root only. Where a recorded rule stands: committed, its
                         install run, and whether that run says installed.

WHY (Captain, 2026-11-23). An attorney wrote five rules for his discovery
responses, each ending "This should be a firm rule", and the Operator refused
them on reach. SMD has to be able to record a client's written instruction from
its own side, reliably, without asking the attorney to confirm it again.

THE CONTROL THAT REPLACES THE READ-BACK. The rule IS the client's own words,
and the broker checks them against the mailbox itself, not against anything the
caller says:

* the sender is on this seat's roster (``scope.inbound_allow_from``);
* the named email is a received message in this mailbox's Inbox, sent by that
  sender (``msgraph_lookup.received_by_graph_id``: not a draft, not this
  mailbox's own, no foreign replyTo), and not a forward (an ``FW:``/``Fwd:``
  subject is refused);
* the audit log records it arriving (an ``INBOUND_RECEIVED`` row for its id);
* the rule's text appears verbatim in what the sender wrote: Graph's
  ``uniqueBody`` (``body`` only when Graph gives none), cut at the first
  forwarded-message header, compared with whitespace, case and curly quotes
  folded. It must say something beyond the stock line "this should be a firm
  rule" (:data:`MIN_RULE_CHARS` characters of its own);
* a live record repeats the dry run's digest (``confirm``), so what is recorded
  is what SMD read.

ONE INSTALL PATH. The rule is proposed (``EstablishmentStore.propose``, scope
``firm_adjust``, ``instructed_by`` the attorney, ``for_admin`` false so no
administrator is asked and no install letter is owed), committed through
``EstablishmentStore.submit`` exactly as a confirmed yes commits it (consume,
ESTABLISHMENT_SUBMITTED, the run the root intake installs), and its run is
watched (``own_rule_verbs.install_watch``) so the seat's install check stamps
``installed_at`` or records the failure as owed to SMD. Both rows carry the
actor ``smd`` (``by_smd``, never a wire field), so the firm's activity feed does
not call this a read-back or a rule the firm confirmed. ``applied_by`` on the
installed rule is the SMD address that recorded it; the root intake requires
only that it is a non-empty string (``_verify_adjustment``), never a roster
member, and ``test_the_root_intake_installs_what_smd_submits`` runs the real
intake on the submission to prove it.

DUPLICATES. The same sentence committed for this attorney under the same
subject and observed installed is a no-op answering the existing id. One
committed and not yet installed, or whose install failed, is refused naming its
run. An open, unconfirmed proposal of the same sentence under the same subject,
not waiting on an administrator, is the row committed; any other open twin is
refused naming it (end it with ``rule_close`` first).

THE AUDIT ROW, ``RULE_RECORDED_BY_SMD``: ids and digests only (proposal, run,
recorder, who stated it, the email's ids, the rule's digest, the subject,
``has_reason``). Never the rule or the reason.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import time
from typing import Any

from . import msgraph_lookup, own_rule_verbs
from .mailbox_keeper_classify import _QUOTE_START
from .broker_context import BrokerContext
from .establishment_validation import (
    EstablishmentValidationError,
    _require_proposal_id,
    normalize_outcome_reason,
    normalize_rule_text,
    require_address,
)
from .msgraph_ops import MsGraphRefused
from .recipient_policy import authored_policy
from .rule_reach import firm_subject

VERB_RECORD = "rule_record_by_smd"
VERB_PROBE = "rule_record_probe"
VERBS: tuple[str, ...] = (VERB_RECORD, VERB_PROBE)

#: Appended once per rule SMD records on a client's written instruction.
RULE_RECORDED_BY_SMD_ACTION_TYPE = "RULE_RECORDED_BY_SMD"
#: What a rule must say beyond the stock line, folded, so it is a rule.
MIN_RULE_CHARS = 20
_STOCK = ("this should be a firm rule", "should be a firm rule")
_FORWARD_SUBJECT = re.compile(r"^\s*(fw|fwd)\s*:", re.IGNORECASE)
#: A line where someone else's words begin: a forwarded-message marker, or a
#: reply's quote header (the mailbox keeper's pattern: "On ... wrote:",
#: "Original Message", an underscore rule, "From: ..."), or a ">" quote line.
_FORWARD_LINE = re.compile(r"^(?:-{2,}.*forwarded|from:\s|>)", re.IGNORECASE)
SCOPE = "firm_adjust"
_GRAPH_ID = re.compile(r"^[A-Za-z0-9=_-]{16,512}$")
_SOURCE_SELECT = "id,subject,body,uniqueBody"
_CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


class RecordRefused(ValueError):
    """The rule cannot be recorded. The message is the sentence for SMD."""


def _fold(text: str) -> str:
    """Whitespace, case and curly quotes folded: how the rule is found in the email."""
    return " ".join(text.translate(_CURLY).split()).casefold()


def _plain(body: Any) -> str:
    """An email body as plain text: tags dropped, entities decoded."""
    if not isinstance(body, dict):
        return ""
    content = str(body.get("content") or "")
    if str(body.get("contentType") or "").lower() == "html":
        content = re.sub(r"(?is)<(script|style|blockquote)\b.*?</\1>", " ", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", content)
        content = re.sub(r"<[^>]+>", " ", content)
    return html.unescape(content)


def _arrived(audit_db: str, graph_message_id: str) -> bool:
    """Whether the broker's own audit log records this email arriving."""
    try:
        conn = sqlite3.connect(audit_db, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT metadata FROM audit_log WHERE action_type='INBOUND_RECEIVED' AND metadata LIKE ?",
                (f"%{graph_message_id}%",),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    for (metadata,) in rows:
        try:
            if json.loads(metadata or "{}").get("vendor_message_id") == graph_message_id:
                return True
        except ValueError:
            continue
    return False


def _own_words(body: str) -> str:
    """What the sender wrote: the lines before the first one where a quoted
    reply or a forwarded message begins."""
    own: list[str] = []
    for line in body.splitlines():
        bare = line.strip()
        if _FORWARD_LINE.match(bare) or _QUOTE_START.match(bare):
            break
        own.append(line)
    return "\n".join(own)


def _rule_of(raw: Any) -> str:
    """The rule's text, which must be the client's words and say something."""
    text = normalize_rule_text(raw)
    rest = _fold(text)
    for stock in _STOCK:
        rest = rest.replace(stock, "")
    if len(re.sub(r"[^a-z0-9]+", "", rest)) < MIN_RULE_CHARS:
        raise RecordRefused(
            f"the rule must say at least {MIN_RULE_CHARS} characters beyond 'this should be a firm rule'"
        )
    return text


def _evidence(broker: BrokerContext, sender: str, gid: str, text: str) -> dict[str, str]:
    """Every check on the client's email, fresh from the mailbox. Raises ``RecordRefused``."""
    if not authored_policy(broker.customer_path).allows_reply_to(sender):
        raise RecordRefused(f"{sender} is not on this seat's roster (scope.inbound_allow_from)")
    if not _GRAPH_ID.match(gid):
        raise RecordRefused("message_id must be the email's Graph id (the requests index's vendor_message_id)")
    ops = broker.msgraph
    if ops is None or not broker.audit_db_path:
        raise RecordRefused("this seat has no Graph mailbox and audit log to check the email against")
    try:
        found = msgraph_lookup.received_by_graph_id(ops, gid)
    except MsGraphRefused as exc:
        raise RecordRefused(str(exc)) from exc
    if found is None or not found["graph_message_id"]:
        raise RecordRefused("that email is not in this seat's Inbox as a received message")
    if found["sender"] != sender:
        raise RecordRefused(f"that email was sent by {found['sender']}, not {sender}")
    if not _arrived(str(broker.audit_db_path), gid):
        raise RecordRefused("the audit log holds no record of that email arriving on this seat")
    source = msgraph_lookup._read(ops, ops._mail_path("messages", gid) + f"?$select={_SOURCE_SELECT}")
    if not isinstance(source, dict) or str(source.get("id") or "") != gid:
        raise RecordRefused("the mailbox answered for a different message than the one named; refusing")
    subject = str(source.get("subject") or "")
    if _FORWARD_SUBJECT.match(subject):
        raise RecordRefused("that email is a forward; a rule is recorded from the email its author sent")
    own = _own_words(_plain(source.get("uniqueBody")) or _plain(source.get("body")))
    if _fold(text) not in _fold(own):
        raise RecordRefused("the rule's text does not appear in what the sender wrote; copy it from the email exactly")
    return {
        "graph_message_id": gid,
        "internet_message_id": found["internet_message_id"],
        "received_at": found["received_at"],
        "subject": subject,
    }


def preview(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """Every check, fresh. Writes nothing. Raises ``RecordRefused`` or
    ``EstablishmentValidationError``; a mailbox that cannot be read raises its
    transport error (nothing was decided)."""
    sender = require_address(request.get("sender"), "sender")
    recorded_by = require_address(request.get("recorded_by"), "recorded_by")
    reason = normalize_outcome_reason(request.get("reason"))
    if reason is None:
        raise RecordRefused("reason is required: say why SMD records this rule on the client's instruction")
    subject_raw = request.get("subject")
    if not isinstance(subject_raw, dict):
        raise RecordRefused("subject must be {output_class, property, doc_kinds[, attorney]}")
    subject = firm_subject(subject_raw)
    text = _rule_of(request.get("text"))
    email = _evidence(broker, sender, str(request.get("message_id") or "").strip(), text)
    digest = hashlib.sha256(
        json.dumps(
            {
                "sender": sender,
                "message_id": email["graph_message_id"],
                "text": text,
                "subject": subject,
                "recorded_by": recorded_by,
                "reason": reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "sender": sender,
        "recorded_by": recorded_by,
        "reason": reason,
        "subject": subject,
        "text": text,
        "email": email,
        "digest": digest,
    }


def _canon(subject: dict[str, Any]) -> dict[str, Any]:
    """A subject as two are compared: its document kinds in sorted order."""
    kinds = subject.get("doc_kinds")
    return {**subject, **({"doc_kinds": sorted(kinds)} if kinds else {})}


def _peek(broker: BrokerContext, run_id: str) -> str:
    """A waiting result's status, read without consuming it (the dry run)."""
    try:
        result = json.loads((broker.establishment.results_dir / f"{run_id}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    return str(result.get("status") or "") if isinstance(result, dict) else ""


def _install_state(broker: BrokerContext, row: dict[str, Any], *, settle: bool) -> str:
    """``installed``, ``pending`` or ``failed`` for a committed row. With
    ``settle`` a waiting result is read for good (the live record); without it
    nothing is written (the dry run)."""
    pending = broker.establishment.pending
    run_id = str(row.get("consumed_run_id") or "")
    if row.get("installed_at") is None and run_id:
        if settle and _settle(broker, row["proposal_id"], run_id):
            return "failed"
        if not settle and (status := _peek(broker, run_id)):
            return "installed" if status == "installed" else "failed"
    if (pending.get(row["proposal_id"]) or row).get("installed_at") is not None:
        return "installed"
    conn = pending._connect()
    try:
        done = conn.execute("SELECT done_at FROM own_rule_install_watch WHERE run_id=?", (run_id,)).fetchone()
    except sqlite3.OperationalError:
        done = None
    finally:
        conn.close()
    # A settled watch on a row never stamped installed is an install that failed.
    return "failed" if done is not None and done[0] is not None else "pending"


def _existing(broker: BrokerContext, p: dict[str, Any], *, settle: bool = True) -> dict[str, Any] | None:
    """The INSTALLED rule this would duplicate, or None. The same words
    committed under another subject, or committed and not installed, refuse."""
    pending = broker.establishment.pending if broker.establishment else None
    if pending is None:
        raise RecordRefused("this broker has no rule store configured; nothing was recorded")
    done = pending.find_committed_duplicate(instructed_by=p["sender"], scope=SCOPE, text=p["text"])
    if done is None:
        return None
    if _canon(done["subject"]) != _canon(p["subject"]):
        raise RecordRefused(
            f"these words are already committed as rule {done['proposal_id']} for {json.dumps(done['subject'])}; "
            "nothing was recorded"
        )
    state = _install_state(broker, done, settle=settle)
    run = done.get("consumed_run_id")
    if state == "pending":
        raise RecordRefused(
            f"rule {done['proposal_id']} with these words was committed on run {run} and is not yet "
            "installed; probe it before recording again. Nothing was recorded"
        )
    if state == "failed":
        raise RecordRefused(
            f"rule {done['proposal_id']} with these words failed to install on run {run} (owed to SMD in "
            "the triage list); fix the cause. Nothing was recorded"
        )
    return pending.get(done["proposal_id"]) or done


def _proposal_for(broker: BrokerContext, p: dict[str, Any], source_ref: str) -> tuple[str, bool]:
    """The row to commit: a fresh proposal, or the open twin of the same words
    and subject that waits on nobody. ``(proposal_id, was_open_twin)``."""
    proposed = broker.establishment.propose(
        {
            "scope": SCOPE,
            "subject": p["subject"],
            "text": p["text"],
            "instructed_by": p["sender"],
            "source_ref": source_ref,
            "for_admin": False,
        },
        by_smd=True,
    )
    if proposed.get("already_recorded"):
        raise RecordRefused(f"rule {proposed['proposal_id']} changed while recording; nothing was recorded")
    twin = proposed.get("duplicate_of")
    if twin is None:
        return proposed["proposal_id"], False
    if _canon(proposed["subject"]) != _canon(p["subject"]) or proposed["for_admin"]:
        waits = ", waiting on an administrator" if proposed["for_admin"] else ""
        raise RecordRefused(
            f"an open proposal {twin} of these words exists for {json.dumps(proposed['subject'])}{waits}; "
            f"end it with `python -m workspace_broker.rule_close {twin}` and record again. Nothing was recorded"
        )
    return str(twin), True


def _record(broker: BrokerContext, p: dict[str, Any]) -> dict[str, Any]:
    """Propose, commit and watch, under the establishment lock. The one install path."""
    store = broker.establishment
    email = p["email"]
    source_ref = email["internet_message_id"][:200] or email["graph_message_id"][:200]
    with broker._establish_lock:
        store.sweep()
        done = _existing(broker, p)
        if done is not None:
            return {
                "ok": True,
                "accepted": True,
                "already_recorded": True,
                "proposal_id": done["proposal_id"],
                "run_id": done.get("consumed_run_id"),
                "installed_at": done.get("installed_at"),
            }
        proposal_id, was_twin = _proposal_for(broker, p, source_ref)
        submitted = store.submit(
            {"scope": SCOPE, "proposal_id": proposal_id, "instructed_by": p["recorded_by"], "source_ref": source_ref},
            by_smd=True,
        )
        run_id = submitted["run_id"]
        own_rule_verbs.install_watch(broker, {"proposal_id": proposal_id, "run_id": run_id})
        broker.ledger.append(
            {
                "action_type": RULE_RECORDED_BY_SMD_ACTION_TYPE,
                "actor": "smd",
                "actor_role": "captain",
                "metadata": json.dumps(
                    {
                        "proposal_id": proposal_id,
                        "run_id": run_id,
                        "recorded_by": p["recorded_by"],
                        "instructed_by": p["sender"],
                        "source_message_id": email["graph_message_id"],
                        "source_internet_message_id": email["internet_message_id"],
                        "text_sha256": hashlib.sha256(p["text"].encode("utf-8")).hexdigest(),
                        "open_twin_committed": was_twin,
                        "has_reason": True,
                        **p["subject"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    return {
        "ok": True,
        "accepted": True,
        "already_recorded": False,
        "proposal_id": proposal_id,
        "run_id": run_id,
        "open_twin_committed": was_twin,
        "digest": p["digest"],
    }


def _refusal(reason: str) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason}


def record_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """Dry run (``dry_run: true``): every check, the digest, nothing written.
    Live: the same checks fresh, and ``confirm`` must equal the fresh digest."""
    if broker.ledger is None or broker.establishment is None:
        raise ValueError("rule_record_by_smd needs the audit ledger and the establishment spool")
    dry_run = request.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return _refusal("dry_run must be a boolean")
    try:
        p = preview(broker, request)
        if dry_run:
            done = _existing(broker, p, settle=False)
            return {
                "ok": True,
                "accepted": True,
                "dry_run": True,
                "already_recorded": done is not None,
                "existing_proposal_id": done["proposal_id"] if done else None,
                **{k: p[k] for k in ("sender", "recorded_by", "reason", "subject", "text", "email", "digest")},
            }
        if str(request.get("confirm") or "").strip() != p["digest"]:
            return _refusal(
                "confirm does not match a fresh dry run of this rule (the words, the email or the subject "
                "changed since); run the dry run again and read it"
            )
        return _record(broker, p)
    except (RecordRefused, EstablishmentValidationError) as exc:
        return _refusal(str(exc))


def _settle(broker: BrokerContext, proposal_id: str, run_id: str) -> str | None:
    """Read this run's result now if the intake has written it: the install
    check's read (``own_rule_verbs._verdict``), done once, its watch settled
    and a failure owed to SMD. ``None`` when there is no result yet."""
    store = broker.establishment
    if not (store.results_dir / f"{run_id}.json").is_file():
        return None
    why = own_rule_verbs._verdict(store, run_id, 0.0)
    if why == "wait":
        return None
    conn = own_rule_verbs._connect(store.pending)
    try:
        with conn:
            conn.execute(
                "UPDATE own_rule_install_watch SET done_at=? WHERE run_id=? AND done_at IS NULL",
                (time.time(), run_id),
            )
    finally:
        conn.close()
    if why:
        own_rule_verbs._owe_install(broker, store.pending, proposal_id, why)
    return why


def probe_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """Where one rule stands. Reads its install result when one is waiting."""
    store = broker.establishment
    if store is None or store.pending is None:
        raise ValueError("rule_record_probe needs the establishment spool and its rule store")
    proposal_id = _require_proposal_id(request.get("proposal_id"))
    with broker._establish_lock:
        row = store.pending.get(proposal_id)
        if row is None:
            return {"ok": True, "found": False, "proposal_id": proposal_id}
        failed = None
        run_id = row.get("consumed_run_id")
        if run_id and row.get("installed_at") is None:
            failed = _settle(broker, proposal_id, str(run_id))
            row = store.pending.get(proposal_id) or row
    return {
        "ok": True,
        "found": True,
        "proposal_id": proposal_id,
        "scope": row["scope"],
        "subject": row["subject"],
        "text": row["text"],
        "instructed_by": row["instructed_by"],
        "consumed_at": row.get("consumed_at"),
        "run_id": row.get("consumed_run_id"),
        "installed_at": row.get("installed_at"),
        "install_failed": failed or None,
    }


__all__ = [
    "MIN_RULE_CHARS",
    "RULE_RECORDED_BY_SMD_ACTION_TYPE",
    "VERBS",
    "RecordRefused",
    "preview",
    "probe_verb",
    "record_verb",
]
