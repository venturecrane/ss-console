"""The msgraph half of the send reconcile's mailbox transport (ss#2499).

Factored out of ``operator/bin/reconcile-sends.py`` VERBATIM when the body-verify
fourth phase landed: the orchestrator sits under the operator module-size
ratchet (operator/contracts/operator-module-size.json), and the Graph transport
cluster is the self-contained piece with no matcher logic in it. Behavior is
unchanged; reconcile-sends re-exports every name so its tests and callers read
exactly as before.

Everything here READS. ``_graph_get`` is the only HTTP verb in the module, on
purpose: the seat's mail is the client's, held in the client's own tenant under
agreement 4.6, and a reconciler is an instrument -- it observes and never
touches.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_TOKEN_HOST = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

_HTTP_TIMEOUT_S = 30.0

#: The header the broker stamps on every message it transmits, and the exact key
#: the reconciler joins on. Matched case-insensitively: header names are
#: case-insensitive per RFC5322 and Exchange re-cases freely, so a case-sensitive
#: compare would report every send as unaudited -- a broken instrument that looks
#: exactly like a mailbox full of foreign mail.
AUDIT_ROW_HEADER = "X-SMD-Audit-Row"

#: Metadata key carrying that header's value on the audit row.
AUDIT_TOKEN_KEY = "audit_row_token"

#: ``internetMessageHeaders`` is not returned unless selected BY NAME. Omitting
#: it does not error -- it silently yields messages with no headers, which reads
#: as "nothing came through the broker" and would turn the reconcile into a
#: machine for accusing the Operator of every send it made.
_GRAPH_SELECT = (
    "id,internetMessageId,internetMessageHeaders,conversationId,"
    "sentDateTime,subject,toRecipients,ccRecipients,bccRecipients"
)

#: Newest-first pages of this size, and a hard cap on how many are walked. The
#: cap exists so a mailbox with years of history cannot make a scheduled run
#: unbounded; a run that hits it says so and HOLDS rather than reporting the
#: truncated set as complete.
_GRAPH_PAGE_SIZE = 100
_GRAPH_MAX_PAGES = 50

#: The env var holding one seat's READ app secret. Per-seat by design (ADR 0010,
#: firm-custodied credentials): the paying firm's Graph secret is its own, and a
#: shared fallback would let a missing per-seat secret quietly authenticate as
#: somebody else's app.
_GRAPH_SECRET_ENV = "MSGRAPH_CLIENT_SECRET__{slug}"


class ReconcileError(RuntimeError):
    """A transport or credential failure. Holds; never reported as a finding.

    Shared by BOTH mailbox channels: it moved here with the Graph cluster
    because the lib module cannot import the dashed reconcile-sends filename,
    and two exception classes for one tri-state contract would let an
    ``except`` clause quietly cover half of it.
    """


@dataclass
class MsGraphSeat:
    """One seat's authored Graph identity, read from its own customer.yaml."""

    slug: str
    mailbox: str
    tenant_id: str
    client_id: str

    @property
    def secret_env(self) -> str:
        return _GRAPH_SECRET_ENV.format(slug=self.slug.upper().replace("-", "_"))


def _default_customers_dir() -> str:
    # lib/ -> bin/ -> operator/ -> operator/customers
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(os.path.dirname(here)), "customers")


def msgraph_seats(customers_dir: str | None = None) -> list[MsGraphSeat]:
    """Every seat whose authored mail adapter is msgraph.

    Read from customer.yaml rather than from a list maintained here, so a seat
    provisioned onto Graph is covered by this control on the day it is authored.
    A hand-kept list is how a channel ends up with zero coverage and nobody
    notices -- which is the state ss#2499 found.

    A seat missing any of the three identity fields is SKIPPED HERE and reported
    as a hold by the caller, never silently dropped.
    """
    import yaml  # deferred: only the msgraph half needs it

    root = customers_dir or _default_customers_dir()
    seats: list[MsGraphSeat] = []
    for slug in sorted(os.listdir(root)):
        if slug.startswith("_") or slug.startswith("."):
            continue
        path = os.path.join(root, slug, "customer.yaml")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except (OSError, ValueError, yaml.YAMLError):
            continue
        email = ((data.get("connectors") or {}).get("Email")) or {}
        if not isinstance(email, dict) or email.get("adapter") != "msgraph":
            continue
        auth = email.get("msgraph_auth") or {}
        seats.append(
            MsGraphSeat(
                slug=slug,
                mailbox=str((auth or {}).get("mailbox") or ""),
                tenant_id=str((auth or {}).get("tenant_id") or ""),
                client_id=str((auth or {}).get("client_id") or ""),
            )
        )
    return seats


