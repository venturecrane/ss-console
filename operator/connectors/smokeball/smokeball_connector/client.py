"""SmokeballClient — the REST/OAuth engine the tool layer wraps.

Auth contract (confirmed against docs.smokeball.com, 2026-06-23, not assumed):

Two grants, selected by ``auth_mode`` (both mint a Bearer at the same Cognito
endpoint ``{auth_host}/oauth2/token`` with ``Authorization: Basic
base64(client_id:client_secret)`` and ``Content-Type:
application/x-www-form-urlencoded``):

- ``client_credentials`` (default) — body ``grant_type=client_credentials`` (+
  ``client_id``). Server-to-server; no user consent. The path proven live against
  our own staging tenant.
- ``authorization_code`` — the firm-delegated grant. The user-consent round-trip
  (authorize → code → first token) happens OUTSIDE this client (the connect flow);
  this client receives the resulting ``refresh_token`` and mints access tokens with
  body ``grant_type=refresh_token`` (+ ``client_id`` + ``refresh_token``). If the
  refresh response rotates the refresh token, we hold the new one in memory.

Both responses carry ``access_token`` / ``expires_in`` / ``token_type: Bearer``.

- EVERY API request carries TWO headers: ``x-api-key`` (the Smokeball-issued
  client key, identifies the app) and ``Authorization: Bearer <token>``.
- Region+environment select the host pair; the two MUST match (never cross
  US/AU/UK or prod/staging hosts).
- ``account_id`` (optional) — when set, every API path is prefixed ``/{account_id}``
  (the documented multi-account server-to-server shape; a single-firm seat usually
  leaves it unset).

This client is a FAITHFUL passthrough: read tools pass params straight through
and return the raw JSON. It deliberately does NOT bake the still-unverified
response/pagination shape or the ``updatedSince`` .NET-ticks conversion — those
are confirmed at the connect step against a live tenant (see smokeball-surface.md
ASSUMED list), and encoding a guess here would be the wrong kind of certainty.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.parse
from typing import Any

import httpx

# /matters/{id} paths take the matter's UUID, never its human matter number.
# Guarded in request() - see the docstring there for the 2026-09-01 incident.
# Deliberately narrow (#2673 doctrine): refuse only a segment that CANNOT be an
# id - all digits, or the register-anchored matter-number formats - and stay
# silent on everything else.
_MATTER_ID_SEG = re.compile(r"^/matters/([^/?]+)")
_MATTER_NUMBERISH = re.compile(r"\d+|\d{4}-[A-Z]{2}-\d{3,4}|[A-Z]{2}-\d{4}-\d{4}")

# region -> environment -> (auth_host, api_host). Confirmed from the base-URLs doc.
_HOSTS: dict[tuple[str, str], tuple[str, str]] = {
    ("us", "production"): ("https://auth.smokeball.com", "https://api.smokeball.com"),
    ("us", "staging"): (
        "https://datastaging-auth.smokeball.com",
        "https://stagingapi.smokeball.com",
    ),
    ("au", "production"): (
        "https://auth.smokeball.com.au",
        "https://api.smokeball.com.au",
    ),
    ("au", "staging"): (
        "https://datastaging-auth.smokeball.com.au",
        "https://stagingapi.smokeball.com.au",
    ),
    ("uk", "production"): (
        "https://auth.smokeball.co.uk",
        "https://api.smokeball.co.uk",
    ),
    ("uk", "staging"): (
        "https://datastaging-auth.smokeball.co.uk",
        "https://stagingapi.smokeball.co.uk",
    ),
}

_TOKEN_SKEW_SECONDS = 60
_MAX_ATTEMPTS = 4
_AUTH_MODES = ("client_credentials", "authorization_code")


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop None-valued query params so optional tool args don't send ``None``."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


class SmokeballAuthError(RuntimeError):
    """Token mint failed — bad client creds, wrong region/env host, or the auth
    server rejected the grant. Surfaced (without the secret) so the agent gets a
    clear refusal rather than a raw 4xx."""


class SmokeballWriteError(RuntimeError):
    """A document write (upload/delete) failed — the metadata POST returned no
    upload URL, or the presigned PUT was rejected. Surfaced as a clear refusal."""


_MAX_ERROR_BODY = 600
# read_document ceiling: a matter PDF/DOCX is KBs to low MBs; anything past this
# is a scan bundle or media file that text extraction shouldn't slurp into RAM.
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
# file_attachment_to_matter: hosts an attachment download URL may point at.
# AgentMail mints time-limited URLs on these hosts (docs.agentmail.to/attachments).
# cdn.agentmail.to observed live 2026-07-07: get_attachment returned a CDN-host
# URL and the transfer fail-closed on the allowlist (post-reprovision
# verification of overlay#140/ss#1744) — the allowlist was right in posture,
# just missing the vendor's real serving host.
_DEFAULT_ATTACHMENT_HOSTS = "download.agentmail.to,cdn.agentmail.to"


def _truncate_body(text: str | None) -> str:
    """Trim an API error body to a log-safe length (no credentials are ever in a
    Smokeball error body, but request headers are never included regardless)."""
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= _MAX_ERROR_BODY else text[:_MAX_ERROR_BODY] + "...(truncated)"


class SmokeballApiError(RuntimeError):
    """An API request returned a 4xx/5xx after retries. Carries the HTTP status and
    the literal (truncated) response body so the agent, and our logs, see WHY a
    call failed (e.g. an insufficient-scope vs matter-permission 403) instead of a
    bare status code. The previous bare ``raise_for_status()`` discarded the body,
    which is exactly what made write failures undiagnosable."""

    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"Smokeball {method} {path} -> HTTP {status}: {body or '(empty body)'}")


class SmokeballClient:
    def __init__(
        self,
        *,
        region: str,
        environment: str,
        client_id: str,
        client_secret: str,
        api_key: str,
        auth_mode: str = "client_credentials",
        refresh_token: str | None = None,
        refresh_token_file: str | None = None,
        account_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        key = (region.lower(), environment.lower())
        if key not in _HOSTS:
            raise ValueError(f"unknown region/environment {region!r}/{environment!r}; valid: {sorted(_HOSTS)}")
        if auth_mode not in _AUTH_MODES:
            raise ValueError(f"unknown auth_mode {auth_mode!r}; valid: {_AUTH_MODES}")
        if auth_mode == "authorization_code" and not refresh_token:
            raise ValueError("auth_mode='authorization_code' requires a refresh_token")
        self.region = region.lower()
        self.environment = environment.lower()
        self.auth_host, self.api_host = _HOSTS[key]
        self.auth_mode = auth_mode
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_key = api_key
        self._refresh_token = refresh_token
        # The firm-delegated refresh token's durable home (ADR 0054): the
        # Machine-hosted OAuth callback writes it here, and we rewrite it in place
        # when Smokeball rotates it on a refresh — so a rotated token survives a
        # restart (the Clio token-file pattern).
        self._refresh_token_file = refresh_token_file
        # Normalize the optional account prefix to a bare segment ("abc", not
        # "/abc/") so request() can build "{api_host}/{account_id}{path}" cleanly.
        self._account_id = account_id.strip("/") if account_id else None
        self._token: str | None = None
        self._token_deadline = 0.0
        self._scopes_logged = False
        self._http = httpx.Client(timeout=timeout)

    # ---- auth -------------------------------------------------------------
    def _token_request_body(self) -> dict[str, str]:
        """The grant-specific form body. client_credentials mints from the app's
        own creds; authorization_code mints from the firm-delegated refresh token."""
        if self.auth_mode == "authorization_code":
            # _refresh_token is guaranteed non-None for this mode (ctor enforces it).
            return {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": self._refresh_token or "",
            }
        return {"grant_type": "client_credentials", "client_id": self._client_id}

    def _post_token_request(self) -> httpx.Response:
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        try:
            return self._http.post(
                f"{self.auth_host}/oauth2/token",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=self._token_request_body(),
            )
        except httpx.HTTPError as exc:
            raise SmokeballAuthError(f"token request to {self.auth_host} failed: {exc}") from exc

    def _reload_refresh_token_from_file(self) -> bool:
        """Re-read the durable refresh-token file and adopt its token when it
        differs from the one in memory. Returns True only when a different token
        was adopted. The file is the canonical seam the Machine-hosted OAuth
        callback writes on (re-)connect, so it can be newer than this process."""
        if not (self.auth_mode == "authorization_code" and self._refresh_token_file):
            return False
        try:
            val = open(self._refresh_token_file, encoding="utf-8").read().strip()
        except OSError:
            return False
        if val and val != self._refresh_token:
            self._refresh_token = val
            return True
        return False

    def _mint_token(self) -> int:
        resp = self._post_token_request()
        if resp.status_code != 200 and self._reload_refresh_token_from_file():
            # Self-heal: a rejected refresh grant in a long-running process usually
            # means a re-connect (OAuth callback) wrote a NEW refresh token to the
            # durable file while this process still holds the old one in memory —
            # the MCP server's client is built once and outlives consents. Retry
            # the mint once with the file's token before failing.
            resp = self._post_token_request()
        if resp.status_code != 200:
            # Never include the response body verbatim — it can echo the grant.
            raise SmokeballAuthError(
                f"token mint ({self.auth_mode}) rejected with HTTP {resp.status_code} at {self.auth_host}/oauth2/token"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise SmokeballAuthError("token response had no access_token")
        # Smokeball may rotate the refresh token on a refresh_token grant; if it
        # returns a new one, hold it in memory AND rewrite the durable token file
        # so the rotated token survives a restart (ADR 0054, Clio pattern).
        rotated = body.get("refresh_token")
        if self.auth_mode == "authorization_code" and rotated and rotated != self._refresh_token:
            self._refresh_token = rotated
            self._persist_refresh_token(rotated)
        expires_in = int(body.get("expires_in", 3600))
        self._token = token
        self._token_deadline = time.monotonic() + max(expires_in - _TOKEN_SKEW_SECONDS, 0)
        # Operability: log the granted scopes once per process on first successful
        # auth. The connector mints on the first tool call of any agent turn (e.g.
        # the inbox router's get_contacts/list_matters), so this surfaces the live
        # token's actual grant without an agent ever calling auth_status — the only
        # reliable readout, since no env flag reaches the broker-curated connector
        # env and no enabled skill writes. Scopes are not secret; the token never is.
        if not self._scopes_logged:
            self._scopes_logged = True
            try:
                import sys

                print(
                    f"[smokeball] authenticated mode={self.auth_mode} granted_scopes={self._decode_token_scopes()}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:  # noqa: BLE001 - logging must never break auth
                pass
        return expires_in

    def _persist_refresh_token(self, token: str) -> None:
        """Atomically rewrite the durable refresh-token file (0600). Best-effort —
        a write failure must not break minting (the in-memory token still works for
        this process); it only means the next restart falls back to the prior file."""
        if not self._refresh_token_file:
            return
        try:
            # ss#2614: the file's mode bits survive rotation. Two principals
            # share this token on a seat that runs the chronology runner (the
            # connector as hermes, the runner as medchron, through a setgid
            # group dir), and ``os.replace`` creates a NEW inode owned by
            # whoever rotated. A fixed 0600 here would lock the other one out
            # at the first rotation; keeping the prior mode (0660 when it was
            # group-shared, 0600 as before otherwise) keeps both reading.
            mode = 0o600
            try:
                mode = os.stat(self._refresh_token_file).st_mode & 0o777 or 0o600
            except OSError:
                pass
            tmp = f"{self._refresh_token_file}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, token.encode())
                os.fchmod(fd, mode)
            finally:
                os.close(fd)
            os.replace(tmp, self._refresh_token_file)
        except OSError:
            pass

    def _bearer(self) -> str:
        if self._token is None or time.monotonic() >= self._token_deadline:
            self._mint_token()
        assert self._token is not None
        return self._token

    # ---- requests ---------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Issue an authenticated request. Returns parsed JSON (or None for an
        empty body). Retries 429 with backoff and refreshes once on a 401.

        Refuses a ``/matters/{id}`` path whose id segment is not a UUID before
        any HTTP is sent. 2026-09-01: the agent passed matter NUMBERS (10006,
        202248) where the API wants ids; the resulting 404 burst manufactured
        Hermes' derivative "MCP server unreachable" circuit (three business
        errors open it - overlay test_connector_signatures.py pins this), every
        later Smokeball call failed instantly, and the sticky ladder HARD_STOPped
        the seat. One instructive refusal here lets the agent self-correct on
        the next call instead of feeding the circuit."""
        seg = _MATTER_ID_SEG.match(path)
        if seg and _MATTER_NUMBERISH.fullmatch(seg.group(1)):
            raise ValueError(
                f"'{seg.group(1)}' is not a matter id (Smokeball matter ids are "
                "UUIDs). It looks like a matter number - resolve the id first "
                "(list_matters / get_matter return both), then call again with "
                "the id."
            )
        prefix = f"/{self._account_id}" if self._account_id else ""
        url = f"{self.api_host}{prefix}{path}"
        refreshed = False
        last: httpx.Response | None = None
        for attempt in range(_MAX_ATTEMPTS):
            headers = {
                "x-api-key": self._api_key,
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
                raise SmokeballApiError(method, path, last.status_code, _truncate_body(last.text))
            if last.status_code == 204 or not last.content:
                return None
            return last.json()
        assert last is not None
        # Attempts exhausted (e.g. a persistent 429) — surface the last status+body.
        raise SmokeballApiError(method, path, last.status_code, _truncate_body(last.text))

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    # ---- document writes --------------------------------------------------
    def add_file(
        self,
        matter_id: str,
        file_name: str,
        data: bytes,
        *,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to a matter via Smokeball's documented two-stage flow:

        1. ``POST /matters/{id}/documents/files`` with the metadata returns a
           presigned ``uploadUrl`` + ``fileId`` (HTTP 202).
        2. ``PUT`` the raw bytes to that URL with an **empty** ``Content-Type``
           (an S3 presigned-PUT requirement Smokeball calls out explicitly — a
           non-empty value breaks the signature → 403).

        Materialization is asynchronous: the file becomes readable once the
        firm's document worker ingests it, so callers that need to confirm should
        poll ``get_file``. Returns the ``fileId`` (and echoes the request)."""
        body: dict[str, Any] = {"fileName": file_name}
        if folder_id is not None:
            body["folderId"] = folder_id
        info = self.request("POST", f"/matters/{matter_id}/documents/files", json=body)
        if not isinstance(info, dict) or not info.get("uploadUrl"):
            raise SmokeballWriteError(
                f"add_file: metadata POST did not return an uploadUrl (matter {matter_id!r}, file {file_name!r})"
            )
        self._put_presigned(info["uploadUrl"], data)
        return {
            "fileId": info.get("fileId"),
            "matterId": matter_id,
            "fileName": file_name,
            "uploaded": True,
        }

    # ---- document reads -----------------------------------------------------
    def download_file(self, matter_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        """Fetch a matter document's bytes via the documented download flow:
        ``GET /matters/{id}/documents/files/{fileId}/download`` returns
        ``{downloadUrl, expiry, fileExtension, fileId, name, sizeBytes}`` (contract
        observed live 2026-07-05); the presigned ``downloadUrl`` is then fetched with
        NO auth headers (same S3-presign rule as the upload leg). Returns
        ``(download_info, blob)``. Size-guarded: refuses anything over
        ``_MAX_DOWNLOAD_BYTES`` up front (from the advertised sizeBytes) and again on
        the actual body, so a mislabeled giant can't flood the process."""
        info = self.request("GET", f"/matters/{matter_id}/documents/files/{file_id}/download")
        if not isinstance(info, dict) or not info.get("downloadUrl"):
            raise SmokeballWriteError(f"download: no downloadUrl for file {file_id!r} on matter {matter_id!r}")
        advertised = info.get("sizeBytes")
        if isinstance(advertised, int) and advertised > _MAX_DOWNLOAD_BYTES:
            raise SmokeballWriteError(
                f"download: file {file_id!r} is {advertised} bytes, over the {_MAX_DOWNLOAD_BYTES}-byte read limit"
            )
        try:
            resp = self._http.get(info["downloadUrl"])
        except httpx.HTTPError as exc:
            raise SmokeballWriteError(f"presigned download GET failed: {exc}") from exc
        if resp.status_code >= 400:
            raise SmokeballWriteError(f"presigned download GET rejected with HTTP {resp.status_code}")
        blob = resp.content
        if len(blob) > _MAX_DOWNLOAD_BYTES:
            raise SmokeballWriteError(
                f"download: file {file_id!r} body is {len(blob)} bytes, over the {_MAX_DOWNLOAD_BYTES}-byte read limit"
            )
        return info, blob

    def fetch_attachment_url(self, url: str) -> bytes:
        """Fetch attachment bytes from a TIME-LIMITED vendor download URL (the
        AgentMail attachment ``download_url`` contract) for filing to a matter.

        Guardrails, because the URL argument ultimately comes from the agent
        loop on a tainted turn: https only; host must be on the allowlist
        (default: AgentMail's download hosts; override via
        ``SMOKEBALL_ATTACHMENT_URL_HOSTS``, comma-separated) so injected
        content cannot direct arbitrary web content into a matter file; no
        redirects are followed (httpx default); no auth headers are sent (the
        URL's token IS the credential); body capped at ``_MAX_DOWNLOAD_BYTES``."""
        parsed = urllib.parse.urlparse(url)
        allowed = {
            h.strip().lower()
            for h in os.environ.get("SMOKEBALL_ATTACHMENT_URL_HOSTS", _DEFAULT_ATTACHMENT_HOSTS).split(",")
            if h.strip()
        }
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed:
            raise SmokeballWriteError(
                f"attachment fetch refused: URL host {parsed.hostname!r} is not an "
                f"allowed attachment source (allowed: {sorted(allowed)})"
            )
        try:
            resp = self._http.get(url)
        except httpx.HTTPError as exc:
            raise SmokeballWriteError(f"attachment fetch failed: {exc}") from exc
        if resp.status_code >= 400:
            raise SmokeballWriteError(f"attachment fetch rejected with HTTP {resp.status_code}")
        blob = resp.content
        if len(blob) > _MAX_DOWNLOAD_BYTES:
            raise SmokeballWriteError(
                f"attachment fetch: body is {len(blob)} bytes, over the {_MAX_DOWNLOAD_BYTES}-byte limit"
            )
        return blob

    def _put_presigned(self, url: str, data: bytes) -> None:
        """PUT raw bytes to a presigned S3 upload URL. The URL is pre-authenticated
        by its signature, so we send NO ``x-api-key``/``Authorization`` and an
        EMPTY ``Content-Type`` (the header is not part of the signed request)."""
        try:
            resp = self._http.put(url, content=data, headers={"Content-Type": ""})
        except httpx.HTTPError as exc:
            raise SmokeballWriteError(f"presigned upload PUT failed: {exc}") from exc
        if resp.status_code >= 400:
            raise SmokeballWriteError(f"presigned upload PUT rejected with HTTP {resp.status_code}")

    def create_folder(self, matter_id: str, name: str, parent_folder_id: str | None = None) -> Any:
        """``POST /matters/{id}/documents/folders``: a document folder on a matter,
        nested under ``parent_folder_id`` or at the matter root. The MCP tool of
        the same name and the chronology runner's delivery step both call this,
        so the wire shape lives in one place."""
        body: dict[str, Any] = {"name": name}
        if parent_folder_id is not None:
            body["parentFolderId"] = parent_folder_id
        return self.request("POST", f"/matters/{matter_id}/documents/folders", json=body)

    def delete_file(self, matter_id: str, file_id: str) -> Any:
        """Delete a file from a matter (``DELETE /matters/{id}/documents/files/{fileId}``,
        async — returns the tracking Link). DESTRUCTIVE at the overlay."""
        return self.request("DELETE", f"/matters/{matter_id}/documents/files/{file_id}")

    # ---- webhook subscription management ----------------------------------
    def delete_webhook_subscription(self, subscription_id: str) -> Any:
        """Unsubscribe — ``DELETE /webhooks/{id}`` (Smokeball webhook CRUD). Used by
        the egress reconciler to remove a stale/duplicate op-managed subscription;
        a plain client method (not an MCP tool), so it carries no manifest class."""
        return self.request("DELETE", f"/webhooks/{subscription_id}")

    # ---- health -----------------------------------------------------------
    def _decode_token_scopes(self) -> list[str]:
        """Decode the current access token's granted scopes from its JWT ``scope``
        (Cognito) claim. Returns [] for an opaque/non-JWT token. Decodes only the
        payload segment and never returns the token itself — the granted scope list
        is the decisive signal for whether a 403 is a scope vs a permission denial."""
        tok = self._token
        if not tok or tok.count(".") != 2:
            return []
        payload_b64 = tok.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64url padding
        try:
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        except (ValueError, TypeError):
            return []
        raw = payload.get("scope", payload.get("scp", ""))
        if isinstance(raw, list):
            return sorted(str(s) for s in raw)
        return sorted(str(raw).split())

    def _refresh_token_persisted(self) -> bool | None:
        """Does the durable token file hold the CURRENT refresh token?

        The persist path is best-effort by design (a write failure must not
        break minting), which makes its failure silent: the in-memory token
        works until restart, then the process falls back to a dead prior file
        and the connector bricks. This check turns that silent state into an
        observable one (ss#2148) — the scheduled auth probe treats False as a
        failure so the persist race pages the same day, not at the restart.

        Compares content, never exposes it. None = not applicable
        (client_credentials mode, or no file configured).
        """
        if self.auth_mode != "authorization_code" or not self._refresh_token_file:
            return None
        try:
            on_disk = open(self._refresh_token_file, encoding="utf-8").read().strip()
        except OSError:
            return False
        return bool(self._refresh_token) and on_disk == self._refresh_token

    def auth_status(self) -> dict[str, Any]:
        """Mint a token and report connectivity — never the token/refresh value.
        ``granted_scopes`` is the decoded JWT scope claim (the live grant), so an
        operator can see exactly what the firm-delegated token is authorized for.

        NOTE (ss#2148, ADR 0080 amendment): this call performs a REAL refresh
        grant. If the vendor rotates refresh tokens on refresh, a scheduled
        auth_status probe is a KEEPALIVE — it renews the credential rather than
        watching it approach expiry. The console's token-age horizon alert is
        the backstop for the probe itself dying."""
        expires_in = self._mint_token()
        return {
            "authenticated": True,
            "auth_mode": self.auth_mode,
            "granted_scopes": self._decode_token_scopes(),
            "account_scoped": self._account_id is not None,
            "region": self.region,
            "environment": self.environment,
            "api_host": self.api_host,
            "token_expires_in": expires_in,
            "refresh_token_persisted": self._refresh_token_persisted(),
        }


# ---- env-driven construction (single source of truth) ---------------------
# The firm-delegated refresh token's durable home (ADR 0054): the Machine-hosted
# OAuth callback writes it here, and the client rewrites it in place on rotation.
_DEFAULT_REFRESH_TOKEN_FILE = "/opt/data/.smokeball-mcp/refresh_token"


def read_refresh_token(token_file: str) -> str | None:
    """Prefer the volume file (survives rotation); fall back to the
    ``SMOKEBALL_REFRESH_TOKEN`` env (cold-start seed); else None (client_credentials
    needs no token; authorization_code raises in the ctor when None)."""
    try:
        val = open(token_file, encoding="utf-8").read().strip()
        if val:
            return val
    except OSError:
        pass
    return os.environ.get("SMOKEBALL_REFRESH_TOKEN") or None


def build_client_from_env() -> SmokeballClient:
    """Construct a SmokeballClient from the connector's runtime env.

    The SINGLE source of truth for the tenant-selecting construction
    (region / environment / auth_mode / account / token file), reused by BOTH
    the MCP server's cached ``_get_client`` and the boot/connect webhook
    reconciler — so the security-sensitive mapping can never drift between the
    two (it was previously a comment-enforced "mirror"). Always passes
    ``refresh_token_file`` so a rotation during use is persisted to the canonical
    path the gateway reads, never desyncing the Machine's token."""
    token_file = os.environ.get("SMOKEBALL_REFRESH_TOKEN_FILE") or _DEFAULT_REFRESH_TOKEN_FILE
    return SmokeballClient(
        region=os.environ.get("SMOKEBALL_REGION", "us"),
        environment=os.environ.get("SMOKEBALL_ENVIRONMENT", "staging"),
        client_id=os.environ["SMOKEBALL_CLIENT_ID"],
        client_secret=os.environ["SMOKEBALL_CLIENT_SECRET"],
        api_key=os.environ["SMOKEBALL_API_KEY"],
        auth_mode=os.environ.get("SMOKEBALL_AUTH_MODE", "client_credentials"),
        refresh_token=read_refresh_token(token_file),
        refresh_token_file=token_file,
        account_id=os.environ.get("SMOKEBALL_ACCOUNT_ID") or None,
    )
