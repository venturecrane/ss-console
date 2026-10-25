"""WordPress REST client (``/wp-json/wp/v2``) over an Application Password.

Application Passwords (WordPress core since 5.6) authenticate one user over
HTTPS Basic auth. The password is per user, revocable by the firm from that
user's profile page, and never grants more than the user's role: issued to a
Contributor, it can create and edit that user's own unpublished posts and
nothing else. That is the posture this connector is built for.

Transport rules, enforced at construction:

* ``https`` only. A plain-``http`` site is refused, because Basic auth would put
  the password on the wire in the clear. The one exception is a loopback host
  (``localhost`` / ``127.0.0.1`` / ``::1``), which is how the integration proof
  runs against a local WordPress; a seat's customer.yaml is validated https-only
  on both sides, so a seat can never reach this branch.
* No redirect following. A redirect off the site would carry the Authorization
  header to another host.

Errors carry the HTTP status and a bounded slice of the response body, never the
credential.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ERR_BODY_CHARS = 600


class WordPressApiError(RuntimeError):
    """A non-2xx WordPress response. Carries the status and the WordPress error
    code (``rest_cannot_create`` and the like) so the agent can tell a
    permission refusal from an outage."""

    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        self.status = status
        self.code = ""
        self.message = body[:_ERR_BODY_CHARS]
        try:
            import json

            parsed = json.loads(body)
            if isinstance(parsed, dict):
                self.code = str(parsed.get("code") or "")
                self.message = str(parsed.get("message") or self.message)[:_ERR_BODY_CHARS]
        except ValueError:
            pass
        super().__init__(f"WordPress {method} {path} -> HTTP {status} {self.code}: {self.message}".strip())


class WordPressConfigError(ValueError):
    """The connector's env is missing or unusable (a seat wiring problem)."""


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_site_url(raw: str) -> str:
    """Validate and normalize the site root. Refuses anything but https, except
    a loopback host (local proof only). Returns the root without a trailing
    slash."""
    url = (raw or "").strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        raise WordPressConfigError("WORDPRESS_SITE_URL must be an absolute URL such as https://www.example.com")
    if parts.scheme == "https":
        pass
    elif parts.scheme == "http" and _is_loopback(host):
        pass
    else:
        raise WordPressConfigError(
            "WORDPRESS_SITE_URL must use https (Basic auth would otherwise send the password in the clear)"
        )
    if parts.query or parts.fragment:
        raise WordPressConfigError("WORDPRESS_SITE_URL must be the site root, with no query or fragment")
    return url.rstrip("/")


class WordPressClient:
    def __init__(
        self,
        *,
        site_url: str,
        username: str,
        app_password: str,
        timeout: float = 30.0,
    ) -> None:
        if not username:
            raise WordPressConfigError("WORDPRESS_USERNAME is empty")
        if not app_password:
            raise WordPressConfigError("WORDPRESS_APP_PASSWORD is empty")
        self.site_url = normalize_site_url(site_url)
        self.api_base = f"{self.site_url}/wp-json/wp/v2"
        self.username = username
        # Application Passwords are displayed with spaces; WordPress accepts
        # them either way. Kept as given.
        self._auth = httpx.BasicAuth(username, app_password)
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)
        self._me: dict[str, Any] | None = None

    # ---- transport ------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.api_base}{path}"
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        resp = self._http.request(
            method,
            url,
            params=clean or None,
            json=json_body,
            content=content,
            headers=headers,
            auth=self._auth,
        )
        if resp.status_code >= 300:
            raise WordPressApiError(method, path, resp.status_code, resp.text)
        if not resp.content:
            return None
        return resp.json()

    # ---- identity -------------------------------------------------------
    def me(self) -> dict[str, Any]:
        """The authenticated user (id, name, slug). Cached for the process."""
        if self._me is None:
            data = self.request("GET", "/users/me", params={"context": "view"})
            if not isinstance(data, dict) or "id" not in data:
                raise WordPressApiError("GET", "/users/me", 200, "response carried no user id")
            self._me = data
        return self._me

    def edit_url(self, post_id: int) -> str:
        return f"{self.site_url}/wp-admin/post.php?post={int(post_id)}&action=edit"


def build_client_from_env() -> WordPressClient:
    """The single source of truth for the runtime env mapping."""
    missing = [
        n for n in ("WORDPRESS_SITE_URL", "WORDPRESS_USERNAME", "WORDPRESS_APP_PASSWORD") if not os.environ.get(n)
    ]
    if missing:
        raise WordPressConfigError(f"wordpress connector is not configured: missing {', '.join(missing)}")
    return WordPressClient(
        site_url=os.environ["WORDPRESS_SITE_URL"],
        username=os.environ["WORDPRESS_USERNAME"],
        app_password=os.environ["WORDPRESS_APP_PASSWORD"],
    )
