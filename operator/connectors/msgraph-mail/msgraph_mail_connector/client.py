"""MsGraphClient — the REST/OAuth engine the tool layer wraps.

Auth contract (Microsoft identity platform, app-only client credentials):

- Mint a bearer at ``https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token``
  with ``Content-Type: application/x-www-form-urlencoded`` and body
  ``grant_type=client_credentials`` + ``client_id`` + ``client_secret`` +
  ``scope=https://graph.microsoft.com/.default``. The response carries
  ``access_token`` / ``expires_in`` / ``token_type: Bearer``. There is NO refresh
  token in the app-only flow — the token is re-minted from the same client creds
  when it nears expiry (contrast the Smokeball authorization_code seat).
- EVERY Graph request carries ``Authorization: Bearer <token>``.
- Graph is GLOBAL: one token host, one api host (``https://graph.microsoft.com/v1.0``)
  for every tenant — no region/environment host pair to select.
- The mailbox is PINNED at construction: every mail path is built as
  ``/users/{mailbox}/...`` from config. No method here takes a mailbox argument, so
  a caller can never redirect a read or a send to another mailbox. Tenant-side this
  is reinforced by an Exchange ApplicationAccessPolicy scoping the app to the one
  mailbox (sandbox-proven 2026-07-24); this client is the code-layer belt to that
  policy's braces.

This client is a faithful passthrough for reads (returns the raw Graph JSON) and
owns the Graph message SHAPE for writes: the tool surface passes FLAT addresses
(``to`` / ``cc`` as plain strings) and the Graph recipient nesting
(``toRecipients[].emailAddress.address``) is built HERE — after governance has seen
the flat args (email-channel-seam D4).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

_TOKEN_HOST = "https://login.microsoftonline.com"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

_TOKEN_SKEW_SECONDS = 60
_MAX_ATTEMPTS = 4
_MAX_ERROR_BODY = 600

# The bounded field set the delta poll selects — metadata + body, so an inbound
# message normalizes from the delta payload without a separate full-body fetch,
# while still keeping the payload off Graph's full (much larger) message shape.
_DELTA_SELECT = "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,conversationId,body"
_LIST_SELECT = "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,conversationId"


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop None-valued query params so optional args don't send ``None``."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _truncate_body(text: str | None) -> str:
    """Trim an API error body to a log-safe length. No credential is ever in a
    Graph error body, and request headers are never included regardless."""
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= _MAX_ERROR_BODY else text[:_MAX_ERROR_BODY] + "...(truncated)"


def _recipients(addrs: str | list[str] | None) -> list[dict[str, Any]] | None:
    """Build Graph ``toRecipients``/``ccRecipients`` nesting from FLAT addresses.

    Accepts a single address string or a list; drops blanks. Lives here (not in the
    tool layer) so the tool argument surface the overlay's recipient extraction
    reads stays flat — the nesting happens after governance (email-channel-seam D4)."""
    if addrs is None:
        return None
    items = [addrs] if isinstance(addrs, str) else list(addrs)
    out = [{"emailAddress": {"address": str(a).strip()}} for a in items if str(a).strip()]
    return out or None


def _message_payload(
    *,
    to: str | list[str],
    subject: str,
    body_text: str,
    cc: str | list[str] | None,
) -> dict[str, Any]:
    """A Graph ``message`` resource (Text body) from flat args."""
    msg: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body_text},
        "toRecipients": _recipients(to) or [],
    }
    cc_nested = _recipients(cc)
    if cc_nested:
        msg["ccRecipients"] = cc_nested
    return msg


class MsGraphAuthError(RuntimeError):
    """Token mint failed — bad client creds, wrong tenant, or the identity platform
    rejected the grant. Surfaced (without the secret) so the agent gets a clear
    refusal rather than a raw 4xx."""


class MsGraphApiError(RuntimeError):
    """A Graph request returned a 4xx/5xx after retries. Carries the HTTP status and
    the literal (truncated) response body so the agent and our logs see WHY a call
    failed instead of a bare status code. ``status`` is read by the delta path to
    detect a 410 expired-cursor and restart."""

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"MSGraph {method} {url} -> HTTP {status}: {body or '(empty body)'}")


class MsGraphClient:
    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        mailbox: str,
        timeout: float = 30.0,
    ) -> None:
        # Fail closed at construction: a missing credential must never reach a live
        # path reporting success. Name the offender without echoing any value.
        missing = [
            name
            for name, val in (
                ("tenant_id", tenant_id),
                ("client_id", client_id),
                ("client_secret", client_secret),
                ("mailbox", mailbox),
            )
            if not (val or "").strip()
        ]
        if missing:
            raise ValueError(
                f"MsGraphClient: missing required config {missing} "
                f"(MSGRAPH_{'/MSGRAPH_'.join(m.upper() for m in missing)})"
            )
        self._tenant_id = tenant_id.strip()
        self._client_id = client_id.strip()
        self._client_secret = client_secret
        self.mailbox = mailbox.strip()
        self._token_url = f"{_TOKEN_HOST}/{self._tenant_id}/oauth2/v2.0/token"
        self._token: str | None = None
        self._token_deadline = 0.0
        self._http = httpx.Client(timeout=timeout)

    # ---- url building -----------------------------------------------------
    def _mail_url(self, suffix: str) -> str:
        """A Graph URL under the PINNED mailbox: ``.../users/{mailbox}/{suffix}``."""
        return f"{_GRAPH_BASE}/users/{self.mailbox}/{suffix}"

    # ---- auth -------------------------------------------------------------
    def _mint_token(self) -> int:
        try:
            resp = self._http.post(
                self._token_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": _GRAPH_SCOPE,
                },
            )
        except httpx.HTTPError as exc:
            raise MsGraphAuthError(f"token request to {self._token_host()} failed: {exc}") from exc
        if resp.status_code != 200:
            # Never include the response body verbatim — it can echo the request.
            raise MsGraphAuthError(
                f"token mint (client_credentials) rejected with HTTP {resp.status_code} at {self._token_host()}"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise MsGraphAuthError("token response had no access_token")
        expires_in = int(body.get("expires_in", 3600))
        self._token = token
        self._token_deadline = time.monotonic() + max(expires_in - _TOKEN_SKEW_SECONDS, 0)
        return expires_in

    def _token_host(self) -> str:
        """The token host without the tenant id — a log-safe identity for errors."""
        return f"{_TOKEN_HOST}/{self._tenant_id}/oauth2/v2.0/token"

    def _bearer(self) -> str:
        if self._token is None or time.monotonic() >= self._token_deadline:
            self._mint_token()
        assert self._token is not None
        return self._token

    # ---- requests ---------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Issue an authenticated Graph request to an ABSOLUTE url (built by
        ``_mail_url`` or a Graph-supplied ``@odata.nextLink``/``deltaLink``). Returns
        parsed JSON (or None for an empty/202/204 body). Retries 429 with backoff and
        refreshes once on a 401."""
        refreshed = False
        last: httpx.Response | None = None
        for attempt in range(_MAX_ATTEMPTS):
            headers = {
                "Authorization": f"Bearer {self._bearer()}",
                "Accept": "application/json",
            }
            last = self._http.request(method, url, params=_clean(params), json=json, headers=headers)
            if last.status_code == 429:
                time.sleep(min(2**attempt, 8))
                continue
            if last.status_code == 401 and not refreshed:
                self._token = None  # force a fresh mint, then retry once
                refreshed = True
                continue
            if last.status_code >= 400:
                raise MsGraphApiError(method, url, last.status_code, _truncate_body(last.text))
            if last.status_code in (202, 204) or not last.content:
                return None
            return last.json()
        assert last is not None
        # Attempts exhausted (e.g. a persistent 429) — surface the last status+body.
        raise MsGraphApiError(method, url, last.status_code, _truncate_body(last.text))

    # ---- reads ------------------------------------------------------------
    def list_messages(self, folder: str, top: int) -> Any:
        """List a mail folder's messages (metadata; newest first)."""
        return self.request(
            "GET",
            self._mail_url(f"mailFolders/{folder}/messages"),
            params={
                "$select": _LIST_SELECT,
                "$orderby": "receivedDateTime desc",
                "$top": top,
            },
        )

    def get_message(self, message_id: str) -> Any:
        """Get one message by id, including its full ``body`` (the read path the
        poller falls back to when a delta item omits the body)."""
        return self.request("GET", self._mail_url(f"messages/{message_id}"))

    def poll_delta(self, delta_link: str | None = None) -> tuple[list[Any], str | None, bool]:
        """Drain the inbox delta query, following ``@odata.nextLink`` pages, and
        return ``(raw_messages, delta_link, cursor_reset)``.

        First call (no ``delta_link``) issues the base delta URL with a bounded
        ``$select``; a subsequent call passes the stored ``deltaLink`` verbatim. A
        410 Gone on a provided cursor (expired sync state) restarts the delta from
        scratch and flags ``cursor_reset``. ``@removed`` tombstones are dropped —
        they carry no content to normalize."""
        try:
            items, out = self._drain_delta(delta_link)
            return items, out, False
        except MsGraphApiError as exc:
            if exc.status == 410 and delta_link is not None:
                items, out = self._drain_delta(None)
                return items, out, True
            raise

    def _drain_delta(self, delta_link: str | None) -> tuple[list[Any], str | None]:
        if delta_link:
            next_url: str | None = delta_link
            params: dict[str, Any] | None = None
        else:
            next_url = self._mail_url("mailFolders/inbox/messages/delta")
            params = {"$select": _DELTA_SELECT}
        items: list[Any] = []
        delta_out: str | None = None
        while next_url:
            resp = self.request("GET", next_url, params=params) or {}
            params = None  # only the first constructed call carries $select
            items.extend(v for v in resp.get("value", []) if "@removed" not in v)
            delta_out = resp.get("@odata.deltaLink") or delta_out
            next_url = resp.get("@odata.nextLink")
        return items, delta_out

    # ---- writes -----------------------------------------------------------
    def create_draft(
        self,
        *,
        to: str | list[str],
        subject: str,
        body_text: str,
        cc: str | list[str] | None = None,
    ) -> Any:
        """Create a draft in the mailbox's Drafts folder (POST /messages). Returns
        the created draft resource (carries the new message ``id``)."""
        return self.request(
            "POST",
            self._mail_url("messages"),
            json=_message_payload(to=to, subject=subject, body_text=body_text, cc=cc),
        )

    def send_mail(
        self,
        *,
        to: str | list[str],
        subject: str,
        body_text: str,
        cc: str | list[str] | None = None,
        save_to_sent_items: bool = True,
    ) -> Any:
        """Send a new message (POST /sendMail), saving a copy to Sent Items. Graph
        returns 202 with no body."""
        self.request(
            "POST",
            self._mail_url("sendMail"),
            json={
                "message": _message_payload(to=to, subject=subject, body_text=body_text, cc=cc),
                "saveToSentItems": save_to_sent_items,
            },
        )
        return {"status": "sent", "saveToSentItems": save_to_sent_items}

    def reply(self, message_id: str, comment: str, *, reply_all: bool = False) -> Any:
        """Reply on an existing message thread (POST /messages/{id}/reply or
        /replyAll) — the recipient-locked reply path: Graph derives the recipients
        from the original message, so the reply cannot be redirected. Returns 202
        with no body."""
        action = "replyAll" if reply_all else "reply"
        self.request(
            "POST",
            self._mail_url(f"messages/{message_id}/{action}"),
            json={"comment": comment},
        )
        return {"status": "replied", "reply_all": reply_all, "message_id": message_id}


# ---- env-driven construction (single source of truth) ---------------------
def build_client_from_env() -> MsGraphClient:
    """Construct an MsGraphClient from the connector's runtime env. Uses ``.get`` so
    a missing var fails as the client's own named ValueError (fail-closed), not a
    bare KeyError."""
    return MsGraphClient(
        tenant_id=os.environ.get("MSGRAPH_TENANT_ID", ""),
        client_id=os.environ.get("MSGRAPH_CLIENT_ID", ""),
        client_secret=os.environ.get("MSGRAPH_CLIENT_SECRET", ""),
        mailbox=os.environ.get("MSGRAPH_MAILBOX", ""),
    )