def graph_token(seat: MsGraphSeat, secret: str, *, opener=None) -> str:
    """A client-credentials token for the seat's READ app registration.

    The READ app, deliberately: this control only ever reads, and the read
    registration is the one the tenant's ApplicationAccessPolicy scopes to this
    single mailbox. Borrowing the SEND app's credential here would hand a
    watchdog transmit rights it has no use for.
    """
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": seat.client_id,
            "client_secret": secret,
            "scope": GRAPH_SCOPE,
        }
    ).encode()
    request = urllib.request.Request(
        f"{GRAPH_TOKEN_HOST}/{seat.tenant_id}/oauth2/v2.0/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=_HTTP_TIMEOUT_S) as response:
            parsed = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Status only. The token endpoint echoes request parameters back in its
        # error bodies, and one of those parameters is the client secret.
        raise ReconcileError(f"msgraph token mint rejected with HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReconcileError(f"msgraph token mint failed for {seat.slug}") from exc
    token = parsed.get("access_token") if isinstance(parsed, dict) else None
    if not isinstance(token, str) or not token:
        raise ReconcileError(f"msgraph token response for {seat.slug} carried no access_token")
    return token


def _graph_get(url: str, token: str, *, opener=None) -> dict:
    """One READ against Graph. There is no other verb in this module, on purpose.

    The seat's mail is the client's, held in the client's own tenant under
    agreement 4.6, and a reconciler is an instrument -- it observes and never
    touches. GET is the only method built here, so a future edit that wanted to
    mutate would have to add the capability rather than pass a flag.
    """
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=_HTTP_TIMEOUT_S) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise ReconcileError(f"msgraph GET failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReconcileError(f"msgraph GET failed: {exc}") from exc


def _audit_token_of(message: dict) -> str:
    """The ``X-SMD-Audit-Row`` value on a Graph message, or ``""``."""
    headers = message.get("internetMessageHeaders")
    if not isinstance(headers, list):
        return ""
    wanted = AUDIT_ROW_HEADER.lower()
    for header in headers:
        if isinstance(header, dict) and str(header.get("name") or "").lower() == wanted:
            return str(header.get("value") or "")
    return ""


def _graph_addresses(message: dict) -> list[str]:
    """Everyone a message reached, across to/cc/bcc, flattened out of Graph's
    ``{"emailAddress": {"address": ...}}`` nesting.

    ``bcc`` is included because it DELIVERS. A report naming only the visible
    recipients of an unaudited send describes the wrong set of people, and a
    finding that is confidently wrong is worse to a reader than a vague one.
    """
    out: list[str] = []
    for field_name in ("toRecipients", "ccRecipients", "bccRecipients"):
        for item in message.get(field_name) or []:
            address = ((item or {}).get("emailAddress") or {}).get("address")
            if address and address not in out:
                out.append(str(address))
    return out


def normalize_graph_message(message: dict) -> dict:
    """A Graph message in the shape the shared matcher and baseline already read.

    ``message_id`` is the RFC2822 ``internetMessageId`` and not the Graph id, for
    two reasons that both point the same way: it is what the broker records on
    the audit row as ``vendor_message_id``, and it is what survives outside this
    mailbox -- in a bounce, in the recipient's copy, in whatever a firm forwards
    when it asks "did you send this?". The mailbox-local Graph id rides along
    separately for anyone who has to go and look at the message.
    """
    return {
        "message_id": str(message.get("internetMessageId") or ""),
        "graph_id": str(message.get("id") or ""),
        "timestamp": str(message.get("sentDateTime") or ""),
        "to": _graph_addresses(message),
        "subject": str(message.get("subject") or ""),
        AUDIT_TOKEN_KEY: _audit_token_of(message),
    }


def list_sent_msgraph(seat: MsGraphSeat, token: str, *, since=None, opener=None) -> list[dict]:
    """Every message in this seat's Sent Items, newest-first, paged and bounded.

    Ordered newest-first so a ``--since`` window can stop paging as soon as it
    passes the boundary rather than walking the whole mailbox to filter at the
    end. The page cap is a guard, not a window: hitting it raises rather than
    returning a truncated list, because a partial scan reported as a complete one
    is how a control quietly stops covering the oldest half of a mailbox.
    """
    from datetime import datetime, timezone

    def _parse_ts(value) -> datetime:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)

    url = (
        f"{GRAPH_API_BASE}/users/{seat.mailbox}/mailFolders/sentitems/messages"
        f"?$select={_GRAPH_SELECT}&$top={_GRAPH_PAGE_SIZE}"
        "&$orderby=" + urllib.parse.quote("sentDateTime desc", safe="")
    )
    out: list[dict] = []
    for _page in range(_GRAPH_MAX_PAGES):
        page = _graph_get(url, token, opener=opener)
        messages = page.get("value")
        for message in messages if isinstance(messages, list) else []:
            normalized = normalize_graph_message(message)
            if since and normalized["timestamp"]:
                if _parse_ts(normalized["timestamp"]) < since:
                    return out
            out.append(normalized)
        url = str(page.get("@odata.nextLink") or "")
        if not url:
            return out
    raise ReconcileError(
        f"{seat.mailbox}: more than {_GRAPH_MAX_PAGES} pages of sent mail; "
        "narrow the run with --days rather than trusting a truncated scan"
    )


def fetch_graph_body(seat: MsGraphSeat, token: str, graph_id: str, *, opener=None) -> Optional[str]:
    """One message's body text, fetched transiently for the body-verify phase.

    Per-message and ONLY for templated-routine sends (the caller gates on the
    send-render declaration): the list scan's ``_GRAPH_SELECT`` deliberately
    stays body-free, so bodies enter the process one at a time, flow into
    ``canonical_body_sha256``, and go nowhere else. Graph serves ``body`` as
    ``{contentType, content}``; the content is returned as-is (an HTML-typed
    body will canon-mismatch, which the verifier grades as a HOLD until the
    channel transforms are calibrated on a live rehearsal).
    """
    if not graph_id:
        return None
    url = f"{GRAPH_API_BASE}/users/{seat.mailbox}/messages/{urllib.parse.quote(graph_id)}?$select=body"
    payload = _graph_get(url, token, opener=opener)
    body = payload.get("body")
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    return content if isinstance(content, str) else None


__all__ = [
    "AUDIT_ROW_HEADER",
    "AUDIT_TOKEN_KEY",
    "GRAPH_API_BASE",
    "GRAPH_SCOPE",
    "GRAPH_TOKEN_HOST",
    "MsGraphSeat",
    "ReconcileError",
    "_GRAPH_MAX_PAGES",
    "_audit_token_of",
    "_graph_addresses",
    "_graph_get",
    "fetch_graph_body",
    "graph_token",
    "list_sent_msgraph",
    "msgraph_seats",
    "normalize_graph_message",
]
