"""AgentMail transmit operations executed inside the broker security domain.

Every outbound message a seat sends passes through here, and nothing else on the
Machine holds a credential that can transmit (ss#2258 — see ``agentmail_auth``
for the full rationale). Two verbs, both fail-closed:

* ``send``  — a fresh message. Every recipient must be on the seat's authored
  counterparty surface, and the From is pinned from config.
* ``reply`` — an answer to an inbound message. The broker fetches the source
  message itself and checks the ORIGINAL SENDER against ``inbound_allow_from``.
  Without that check the reply lane would be an exfiltration primitive: anyone on
  the internet may email a seat's inbox, so "reply to whoever wrote in" reaches
  arbitrary unapproved addresses — and would do so carrying a clean audit row,
  which is worse than no row at all.

Policy refusals raise ``AgentMailRefused``; anything else raises
``AgentMailTransportError``. The distinction matters to the caller: a refusal is a
decision worth auditing as such, while a transport failure must never be recorded
as though the seat had been forbidden to write.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .agentmail_auth import (
    RecipientPolicy,
    authored_policy,
    load_send_key,
    normalize_address,
    seat_inbox_address,
    sender_key,
)

API_BASE = "https://api.agentmail.to/v0"
TIMEOUT_S = 15.0

#: The only fields forwarded to AgentMail. A closed allowlist so no internal key
#: (a grant, an approval marker, a caller's bookkeeping) can ride the wire, and
#: so a caller cannot smuggle a ``from``/``inbox`` override past the pinned identity.
_BODY_FIELDS = ("to", "cc", "bcc", "subject", "text", "html", "reply_to")
_RECIPIENT_FIELDS = ("to", "cc", "bcc")


class AgentMailRefused(RuntimeError):
    """The authored policy forbids this send. Never retried, always audited."""


class AgentMailTransportError(RuntimeError):
    """The send could not be attempted or its outcome is unknown."""


def _message_id(response: dict[str, Any]) -> str:
    """AgentMail's id, under either spelling it uses.

    The reply endpoint documents ``messageId`` and the send endpoint is read as
    ``message_id`` by the two existing overlay call sites — they disagree with
    each other. Accepting both is what keeps the id out of the audit row from
    being empty, and an empty id would silently break the console reconciler's
    exact-match join, which is the backstop for this entire control.
    """
    for key in ("message_id", "messageId"):
        found = response.get(key)
        if isinstance(found, str) and found:
            return found
    return ""


def _as_addresses(value: Any) -> list[str]:
    """Normalize a recipient field that may be a bare string or a list."""
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [v for v in value if isinstance(v, str)]
    elif value is None:
        return []
    else:
        raise AgentMailRefused(f"recipient field must be a string or list, got {type(value).__name__}")
    return [normalize_address(v) for v in candidates if normalize_address(v)]


def collect_recipients(payload: dict[str, Any]) -> list[str]:
    """Every address this payload would reach, across to/cc/bcc."""
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
        raise AgentMailRefused("refusing a send with no recipient")
    refused = [a for a in recipients if not policy.allows_recipient(a)]
    if refused:
        raise AgentMailRefused(
            f"{len(refused)} recipient(s) are not on this seat's authored "
            "counterparty surface (scope.outbound_roster + inbound_allow_from + "
            "admins, minus domain_blocks). A seat may only write to people its "
            "own configuration names (ss#2258): " + ", ".join(sorted(refused))
        )


class AgentMailOps:
    """Transmit operations bound to one seat's pinned inbox and authored policy."""

    def __init__(
        self,
        credential_path: Path,
        customer_path: Path,
        customer_slug: str,
        *,
        base_url: str = API_BASE,
        opener: Any | None = None,
    ) -> None:
        self._credential_path = credential_path
        self._customer_path = customer_path
        self._customer_slug = customer_slug
        self._base_url = base_url.rstrip("/")
        self._opener = opener
        self._inbox_id: str | None = None

    # -- transport ---------------------------------------------------------

    def _request(self, path: str, method: str, body: dict[str, Any] | None) -> dict[str, Any]:
        key = load_send_key(self._credential_path)
        if not key:
            raise AgentMailTransportError(
                "no AgentMail send credential in the broker store; refusing to send"
            )
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            opener = self._opener or urllib.request.urlopen
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with opener(request, timeout=TIMEOUT_S) as response:
                raw = response.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as exc:  # includes a vendor-side 403
            raise AgentMailTransportError(
                f"agentmail {method} {path} failed: HTTP {exc.code}"
            ) from exc
        except Exception as exc:
            raise AgentMailTransportError(f"agentmail {method} {path} failed: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise AgentMailTransportError("agentmail returned non-JSON") from exc
        return parsed if isinstance(parsed, dict) else {}

    # -- identity ----------------------------------------------------------

    def inbox_id(self) -> str:
        """This seat's own inbox, pinned from config and confirmed present.

        The address is authored; the listing check is what makes a provisioning
        mistake fail closed instead of silently sending from someone else's
        mailbox. With an inbox-scoped key the listing holds only this inbox, so
        the check is cheap and stays correct if scoping is ever lost.
        """
        if self._inbox_id:
            return self._inbox_id
        address = seat_inbox_address(self._customer_path, self._customer_slug)
        if not address:
            raise AgentMailTransportError("cannot resolve this seat's inbox address")
        listing = self._request("/inboxes", "GET", None)
        inboxes = listing.get("inboxes")
        if not isinstance(inboxes, list) or not inboxes:
            raise AgentMailTransportError("agentmail returned no inboxes")
        for entry in inboxes:
            if not isinstance(entry, dict):
                continue
            found = entry.get("inbox_id")
            if isinstance(found, str) and found.lower() == address:
                self._inbox_id = found
                return found
        raise AgentMailTransportError(
            f"this seat's inbox {address!r} is not in the account listing "
            f"({len(inboxes)} visible); refusing to send from another inbox"
        )

    def _path(self, *parts: str) -> str:
        quoted = "/".join(urllib.parse.quote(p, safe="") for p in parts)
        return f"/inboxes/{urllib.parse.quote(self.inbox_id(), safe='')}/{quoted}"

    # -- verbs -------------------------------------------------------------

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fence every recipient, then transmit from this seat's pinned inbox."""
        policy = authored_policy(self._customer_path)
        recipients = collect_recipients(payload)
        enforce_recipients(policy, recipients)
        body = {k: payload[k] for k in _BODY_FIELDS if payload.get(k) not in (None, "", [])}
        response = self._request(self._path("messages", "send"), "POST", body)
        return {
            "message_id": _message_id(response),
            "recipients": recipients,
            "inbox_id": self.inbox_id(),
        }

    def reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Answer an inbound message, but only one from an allowed sender.

        The recipient is structural — AgentMail derives it from the source
        message — so the check that matters is on the ORIGINAL SENDER, fetched
        here rather than taken from the caller. A caller that could name the
        sender could name any sender.
        """
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id:
            raise AgentMailRefused("reply requires the source message_id")
        source = self._request(self._path("messages", message_id), "GET", None)
        sender = normalize_address(source.get("from") or source.get("from_"))
        if not sender:
            raise AgentMailRefused(
                f"cannot determine who sent message {message_id!r}; refusing to reply"
            )
        policy = authored_policy(self._customer_path)
        if not policy.allows_reply_to(sender):
            raise AgentMailRefused(
                "the sender of that message is not on scope.inbound_allow_from, so "
                "this seat may not answer it. Anyone can email this inbox; only "
                "authored senders get replies (ss#2258)"
            )
        body = {k: payload[k] for k in ("text", "html") if payload.get(k)}
        if not body:
            raise AgentMailRefused("refusing to send an empty reply")
        response = self._request(self._path("messages", message_id, "reply"), "POST", body)
        return {
            "message_id": _message_id(response),
            "recipients": [sender],
            "inbox_id": self.inbox_id(),
            # ss#2497 — the twin of the msgraph verb. The broker is the only
            # party that knows who this answered, because it fetched the source
            # message rather than trusting a caller to name the sender. Hashed,
            # so the join exists and the address does not.
            "sender_key": sender_key(sender),
        }
