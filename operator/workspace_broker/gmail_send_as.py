"""Staff send-as through a Google Workspace mailbox (ADR 0089 section 9).

The Gmail half of the send-as transport seam (``send_as_transport``). The acts
(``send_as_acts``) own every decision: the row, the digest, the approver, the
one consume. This module is what those decisions reach on a seat whose
``scope.staff_send_as`` authors ``via: gmail``:

* ``send_as``: the MIME message built from the canonical payload the approver
  saw, stamped with ``X-SMD-Audit-Row``, sent with ``users.messages.send``
  impersonating the authored mailbox through the broker's own Google
  credential (``google_auth``). The only place this broker sends Gmail.
* ``check_answer_origin``: the two-sided forgery guard. An answer counts only
  when it IS in the approver's SENT mail (they sent it) and is NOT a message
  this broker sent (no audit header, not a Gmail id the broker recorded). Any
  error looking refuses: "could not tell" never reads as "they sent it".
* ``notify``: the approval email and every notice, from the seat's own
  AgentMail inbox through the same audited, recipient-fenced dispatch every
  other seat send uses.
* ``conversation_of`` / ``reply_keys``: the sent message's RFC Message-ID, the
  one key an inbound to the seat can name (In-Reply-To / References).

WHY IT IS NOT IN ``operations.py``. ``WorkspaceOperations._handlers`` is the
reviewed surface any gateway call can reach by name. A send that sets a From
must be reachable from an approved row and from nothing else, so it lives here,
callable only from ``send_as_acts`` (``workspace_gmail_send`` stays unsupported).

WHY NO LINKS. The seat reads this mailbox (that is its job), so a signed
approve link mailed to it is a link the seat could read and press. Approvals are
replies, and ``send_as_acts.decide_link`` refuses on this transport.
"""

from __future__ import annotations

import base64
import re
import sqlite3
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, make_msgid
from typing import Any

from . import google_auth
from .agentmail_ops import AgentMailRefused, AgentMailTransportError
from .agentmail_ops import collect_recipients as agentmail_recipients
from .audit_ledger import new_row_token
from .broker_context import BrokerContext
from .msgraph_ops import AUDIT_ROW_HEADER, collect_recipients
from .participant_fence import LANE_SEND_AS
from .recipient_policy import normalize_address
from .request_errors import BrokerRefusal
from .send_as_transport import NOT_CONFIGURED, VIA_GMAIL, DraftRefused, OriginRefused, seat_data
from .transmit_verbs import dispatch_transmit

#: The Google client factory. Module-level so a test hands in a fake mailbox;
#: production builds the client from the broker-held credential (google_auth).
google_service = google_auth.service

_THREAD_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")
_RFC_ID = re.compile(r"^[^\s<>\"]{1,900}@[^\s<>\"]{1,900}$")
_ANGLE = re.compile(r"<([^<>\s]{1,998})>")
_MAX_REFERENCES = 20
_THREAD_HEADERS = ["From", "To", "Cc", "Delivered-To", "Message-ID", "References"]


class GmailSendAsRefused(BrokerRefusal):
    """Gmail refused the call (a 4xx): certainly nothing was sent."""


class GmailSendAsTransportError(BrokerRefusal):
    """The call failed with an unknown outcome, or a read could not run."""


