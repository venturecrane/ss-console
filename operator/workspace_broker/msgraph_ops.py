"""Microsoft Graph transmit operations executed inside the broker security domain.

The msgraph sibling of ``agentmail_ops``, deliberately the same shape: two verbs,
both fail-closed, both fencing the recipient against the seat's authored
counterparty surface before the credential is used, and both leaving a row that
the credential holder writes rather than the caller.

* ``send``  — a fresh message via ``POST /users/{mailbox}/sendMail``. Every
  recipient must be on the seat's authored surface; the From is the mailbox
  pinned in customer.yaml.
* ``reply`` — an answer via ``POST /users/{mailbox}/messages/{id}/reply``. Graph
  derives the recipients from the source message, so the check that matters is on
  the ORIGINAL SENDER, which the broker fetches itself. Without that check the
  reply lane is an exfiltration primitive: anyone on the internet may email the
  operator mailbox, so "reply to whoever wrote in" reaches arbitrary unapproved
  addresses — and would do it carrying a clean audit row, which is worse than no
  row at all.

TWO CREDENTIALS, ONE VERB (overlay#280). Under the two-app fence the send app
holds ``Mail.Send`` only, so the sender-verification GET above can never succeed
on the send credential — 403 by construction, observed live on the first
production two-app seat. The GET therefore runs on the READ app's credential
(the same registration the agent already holds; reading is the lower privilege)
from a second broker-store file, and the POST stays on the send credential.
Absent read credential ⇒ the reply fails closed: verifying the sender is the
load-bearing step and must not be skipped or delegated to the caller.

WHY THE BROKER SPEAKS GRAPH ITSELF rather than importing the overlay's client:
the overlay runs in the agent's address space and this process exists to be
outside it. A shared import would be a shared dependency across the boundary the
whole design draws. The subset needed here is small — mint a token, POST twice,
GET once — and it is written against the same endpoints the overlay client uses,
so the two agree on the wire without agreeing on code.

EVERY SEND CARRIES ITS OWN AUDIT KEY (ss#2499). Graph answers both verbs with
202 and no body, so until now a msgraph transmit wrote a row with no vendor id
at all -- 9 of 9 ``CONFIRM_SEND_DISPATCHED`` rows with an empty ``message_id``
and 8 of 8 ``REPLY_SENT`` rows reading "(sent via msgraph, id unavailable)".
A row that cannot be joined to the mailbox cannot answer the one question a firm
ever asks an audit log about a message it did not expect -- "is this one of
yours?" -- which is the question ``operator/bin/reconcile-sends.py`` exists to
settle and could not ask on this channel at all.

So the broker stamps an ``X-SMD-Audit-Row`` internet header carrying a ULID
minted for the row it is about to write, and then LOOKS THE MESSAGE UP in Sent
Items on the read credential, keyed on that header, to learn the two ids Graph
declined to hand back. Two independent joins come out of that, which is the
point: the vendor id if the lookup succeeded, and the header itself either way.
A lookup that fails leaves the row still joinable from the mailbox side, and it
is RECORDED on the row (``lookup: failed: ...``) rather than left blank, because
a blank id and an unrecorded failure are indistinguishable from outside and the
second one is the state this whole control exists to end.

Policy refusals raise ``MsGraphRefused``; anything else raises
``MsGraphTransportError``. The distinction matters: a refusal is a decision worth
auditing as such, while a transport failure must never be recorded as though the
seat had been forbidden to write.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .audit_ledger import new_row_token
from .msgraph_auth import load_credential, seat_mailbox
from .recipient_policy import RecipientPolicy, authored_policy, normalize_address, sender_key

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_HOST = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TIMEOUT_S = 15.0

#: Re-mint this far before the stated expiry, so a token that is valid when we
#: check is still valid when Graph reads it.
_TOKEN_SKEW_S = 60

#: Every field that can put an address on the wire. The fence enumerates THIS
#: tuple, and ``_message`` below builds the Graph payload from the same one, so a
#: recipient field cannot exist in the message without having been checked. Adding
#: one in only a single place is the mistake this pairing exists to prevent.
_RECIPIENT_FIELDS = ("to", "cc", "bcc")

#: Body-text under either spelling the two callers use. The out-of-band confirm
#: dispatch carries ``body_text`` (the flat ``mcp_msgraph_mail_send_message`` arg
#: shape, ADR 0078 D4); the ``smd_send_message`` tool carries ``text`` (the shape
#: its schema advertises). Accepting both is what lets ONE broker verb serve both
#: callers rather than a second verb that can drift from this one.
_TEXT_FIELDS = ("body_text", "text")

#: The header every broker-sent message carries, and the exact key the
#: console-side reconciler joins on (ss#2499). Graph accepts custom headers only
#: when they begin ``x-`` and only at message-creation time; both hold here.
#: Compared case-insensitively on the way back, because header names are
#: case-insensitive per RFC5322 and Exchange is free to re-case them -- a
#: case-sensitive compare would simply never match and would look like an absent
#: header rather than a broken one.
AUDIT_ROW_HEADER = "X-SMD-Audit-Row"

#: The well-known folder id for Sent Items. Not a display name: display names are
#: localized, so a tenant in another language would 404 on "Sent Items".
SENT_ITEMS_FOLDER = "sentitems"

#: ``internetMessageHeaders`` is not returned unless it is selected by name --
#: an omission that would make every lookup silently find nothing.
_SENT_LOOKUP_SELECT = "id,internetMessageId,internetMessageHeaders,conversationId,sentDateTime"

#: How many of the newest sent messages one lookup reads. The message being
#: looked up was sent seconds ago, so it is at the head of this list; the depth
#: is headroom for a busy mailbox, not a search window.
_SENT_LOOKUP_TOP = 25

#: Graph ACCEPTS a send (202) before the copy reaches Sent Items, so the first
#: read can legitimately miss. Bounded and short on purpose: this runs inside a
#: transmit verb, and unbounded polling here turns a slow mailbox into a hung
#: agent turn.
_SENT_LOOKUP_BACKOFF_S = (0.5, 1.5, 3.0)


class MsGraphRefused(RuntimeError):
    """The authored policy forbids this send. Never retried, always audited."""


class MsGraphTransportError(RuntimeError):
    """The send could not be attempted or its outcome is unknown.

    ``status`` carries the HTTP status when Graph answered with one, and is
    ``None`` when the failure was below HTTP (DNS, timeout, a torn socket). The
    reply lane reads it to tell "Graph rejected this request body" (400, nothing
    was sent, safe to re-shape and retry once) apart from every other failure,
    where re-sending could deliver the same message twice.
    """

    status: int | None = None


def _as_addresses(value: Any) -> list[str]:
    """Normalize a recipient field that may be a bare string, a list, or Graph's
    own ``{"emailAddress": {"address": …}}`` nesting."""
    if isinstance(value, str):
        candidates: list[Any] = [value]
    elif isinstance(value, list):
        candidates = list(value)
    elif value is None:
        return []
    elif isinstance(value, dict):
        candidates = [value]
    else:
        raise MsGraphRefused(
            f"recipient field must be a string, list, or address object, got {type(value).__name__}"
        )
    return [normalize_address(v) for v in candidates if normalize_address(v)]


def collect_recipients(payload: dict[str, Any]) -> list[str]:
    """Every address this payload would reach, across to/cc/bcc.

    ``bcc`` is here because it DELIVERS. A fence that reads only the visible
    recipients would pass a message whose blind copy goes anywhere, and it would
    write an audit row naming the wrong set of people — a row that looks clean
    and is wrong, which is worse than a missing one.
    """
    found: list[str] = []
    for field in _RECIPIENT_FIELDS:
        for address in _as_addresses(payload.get(field)):
            if address not in found:
                found.append(address)
    return found


def enforce_recipients(policy: RecipientPolicy, recipients: list[str]) -> None:
    """Refuse unless EVERY recipient is on the seat's authored surface.

    All-or-nothing on purpose: a partial send is not a safer send, and dropping
    the offending address silently would deliver a message whose visible
    recipient list is a lie.
    """
    if not recipients:
        raise MsGraphRefused("refusing a send with no recipient")
    refused = [a for a in recipients if not policy.allows_recipient(a)]
    if refused:
        raise MsGraphRefused(
            f"{len(refused)} recipient(s) are not on this seat's authored "
            "counterparty surface (scope.outbound_roster + inbound_allow_from + "
            "admins, minus domain_blocks). A seat may only write to people its "
            "own configuration names (ss#2258): " + ", ".join(sorted(refused))
        )


#: Characters a Graph id may legitimately contain. Graph message ids are a
#: URL-safe base64 variant, so alphanumerics plus ``-_=`` covers them; ``.`` and
#: ``~`` are permitted because they are unreserved in a path and harmless.
#: Everything else — most importantly ``/``, ``?`` and ``#`` — is refused.
_SEGMENT_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_=.~"
)


def _safe_segment(value: str) -> str:
    """A path segment that cannot restructure the URL it is placed in.

    The segments reaching here are a fixed verb (``sendMail``, ``messages``,
    ``reply``) and one caller-supplied message id. Since segments are passed to
    Graph raw, an id carrying ``/`` would silently add a path element and an id
    carrying ``?`` would start a query string — so the id is validated instead of
    escaped, and an invalid one is a refusal rather than a request that quietly
    addresses something else.
    """
    if not value or any(ch not in _SEGMENT_ALLOWED for ch in value):
        raise MsGraphRefused(
            "refusing a Graph path segment with characters outside the id "
            f"alphabet: {value!r}"
        )
    return value


def _audit_headers(audit_token: str) -> list[dict[str, str]]:
    """The one custom header a broker-sent message carries, or nothing.

    An empty token yields an empty list rather than a header with an empty value:
    a header that is present and blank matches nothing on the way back and reads,
    to anyone examining the message, as a stamped send whose key was lost.
    """
    if not audit_token:
        return []
    return [{"name": AUDIT_ROW_HEADER, "value": audit_token}]


def _audit_header_of(message: Any) -> str:
    """The ``X-SMD-Audit-Row`` value on a Graph message, or ``""``.

    Case-insensitive by RFC5322: Exchange re-cases header names freely, and a
    case-sensitive compare would report every message as unstamped -- a broken
    instrument that looks exactly like a clean mailbox.
    """
    if not isinstance(message, dict):
        return ""
    headers = message.get("internetMessageHeaders")
    if not isinstance(headers, list):
        return ""
    wanted = AUDIT_ROW_HEADER.lower()
    for header in headers:
        if isinstance(header, dict) and str(header.get("name") or "").lower() == wanted:
            return str(header.get("value") or "")
    return ""


def _recipients(addresses: Any) -> list[dict[str, Any]]:
    """Flat addresses → Graph's ``toRecipients``/``ccRecipients`` nesting."""
    items = [addresses] if isinstance(addresses, str) else list(addresses or [])
    return [
        {"emailAddress": {"address": str(a).strip()}} for a in items if str(a or "").strip()
    ]


