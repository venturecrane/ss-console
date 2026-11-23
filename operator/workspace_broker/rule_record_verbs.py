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

THE CONTROL THAT REPLACES THE READ-BACK. The rule cites the client's own words,
and the broker checks them against the mailbox itself, not against anything the
caller says:

* the sender is on this seat's roster (``scope.inbound_allow_from``);
* the named email is a received message in this mailbox's Inbox, sent by that
  sender (``msgraph_lookup.received_by_graph_id``: not a draft, not this
  mailbox's own, no foreign replyTo);
* the audit log records it arriving (an ``INBOUND_RECEIVED`` row for its id);
* the quote, at least :data:`MIN_QUOTE_CHARS` characters, appears in that
  email's own text (Graph's ``uniqueBody``, the part not quoted from earlier
  mail; ``body`` only when Graph gives none), compared with whitespace, case and
  curly quotes folded;
* a live record repeats the dry run's digest (``confirm``), so what is recorded
  is what SMD read.

ONE INSTALL PATH. The rule is proposed (``EstablishmentStore.propose``, scope
``firm_adjust``, ``instructed_by`` the attorney, ``for_admin`` false so no
administrator is asked and no install letter is owed), committed through
``EstablishmentStore.submit`` exactly as a confirmed yes commits it (consume,
ESTABLISHMENT_SUBMITTED, the run the root intake installs), and its run is
watched (``own_rule_verbs.install_watch``) so the seat's install check stamps
``installed_at`` or records the failure as owed to SMD. ``applied_by`` on the
installed rule is the SMD address that recorded it, so the spec file says who
put it in force.

DUPLICATES. The same sentence already committed for this attorney under the
same subject is a no-op answering the existing id. An open, unconfirmed row of
the same sentence is ended quietly (``rule_close_by_smd``) and replaced, so a
later "yes" cannot commit it twice.

THE AUDIT ROW, ``RULE_RECORDED_BY_SMD``: ids and digests only (proposal, run,
recorder, who stated it, the email's ids, the quote's and the rule's digests,
the subject, ``has_reason``). Never the quote, the rule or the reason.
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
#: A quote shorter than this could be found in almost any email.
MIN_QUOTE_CHARS = 20
MAX_QUOTE_CHARS = 2000
SCOPE = "firm_adjust"
_GRAPH_ID = re.compile(r"^[A-Za-z0-9=_-]{16,512}$")
_SOURCE_SELECT = "id,subject,body,uniqueBody"
_CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


class RecordRefused(ValueError):
    """The rule cannot be recorded. The message is the sentence for SMD."""


def _fold(text: str) -> str:
    """Whitespace, case and curly quotes folded: how a quote is compared."""
    return " ".join(text.translate(_CURLY).split()).casefold()


def _plain(body: Any) -> str:
    """An email body as plain text: tags dropped, entities decoded."""
    if not isinstance(body, dict):
        return ""
    content = str(body.get("content") or "")
    if str(body.get("contentType") or "").lower() == "html":
        content = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", content)
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


def _evidence(broker: BrokerContext, sender: str, gid: str, quote: str) -> dict[str, str]:
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
    own = _plain(source.get("uniqueBody")) or _plain(source.get("body"))
    if _fold(quote) not in _fold(own):
        raise RecordRefused("the quote does not appear in that email's own text; copy it from the email exactly")
    return {
        "graph_message_id": gid,
        "internet_message_id": found["internet_message_id"],
        "received_at": found["received_at"],
        "subject": str(source.get("subject") or ""),
    }


def _quote_of(raw: Any) -> str:
    if not isinstance(raw, str):
        raise RecordRefused("quote must be the client's words, copied from the email")
    quote = " ".join(raw.split())
    if len(quote) < MIN_QUOTE_CHARS or len(quote) > MAX_QUOTE_CHARS:
        raise RecordRefused(f"quote must be {MIN_QUOTE_CHARS} to {MAX_QUOTE_CHARS} characters of the client's words")
    return quote


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
    text = normalize_rule_text(request.get("text"))
    quote = _quote_of(request.get("quote"))
    email = _evidence(broker, sender, str(request.get("message_id") or "").strip(), quote)
    digest = hashlib.sha256(
        json.dumps(
            {
                "sender": sender,
                "message_id": email["graph_message_id"],
                "text": text,
                "quote": quote,
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
        "quote": quote,
        "email": email,
        "digest": digest,
    }


def _existing(broker: BrokerContext, p: dict[str, Any]) -> dict[str, Any] | None:
    """The committed rule this would duplicate, or None. A committed rule of
    the same words under ANOTHER subject is a refusal, not a second rule."""
    pending = broker.establishment.pending if broker.establishment else None
    if pending is None:
        raise RecordRefused("this broker has no rule store configured; nothing was recorded")
    done = pending.find_committed_duplicate(instructed_by=p["sender"], scope=SCOPE, text=p["text"])
    if done is None:
        return None
    if done["subject"] != p["subject"]:
        raise RecordRefused(
            f"these words are already committed as rule {done['proposal_id']} for {json.dumps(done['subject'])}; "
            "nothing was recorded"
        )
    return done


def _close_open_twin(broker: BrokerContext, p: dict[str, Any]) -> str | None:
    """End an open, unconfirmed row of the same words quietly, so a later yes
    cannot commit the rule a second time. Returns the ended id, or None."""
    store = broker.establishment
    twin = store.pending.find_open_duplicate(instructed_by=p["sender"], scope=SCOPE, text=p["text"])
    if twin is None:
        return None
    store._ops.rule_close_by_smd(
        {
            "proposal_id": twin["proposal_id"],
            "closed_by": p["recorded_by"],
            "reason": "replaced by the same rule, recorded by SMD on the client's written instruction",
        }
    )
    return str(twin["proposal_id"])


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
        replaced = _close_open_twin(broker, p)
        proposed = store.propose(
            {
                "scope": SCOPE,
                "subject": p["subject"],
                "text": p["text"],
                "instructed_by": p["sender"],
                "source_ref": source_ref,
                "for_admin": False,
            }
        )
        if proposed.get("duplicate_of") or proposed.get("already_recorded"):
            raise RecordRefused(f"rule {proposed['proposal_id']} changed while recording; nothing was recorded")
        proposal_id = proposed["proposal_id"]
        submitted = store.submit(
            {"scope": SCOPE, "proposal_id": proposal_id, "instructed_by": p["recorded_by"], "source_ref": source_ref}
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
                        "quote_sha256": hashlib.sha256(p["quote"].encode("utf-8")).hexdigest(),
                        "text_sha256": hashlib.sha256(p["text"].encode("utf-8")).hexdigest(),
                        "replaced": replaced,
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
        "replaced": replaced,
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
            done = _existing(broker, p)
            return {
                "ok": True,
                "accepted": True,
                "dry_run": True,
                "already_recorded": done is not None,
                "existing_proposal_id": done["proposal_id"] if done else None,
                **{k: p[k] for k in ("sender", "recorded_by", "reason", "subject", "text", "quote", "email", "digest")},
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
    "MIN_QUOTE_CHARS",
    "RULE_RECORDED_BY_SMD_ACTION_TYPE",
    "VERBS",
    "RecordRefused",
    "preview",
    "probe_verb",
    "record_verb",
]