def gmail_entries(data: dict[str, Any]) -> list[dict[str, str]]:
    """``scope.staff_send_as`` entries authoring ``via: gmail`` and a mailbox,
    as ``{address, name, mailbox}``, lowercased, in authored order."""
    scope = data.get("scope") if isinstance(data, dict) else None
    raw = scope.get("staff_send_as") if isinstance(scope, dict) else None
    out: list[dict[str, str]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or str(entry.get("via") or "").strip().lower() != VIA_GMAIL:
            continue
        address = normalize_address(entry.get("address"))
        mailbox = normalize_address(entry.get("mailbox"))
        name = entry.get("name")
        if address and mailbox and "@" in address and "@" in mailbox and isinstance(name, str) and name.strip():
            out.append({"address": address, "name": name.strip(), "mailbox": mailbox})
    return out


def rfc_id(value: Any) -> str:
    """An RFC 5322 Message-ID without its angle brackets, or ``""``."""
    if not isinstance(value, str):
        return ""
    bare = value.strip()
    if bare.startswith("<") and bare.endswith(">"):
        bare = bare[1:-1].strip()
    return bare if _RFC_ID.match(bare) else ""


def _headers(message: dict[str, Any]) -> dict[str, str]:
    """A Gmail metadata message's headers, keyed by lowercased name (first wins)."""
    payload = message.get("payload") if isinstance(message, dict) else None
    pairs = payload.get("headers") if isinstance(payload, dict) else None
    out: dict[str, str] = {}
    for pair in pairs if isinstance(pairs, list) else []:
        if isinstance(pair, dict) and isinstance(pair.get("name"), str) and isinstance(pair.get("value"), str):
            out.setdefault(pair["name"].lower(), pair["value"])
    return out


def _addresses(header: str) -> list[str]:
    return [normalize_address(a) for _, a in getaddresses([header]) if normalize_address(a)]


def _classify(exc: Exception) -> BrokerRefusal:
    """A Google client error as this transport's refusal or transport error.

    A 4xx other than 429 means Google refused the request and sent nothing; any
    other failure (5xx, a socket error, an unknown exception) leaves the outcome
    unknown. The vendor's text never travels, only its status (BrokerRefusal)."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None) or getattr(exc, "status_code", None)
    try:
        code = int(status) if status is not None else 0
    except (TypeError, ValueError):
        code = 0
    if 400 <= code < 500 and code != 429:
        return GmailSendAsRefused(f"Gmail refused the send (HTTP {code})")
    return GmailSendAsTransportError(
        f"the Gmail send did not complete ({type(exc).__name__}{f' {code}' if code else ''})"
    )


class GmailSendAs:
    """The ``via: gmail`` send-as transport (``send_as_transport.SendAsTransport``)."""

    via = VIA_GMAIL
    links = False
    refused: type[Exception] = GmailSendAsRefused
    failures: tuple[type[Exception], ...] = (
        GmailSendAsRefused,
        GmailSendAsTransportError,
        OriginRefused,
        AgentMailRefused,
        AgentMailTransportError,
    )

    def __init__(self, broker: BrokerContext) -> None:
        if broker.agentmail is None or not broker.credential_path.is_file():
            # The approval loop rides the seat's AgentMail inbox and the send
            # rides the broker's Google credential: without both, nothing.
            raise ValueError(NOT_CONFIGURED)
        self._broker = broker
        self._agentmail = broker.agentmail

    # -- who --------------------------------------------------------------

    def _entries(self, data: dict[str, Any] | None = None) -> list[dict[str, str]]:
        return gmail_entries(data if data is not None else seat_data(self._broker))

    def _mailbox_of(self, address: str, data: dict[str, Any] | None = None) -> str:
        for entry in self._entries(data):
            if entry["address"] == address:
                return entry["mailbox"]
        return ""

    def approvers_for(self, approver: str, data: dict[str, Any]) -> set[str]:
        """Every address authored for the mailbox ``approver`` belongs to: one
        person known by several addresses. Empty when ``approver`` is off the
        roster, so nobody can send or change a row whose sender left it."""
        mailbox = self._mailbox_of(approver, data)
        return {e["address"] for e in self._entries(data) if mailbox and e["mailbox"] == mailbox}

    def reply_mailbox(self) -> str:
        # No seat address on the Reply-To: the other party is writing to a
        # person, not to SMD's agent (ADR 0089 section 9).
        return ""

    def _service(self, mailbox: str) -> Any:
        if not mailbox:
            raise GmailSendAsTransportError("no Gmail mailbox is authored for that address")
        try:
            return google_service("gmail", "v1", self._broker.credential_path, self._broker.customer_path, mailbox)
        except Exception as exc:
            raise GmailSendAsTransportError(f"the Gmail client could not be built ({type(exc).__name__})") from exc

    # -- propose ----------------------------------------------------------

    def prepare(self, msg: dict[str, Any], request: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """Bind the draft to its thread and choose its From, in code.

        The thread id, the In-Reply-To and References the reply will carry, and
        the sender's authored name all join the canonical message, so the digest
        covers them. On a thread, From is the authored alias the other party
        wrote to; a caller From that disagrees is refused, naming the right one.
        """
        requested = msg["from"]
        mailbox = self._mailbox_of(requested, data)
        if not mailbox:
            raise DraftRefused(f"{requested} has no Gmail mailbox authored on scope.staff_send_as")
        name = next(e["name"] for e in self._entries(data) if e["address"] == requested)
        payload = request.get("payload")
        thread_id = payload.get("thread_id") if isinstance(payload, dict) else None
        bound = {"from_name": name, "thread_id": "", "in_reply_to": "", "references": ""}
        if thread_id in (None, ""):
            return {**msg, **bound}
        if not isinstance(thread_id, str) or not _THREAD_ID.match(thread_id):
            raise DraftRefused("thread_id is not a Gmail thread id")
        messages = self._thread(mailbox, thread_id)
        chosen = self._received_at(messages, self.approvers_for(requested, data), data)
        if chosen != requested:
            raise DraftRefused(f"this thread was received at {chosen}; propose the draft from {chosen}")
        last = _headers(messages[-1])
        answered = rfc_id(last.get("message-id"))
        if not answered:
            raise DraftRefused("the thread's newest message carries no Message-ID to reply to")
        prior = [i for i in (rfc_id(m) for m in _ANGLE.findall(last.get("references", ""))) if i]
        references = " ".join(f"<{i}>" for i in [*prior, answered][-_MAX_REFERENCES:])
        bound.update(thread_id=thread_id, in_reply_to=f"<{answered}>", references=references)
        return {**msg, **bound}

    def _thread(self, mailbox: str, thread_id: str) -> list[dict[str, Any]]:
        try:
            thread = (
                self._service(mailbox)
                .users()
                .threads()
                .get(userId="me", id=thread_id, format="metadata", metadataHeaders=_THREAD_HEADERS)
                .execute()
            )
        except Exception as exc:
            raise DraftRefused(f"the Gmail thread could not be read ({type(exc).__name__})") from exc
        messages = thread.get("messages") if isinstance(thread, dict) else None
        if not isinstance(messages, list) or not messages:
            raise DraftRefused("the Gmail thread holds no messages")
        if not all(isinstance(m, dict) for m in messages):
            raise DraftRefused("the Gmail thread came back in a shape this seat cannot read")
        return messages

    def _received_at(self, messages: list[dict[str, Any]], approvers: set[str], data: dict[str, Any]) -> str:
        """The owner's authored address the other party wrote to, newest first:
        To, then Cc, then Delivered-To. Refused when the thread names none."""
        authored = [e["address"] for e in self._entries(data) if e["address"] in approvers]
        for message in reversed(messages):
            headers = _headers(message)
            sender = _addresses(headers.get("from", ""))
            if sender and sender[0] in approvers:
                continue
            for field in ("to", "cc", "delivered-to"):
                hit = [a for a in _addresses(headers.get(field, "")) if a in authored]
                if hit:
                    return hit[0]
        raise DraftRefused("this thread was not received at any address authored for that mailbox")

    # -- send -------------------------------------------------------------

    def _mime(self, msg: dict[str, Any], from_address: str, token: str) -> EmailMessage:
        mime = EmailMessage()
        mime["From"] = formataddr((str(msg.get("from_name") or ""), from_address))
        mime["To"] = ", ".join(msg["to"])
        if msg.get("cc"):
            mime["Cc"] = ", ".join(msg["cc"])
        mime["Subject"] = msg["subject"]
        reply_to = [a for a in msg.get("reply_to") or [] if a != from_address]
        if reply_to:
            mime["Reply-To"] = ", ".join(msg.get("reply_to") or [])
        if msg.get("in_reply_to"):
            mime["In-Reply-To"] = msg["in_reply_to"]
            mime["References"] = msg.get("references") or msg["in_reply_to"]
        mime["Message-ID"] = make_msgid(domain=from_address.partition("@")[2] or None)
        mime[AUDIT_ROW_HEADER] = token
        mime.set_content(msg["body_text"])
        mime.add_alternative(msg.get("html") or "", subtype="html")
        return mime

    def send_as(self, msg: dict[str, Any], from_address: str) -> dict[str, Any]:
        """Transmit an approved row's exact message from the owner's mailbox.

        Not a verb and not fenced here, for the reason ``MsGraphOps.send_as_staff``
        gives: the only caller has bound this payload to a row its approver
        approved by digest and consumed once. Google enforces the other half: the
        From must be the mailbox or one of its verified send-as aliases.
        """
        recipients = collect_recipients(msg)
        if not recipients:
            raise GmailSendAsRefused("refusing a send with no recipient")
        mailbox = self._mailbox_of(from_address)
        token = new_row_token()
        mime = self._mime(msg, from_address, token)
        body: dict[str, Any] = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")}
        if msg.get("thread_id"):
            body["threadId"] = msg["thread_id"]
        service = self._service(mailbox)
        try:
            sent = service.users().messages().send(userId="me", body=body).execute()
        except Exception as exc:
            raise _classify(exc) from exc
        gmail_id = str((sent or {}).get("id") or "") if isinstance(sent, dict) else ""
        if not gmail_id:
            raise GmailSendAsTransportError("Gmail answered the send without a message id")
        rfc = self._sent_rfc_id(service, gmail_id) or rfc_id(mime["Message-ID"])
        return {
            "message_id": "",
            "recipients": recipients,
            "mailbox": mailbox,
            "audit_row_token": token,
            "vendor_message_id": gmail_id,
            "gmail_message_id": gmail_id,
            "gmail_thread_id": str(sent.get("threadId") or ""),
            "rfc_message_id": rfc,
            "lookup": gmail_id,
        }

    @staticmethod
    def _sent_rfc_id(service: Any, gmail_id: str) -> str:
        """The Message-ID Gmail stored on the sent message, or ``""``. Never raises:
        the send is out, and the id we stamped is the fallback."""
        try:
            found = (
                service.users()
                .messages()
                .get(userId="me", id=gmail_id, format="metadata", metadataHeaders=["Message-ID"])
                .execute()
            )
            return rfc_id(_headers(found).get("message-id"))
        except Exception:  # noqa: BLE001 - the send is out; a failed id lookup falls back, never raises
            return ""

    def conversation_of(self, result: dict[str, Any]) -> str:
        return str(result.get("rfc_message_id") or "")

    def reply_keys(self, request: dict[str, Any]) -> list[str]:
        """The RFC ids an inbound names as what it answers (In-Reply-To, then
        References, newest first), the keys a dispatched Gmail row is found by."""
        keys: list[str] = []
        for field in ("in_reply_to", "references"):
            value = request.get(field)
            found = _ANGLE.findall(value) if isinstance(value, str) else []
            if isinstance(value, str) and not found:
                found = value.split()
            for raw in reversed(found) if field == "references" else found:
                key = rfc_id(raw)
                if key and key not in keys:
                    keys.append(key)
        return keys[:_MAX_REFERENCES]

    # -- answer -----------------------------------------------------------

    def check_answer_origin(self, request: dict[str, Any], approver: str) -> None:
        """Pass only when the answer is in the approver's SENT mail and is not a
        message this broker sent. Raises ``OriginRefused`` on a no and
        ``GmailSendAsTransportError`` when the look itself fails."""
        wanted = rfc_id(request.get("internet_message_id"))
        if not wanted:
            raise GmailSendAsTransportError("no internet message id on the approval; cannot check its origin")
        service = self._service(self._mailbox_of(approver))
        try:
            listed = (
                service.users().messages().list(userId="me", q=f"rfc822msgid:{wanted} in:sent", maxResults=5).execute()
            )
            hits = [h for h in (listed or {}).get("messages") or [] if isinstance(h, dict) and h.get("id")]
            matched = [h for h in hits[:5] if self._is_owners(service, str(h["id"]), wanted)]
        except OriginRefused:
            raise
        except Exception as exc:
            raise GmailSendAsTransportError(
                f"the approver's Sent mail could not be searched ({type(exc).__name__})"
            ) from exc
        if not matched:
            raise OriginRefused("that answer is not in the approver's Sent mail")

    def _is_owners(self, service: Any, gmail_id: str, wanted: str) -> bool:
        """Whether one SENT hit is the owner's own answer. A broker-sent message
        (audit header, or a Gmail id a row recorded) refuses outright."""
        found = (
            service.users()
            .messages()
            .get(userId="me", id=gmail_id, format="metadata", metadataHeaders=["Message-ID", AUDIT_ROW_HEADER])
            .execute()
        )
        headers = _headers(found)
        if headers.get(AUDIT_ROW_HEADER.lower()) is not None or self._broker_sent(gmail_id):
            raise OriginRefused("that answer was sent by the Operator, not by the approver")
        return rfc_id(headers.get("message-id")) == wanted

    def _broker_sent(self, gmail_id: str) -> bool:
        """Whether a send-as row recorded this Gmail id as one the broker sent.
        Raises when the table cannot be read, so the caller refuses."""
        db = sqlite3.connect(str(self._broker.audit_db_path), timeout=10)
        try:
            row = db.execute("SELECT 1 FROM send_as_acts WHERE result = ? LIMIT 1", (gmail_id,)).fetchone()
        finally:
            db.close()
        return row is not None

    # -- the seat's own mail ------------------------------------------------

    def notify(self, payload: dict[str, Any], session_id: str = "") -> dict[str, Any]:
        """An approval email or notice, from the seat's AgentMail inbox, through
        the audited dispatch (recipient-fenced, CONFIRM_SEND_* rows)."""
        message = {"to": payload.get("to"), "subject": payload.get("subject"), "text": payload.get("body_text")}
        if payload.get("html"):
            message["html"] = payload["html"]
        agentmail = self._agentmail
        return dispatch_transmit(
            self._broker,
            "agentmail_send",
            {"payload": message, "session_id": session_id},
            send=agentmail.send,
            reply=agentmail.reply,
            refused=AgentMailRefused,
            transport=AgentMailTransportError,
            attempted_for_send=agentmail_recipients,
            identity_key="inbox_id",
            channel="agentmail",
            internal_lane=LANE_SEND_AS,
        )


__all__ = [
    "GmailSendAs",
    "GmailSendAsRefused",
    "GmailSendAsTransportError",
    "gmail_entries",
    "google_service",
    "rfc_id",
]