class MsGraphOps:
    """Transmit operations bound to one seat's pinned mailbox and authored policy."""

    def __init__(
        self,
        credential_path: Path,
        customer_path: Path,
        *,
        read_credential_path: Path | None = None,
        graph_base: str = GRAPH_BASE,
        token_host: str = TOKEN_HOST,
        opener: Any | None = None,
        sleep: Any | None = None,
    ) -> None:
        # Injected for the same reason ``opener`` is: the Sent Items lookup waits
        # between attempts, and a test that really slept would either be slow or
        # would tempt someone to shrink the backoff to a value the live path
        # cannot use.
        self._sleep = sleep or time.sleep
        self._credential_path = credential_path
        self._read_credential_path = read_credential_path
        self._customer_path = customer_path
        self._graph_base = graph_base.rstrip("/")
        self._token_host = token_host.rstrip("/")
        self._opener = opener
        # One token cache PER CREDENTIAL FILE. A shared cache would let reply()'s
        # read-token mint (the GET runs first) leak onto the send POST — the
        # send would then carry a token that cannot send, failing at Graph with
        # a confusing 403 that looks like the fence misfiring.
        self._tokens: dict[str, tuple[str, float]] = {}

    # -- identity ----------------------------------------------------------

    def mailbox(self) -> str:
        """The seat's own operator mailbox, re-read from config on every call.

        Not cached: customer.yaml is root-owned and can be re-applied under a
        running broker, and a stale mailbox would mean sending as an identity the
        seat no longer holds. The read is a local file; the send is a network
        round trip.
        """
        address = seat_mailbox(self._customer_path)
        if not address:
            raise MsGraphTransportError(
                "this seat authors no msgraph mailbox (connectors.Email.msgraph_auth); "
                "refusing to send"
            )
        return address

    # -- transport ---------------------------------------------------------

    def _bearer(self, credential_path: Path | None = None, *, role: str = "send") -> str:
        path = credential_path or self._credential_path
        cache_key = str(path)
        cached = self._tokens.get(cache_key)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        credential = load_credential(path)
        if not credential:
            raise MsGraphTransportError(
                f"no msgraph {role} credential in the broker store; refusing to {role}"
            )
        data = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": credential["client_id"],
                "client_secret": credential["client_secret"],
                "scope": GRAPH_SCOPE,
            }
        ).encode()
        url = f"{self._token_host}/{credential['tenant_id']}/oauth2/v2.0/token"
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            opener = self._opener or urllib.request.urlopen
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with opener(request, timeout=TIMEOUT_S) as response:
                raw = response.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as exc:
            # The token endpoint echoes request parameters in its error bodies,
            # and one of those parameters is the client secret. Status only.
            raise MsGraphTransportError(
                f"msgraph token mint rejected with HTTP {exc.code}"
            ) from exc
        except Exception as exc:
            raise MsGraphTransportError(f"msgraph token mint failed: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise MsGraphTransportError("msgraph token response was not JSON") from exc
        token = parsed.get("access_token") if isinstance(parsed, dict) else None
        if not isinstance(token, str) or not token:
            raise MsGraphTransportError("msgraph token response carried no access_token")
        expires_in = parsed.get("expires_in")
        lifetime = expires_in if isinstance(expires_in, int) else 3600
        deadline = time.monotonic() + max(lifetime - _TOKEN_SKEW_S, 0)
        self._tokens[cache_key] = (token, deadline)
        return token

    def _request(
        self,
        path: str,
        method: str,
        body: dict[str, Any] | None,
        *,
        credential_path: Path | None = None,
        role: str = "send",
    ) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self._graph_base + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._bearer(credential_path, role=role)}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            opener = self._opener or urllib.request.urlopen
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with opener(request, timeout=TIMEOUT_S) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # The status rides on the exception because ONE caller must branch on
            # it: a 400 means Graph rejected the request body and sent nothing,
            # which is the only failure it is safe to re-shape and retry. Parsing
            # it back out of the message string would be a second, silent
            # encoding of the same fact.
            failure = MsGraphTransportError(
                f"msgraph {method} {path} failed: HTTP {exc.code}"
            )
            failure.status = exc.code
            raise failure from exc
        except Exception as exc:
            raise MsGraphTransportError(f"msgraph {method} {path} failed: {exc}") from exc
        # sendMail and reply answer 202 with no body; that is success, not a
        # malformed response, so an empty payload must not raise here.
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise MsGraphTransportError("msgraph returned non-JSON") from exc
        return parsed if isinstance(parsed, dict) else {}

    def _mail_path(self, *parts: str) -> str:
        """A path under the PINNED mailbox, built from RAW segments.

        Nothing here is percent-encoded, and that is a deliberate match to the
        live-proven wire format rather than a convenience. The overlay's Graph
        client and the sandbox-verified connector both pass these segments raw,
        and the client says why for the mailbox: Graph wants the bare address and
        encoding the ``@`` breaks the route. Message ids ride the same way there.
        Encoding them here on a guess would mean this process speaks a different
        wire format from the one that was actually proven against the tenant —
        and the failure would appear as a 404 on a reply, at runtime, on a client
        seat.

        Safety therefore comes from ``_safe_segment`` below rather than from
        quoting: a caller-supplied id that could restructure the path is refused
        outright, which is the stronger control anyway. Encoding turns a
        traversal attempt into a lookup that merely fails; refusing it makes the
        attempt itself visible in the ledger.
        """
        suffix = "/".join(_safe_segment(p) for p in parts)
        return f"/users/{self.mailbox()}/{suffix}"

    # -- verbs -------------------------------------------------------------

    def _message(self, payload: dict[str, Any], audit_token: str = "") -> dict[str, Any]:
        """The Graph ``message`` resource, built from a CLOSED set of fields.

        Nothing reaches the wire that is not named here, so an internal key — a
        broker grant, an approval marker, a caller's bookkeeping — cannot ride
        along, and no caller can smuggle a ``from``/``sender``/``mailbox``
        override past the identity pinned from customer.yaml.

        ``internetMessageHeaders`` is on that closed list but is NOT a caller
        field: the value comes from ``audit_token``, minted by the verb one frame
        up. A caller that could set this header could stamp its own send with
        another send's audit key, which is precisely the join the reconciler
        trusts (ss#2499).
        """
        html = payload.get("html")
        if isinstance(html, str) and html.strip():
            body = {"contentType": "HTML", "content": html}
        else:
            text = next(
                (
                    str(payload[key])
                    for key in _TEXT_FIELDS
                    if isinstance(payload.get(key), str) and payload[key]
                ),
                "",
            )
            body = {"contentType": "Text", "content": text}
        message: dict[str, Any] = {
            "subject": str(payload.get("subject") or ""),
            "body": body,
            "toRecipients": _recipients(payload.get("to")),
        }
        for field, graph_key in (("cc", "ccRecipients"), ("bcc", "bccRecipients")):
            nested = _recipients(payload.get(field))
            if nested:
                message[graph_key] = nested
        # `replyTo` names where a REPLY should go, not where this message goes, so
        # it delivers nothing and is not fenced — matching the AgentMail verb,
        # which passes its `reply_to` through the same way. Worth knowing it is a
        # deliberate parity call and not an oversight.
        reply_to = _recipients(payload.get("reply_to"))
        if reply_to:
            message["replyTo"] = reply_to
        headers = _audit_headers(audit_token)
        if headers:
            message["internetMessageHeaders"] = headers
        return message

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fence every recipient, then transmit from this seat's pinned mailbox."""
        policy = authored_policy(self._customer_path)
        recipients = collect_recipients(payload)
        enforce_recipients(policy, recipients)
        mailbox = self.mailbox()
        audit_token = new_row_token()
        self._request(
            self._mail_path("sendMail"),
            "POST",
            {"message": self._message(payload, audit_token), "saveToSentItems": True},
        )
        return {
            # Graph still answers sendMail with 202 and no body, so this stays
            # empty: it is the id the CALL returned, and the call returned none.
            # The id that identifies this message is resolved after the fact,
            # below, and travels as ``vendor_message_id`` (the field name ss#2497
            # introduced) rather than being back-filled here, so a reader can
            # always tell what the vendor said from what we went and looked up.
            "message_id": "",
            "recipients": recipients,
            "mailbox": mailbox,
            "audit_row_token": audit_token,
            **self._locate_sent(audit_token),
        }

    def _locate_sent(self, audit_token: str, *, conversation_id: str = "") -> dict[str, str]:
        """Find the message just sent in Sent Items, by the header stamped on it.

        ON THE READ CREDENTIAL, NECESSARILY AND RIGHTLY. Under the two-app fence
        the send app holds ``Mail.Send`` only (overlay#280), so it cannot list a
        folder at all and this GET would 403 by construction. It is also the
        correct posture on its own terms: locating a message is a read, and the
        service agreement's 4.6 read surface is exactly this one mailbox.

        THIS NEVER RAISES, and that is the load-bearing property. The message is
        already gone. Turning a lookup failure into a transport error would tell
        the caller its send failed when it did not, and a caller that retries a
        delivered message delivers it twice -- trading a missing id for a
        duplicate message to a client. So the failure is RECORDED (``lookup``
        lands on the audit row) rather than raised or, worse, left as a blank id
        indistinguishable from the state ss#2499 exists to end.

        The reply lane passes the ``conversationId`` it already fetched. It is
        used as a CROSS-CHECK on the match, never as the match itself: a
        conversation holds many messages and the audit header holds exactly one,
        so keying on the conversation would be a heuristic wearing an exact
        match's clothes. A disagreement is reported, never silently preferred.
        """
        if self._read_credential_path is None:
            # Not a failure of the lookup -- there is no credential to look with.
            # Named distinctly so a seat provisioned single-app reads as
            # unequipped rather than as a mailbox that lost a message.
            return {"lookup": "skipped: no msgraph read credential staged on this seat"}
        path = (
            self._mail_path("mailFolders", SENT_ITEMS_FOLDER, "messages")
            + f"?$select={_SENT_LOOKUP_SELECT}"
            + f"&$top={_SENT_LOOKUP_TOP}"
            + "&$orderby="
            + urllib.parse.quote("sentDateTime desc", safe="")
        )
        reason = f"{AUDIT_ROW_HEADER} not on any of the newest {_SENT_LOOKUP_TOP} sent items"
        for pause in _SENT_LOOKUP_BACKOFF_S:
            # Sleep BEFORE the first read, not only between reads: Graph accepts
            # the send asynchronously, so an immediate read races the mailbox and
            # the first attempt would be spent on a near-certainty.
            self._sleep(pause)
            try:
                page = self._request(
                    path,
                    "GET",
                    None,
                    credential_path=self._read_credential_path,
                    role="read",
                )
            except MsGraphTransportError as exc:
                reason = str(exc)
                continue
            value = page.get("value")
            found = next(
                (
                    m
                    for m in (value if isinstance(value, list) else [])
                    if _audit_header_of(m) == audit_token
                ),
                None,
            )
            if found is None:
                continue
            located = {
                "vendor_message_id": str(found.get("internetMessageId") or ""),
                "graph_message_id": str(found.get("id") or ""),
                "lookup": "ok",
            }
            thread = str(found.get("conversationId") or "")
            if conversation_id and thread and thread != conversation_id:
                located["lookup"] = "ok: matched a message on a different conversation"
            return located
        return {"lookup": f"failed: {reason}"}

    def reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Answer an inbound message, but only one from an allowed sender.

        The recipient is structural — Graph derives it from the source message —
        so the check that matters is on the ORIGINAL SENDER, fetched here rather
        than taken from the caller. A caller that could name the sender could name
        any sender.

        The fetch runs on the READ credential: under the two-app fence the send
        app cannot read (overlay#280), and skipping or delegating the fetch is
        not an option — it is the load-bearing check. No read credential means
        no reply, fail-closed. Scope honesty: this GET defends against a caller
        naming the sender; it does not defend against an agent that PATCHes the
        stored message's from/replyTo with its own Mail.ReadWrite — that is a
        pre-existing property of replying to mutable message objects.
        """
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id:
            raise MsGraphRefused("reply requires the source message_id")
        comment = str(payload.get("comment") or "").strip()
        html = str(payload.get("html") or "").strip()
        if not (comment or html):
            raise MsGraphRefused("refusing to send an empty reply")
        if self._read_credential_path is None:
            raise MsGraphTransportError(
                "reply requires the broker read credential to verify the sender "
                "under the two-app fence (overlay#280); this seat staged none — "
                "reprovision materializes it"
            )
        # ``$select`` rather than the default projection, and the list is a
        # contract: ``from``/``sender`` are what the fence reads, and
        # ``conversationId`` is what the Sent Items lookup cross-checks its match
        # against (ss#2499). Naming them makes the dependency visible to whoever
        # edits this next, instead of leaving it to a default that could narrow.
        source = self._request(
            self._mail_path("messages", message_id)
            + "?$select=id,from,sender,conversationId",
            "GET",
            None,
            credential_path=self._read_credential_path,
            role="read",
        )
        sender = normalize_address(source.get("from") or source.get("sender"))
        if not sender:
            raise MsGraphRefused(
                f"cannot determine who sent message {message_id!r}; refusing to reply"
            )
        policy = authored_policy(self._customer_path)
        if not policy.allows_reply_to(sender):
            raise MsGraphRefused(
                "the sender of that message is not on scope.inbound_allow_from, so "
                "this seat may not answer it. Anyone can email this mailbox; only "
                "authored senders get replies (ss#2258)"
            )
        conversation_id = str(source.get("conversationId") or "")
        audit_token = new_row_token()
        reply_path = self._mail_path("messages", message_id, "reply")
        stamped = True
        try:
            self._request(reply_path, "POST", self._reply_body(comment, html, audit_token))
        except MsGraphTransportError as exc:
            # ONE retry, and only on 400. Graph's reference says the ``message``
            # parameter takes "any writeable properties for the reply", and
            # ``internetMessageHeaders`` is writeable at creation -- but the
            # combination has never been observed on the wire from this seat, and
            # the reply lane is a client-facing path. A 400 means Graph rejected
            # the request BODY and sent nothing, which is the only failure it is
            # safe to re-shape and repeat; every other failure may have delivered,
            # so it propagates rather than risking the same message twice.
            if getattr(exc, "status", None) != 400:
                raise
            stamped = False
            self._request(reply_path, "POST", self._reply_body(comment, html, ""))
        located = (
            self._locate_sent(audit_token, conversation_id=conversation_id)
            if stamped
            else {
                "lookup": (
                    f"failed: Graph refused {AUDIT_ROW_HEADER} on /reply (HTTP 400); "
                    "the reply was re-sent unstamped and cannot be located by id"
                )
            }
        )
        return {
            "message_id": "",
            "recipients": [sender],
            "mailbox": self.mailbox(),
            # Empty when the header was refused, so the row never claims a key
            # that is not on the message. The reconciler reads a blank token as
            # "no exact join available here", which is true.
            "audit_row_token": audit_token if stamped else "",
            **located,
            # ss#2497. The broker is the ONLY party that knows who this answered:
            # it fetched the source message itself precisely because a caller
            # naming the sender could name any sender. So the row's join to the
            # INBOUND_RECEIVED row is minted here, from the verified sender, and
            # hashed because the ledger must not hold an address.
            "sender_key": sender_key(sender),
        }

    @staticmethod
    def _reply_body(comment: str, html: str, audit_token: str = "") -> dict[str, Any]:
        """The ``/reply`` request body: an HTML body when one was rendered,
        otherwise today's bare comment.

        ss#2489 — WHY THIS IS NOT COSMETIC. Graph composes the reply message IN
        HTML (its own reference says so where it explains ``Prefer:
        outlook.timezone``), so a plain-text ``comment`` is dropped into an HTML
        body and every newline in it collapses. Live on hermes-ashton-price
        2026-08-20: four replies reached the firm as one unbroken block. The raw
        MIME named the cause — the text/html part carried the text inline with
        ZERO ``<br>``, and text/plain was that HTML with the tags stripped.

        ``comment`` and ``message.body`` are mutually exclusive: Graph answers
        400 when both are present, so this returns one or the other and never
        merges them.

        WHAT WE GIVE UP, STATED RATHER THAN DISCOVERED LATER. The ``comment``
        form produces a reply that carries the quoted original beneath it.
        Whether Graph still appends that quote when the caller supplies
        ``message.body`` is not documented, and this code does not assume either
        way — the first live reply settles it. If the quote is gone and the firm
        wants it, the fallback is ``createReply`` + ``PATCH`` + ``send``, which
        prepends to the draft Graph already built. That was not taken here
        because it is three calls where one will do, and a partial failure
        strands a half-written draft in the client's own Drafts folder.

        The recipient lock is untouched either way: Graph derives the recipients
        from the source message and this body sets none.

        ss#2499 -- THE AUDIT HEADER, AND WHAT IS AND IS NOT PROVEN. The ``message``
        object on ``/reply`` is live-proven on this tenant: it is how the HTML
        body above reaches the firm (``vfy_01M0H94SS3ETAV5B12P0KPHMMR``, a real
        Operator reply read back out of the mailbox as MIME). Graph's own
        reference says the parameter carries "any writeable properties for the
        reply", and ``internetMessageHeaders`` is writeable at creation, so
        stamping it here is documented rather than guessed. What is NOT proven is
        the pairing on the bare-comment path -- ``comment`` beside a ``message``
        that carries only headers. That one is documented-accepted and untested
        on the wire, which is why the caller retries once unstamped on a 400
        instead of letting a client's reply fail on a doc-derived assumption.
        """
        headers = _audit_headers(audit_token)
        if html:
            message: dict[str, Any] = {"body": {"contentType": "HTML", "content": html}}
            if headers:
                message["internetMessageHeaders"] = headers
            return {"message": message}
        body: dict[str, Any] = {"comment": comment}
        if headers:
            # Headers only -- never a body here. ``comment`` and ``message.body``
            # are mutually exclusive (Graph answers 400), and that exclusion is
            # about the BODY, not about the message object itself.
            body["message"] = {"internetMessageHeaders": headers}
        return body
