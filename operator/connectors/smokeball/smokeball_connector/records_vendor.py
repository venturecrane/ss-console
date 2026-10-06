"""The firm's records-retrieval vendor's Order API, as the Smokeball connector calls it.

WHY THE VENDOR IS NOT NAMED HERE. This is a public repo, and which records
vendor a client firm uses is that firm's information: the vendor's name is on
the repo-wide scrub denylist (``tests/medchron-scrub.test.ts``). So the vendor's
identity lives with the seat, not the code: its API base URL and the display name
the read-back uses are staged per customer next to the token
(``bin/lib/stage-smokeball.sh``), and nothing in this tree spells them.

WHY THIS LIVES INSIDE THE SMOKEBALL CONNECTOR. Every order the firm places is
linked to a Smokeball matter (``matter_type: smokeball``), and an order needs the
client's Social Security number, date of birth and address, which live on the
Smokeball client contact. Two facts decide the placement:

* each connector runs in its own isolated venv (``operator/templates/Dockerfile``,
  the per-connector ``--no-deps`` installs), so a second connector would need its
  own Smokeball credentials;
* the Smokeball client rotates its refresh token without a lock
  (``client.py`` ``_mint_token``), so a second process holding the same token
  races it.

So one process owns the Smokeball token, reads the client's identifiers in
process, and hands them to the vendor directly. They never enter a tool return,
an error message, or the model's context.

Vendor shape: the vendor's published OpenAPI document (Order API 1.0.0, Bearer
auth), read 2026-10-05. The order lifecycle is ``POST /api/v1/orders/_new``
(draft) then ``PATCH /api/v1/orders/_new/{id}``, ``POST .../upload`` (multipart,
PDF only, 10 MB, bracket notation ``files[0][file]``), ``POST .../validate`` and
``POST .../finish`` (converted into real tasks; no longer modifiable). ``GET
/api/v1/orders/external:{matter_id}`` reads the result back by the Smokeball
matter id; ``GET /api/v1/get_locations`` searches its custodian directory. NOT
yet exercised against a live account: the firm's API token is not in hand.

THE SEAT'S THREE VALUES, all per customer: ``RECORDS_VENDOR_API_TOKEN`` (the
firm's own account), ``RECORDS_VENDOR_API_URL`` (https only) and
``RECORDS_VENDOR_NAME`` (shown to the administrator). Without the token or the
URL, :func:`client_from_env` returns None and every tool says plainly that the
records vendor is not connected on this seat.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

TOKEN_ENV = "RECORDS_VENDOR_API_TOKEN"
URL_ENV = "RECORDS_VENDOR_API_URL"
NAME_ENV = "RECORDS_VENDOR_NAME"
DEFAULT_VENDOR_NAME = "the records vendor"
NOT_CONNECTED = "The records vendor is not connected on this seat (no API token)."

#: The vendor's upload ceiling for one file.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_KINDS = (
    "completed_hipaa_form",
    "signed_signature_page",
    "power_of_attorney",
    "death_certificate",
    "special_form",
)

#: The vendor's directory search answers in ~23s from a seat (measured
#: 2026-10-06 on ashton-price) and twice ran past a 30s limit the same day, so
#: the read limit leaves room. Connecting stays short: a dead host fails fast.
_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
_MAX_ERROR_CHARS = 400
#: Anything shaped like an SSN, with or without separators, is blanked out of
#: every vendor message before it is kept: a validation error may echo a value.
_SSN_SHAPE = re.compile(r"(?<!\d)\d{3}[- ]?\d{2}[- ]?\d{4}(?!\d)")
_UUID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


class RecordsVendorNotConnected(RuntimeError):
    """No API token (or no API URL) is staged on this seat."""

    def __init__(self) -> None:
        super().__init__(NOT_CONNECTED)


class RecordsVendorApiError(RuntimeError):
    """The vendor answered 4xx/5xx, or could not be reached. Carries the status and
    the vendor's own (scrubbed, truncated) message, never the request body."""

    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail
        super().__init__(f"records vendor {method} {path} -> HTTP {status}: {detail or '(no message)'}")


def scrub(text: Any) -> str:
    """A vendor string safe to keep: SSN-shaped runs blanked, one line, bounded."""
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    flat = _SSN_SHAPE.sub("[redacted]", flat)
    return flat if len(flat) <= _MAX_ERROR_CHARS else flat[: _MAX_ERROR_CHARS - 3] + "..."


def _error_detail(resp: httpx.Response) -> str:
    """The vendor's message and field errors, never the raw body whole."""
    try:
        body = resp.json()
    except ValueError:
        return scrub(resp.text)
    if not isinstance(body, dict):
        return scrub(body)
    parts = [str(body.get("message") or "")]
    errors = body.get("errors")
    if isinstance(errors, dict):
        for field, msgs in errors.items():
            listed = msgs if isinstance(msgs, list) else [msgs]
            parts.append(f"{field}: {'; '.join(str(m) for m in listed)}")
    return scrub(" | ".join(p for p in parts if p))


def _require_order_id(order_record_id: Any) -> str:
    text = str(order_record_id or "").strip()
    if not _UUID.match(text):
        raise ValueError("an order record id is a UUID")
    return text


class RecordsVendorClient:
    """A thin client over the endpoints the records-order tools use."""

    def __init__(
        self,
        token: str,
        base_url: str,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        if not token or not base_url.startswith("https://"):
            raise RecordsVendorNotConnected()
        self._token = token
        self._base = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=_TIMEOUT)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            resp = self._http.request(method, self._base + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RecordsVendorApiError(
                method, path, 0, f"the records vendor could not be reached ({type(exc).__name__})"
            ) from exc
        if resp.status_code >= 400:
            raise RecordsVendorApiError(method, path, resp.status_code, _error_detail(resp))
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise RecordsVendorApiError(method, path, resp.status_code, "the answer was not JSON") from exc

    # ---- directory ---------------------------------------------------------
    def get_locations(self, term: str, zip_code: str | None = None) -> list[dict[str, Any]]:
        """``GET /api/v1/get_locations``: custodian locations matching a term."""
        params = {"term": term}
        if zip_code:
            params["zip"] = zip_code
        found = self._request("GET", "/api/v1/get_locations", params=params)
        if isinstance(found, dict):
            # The spec documents one location object; a list, or a list under
            # ``data``, is what a search answers. Accept all three shapes.
            found = found.get("data") if isinstance(found.get("data"), list) else [found]
        return [row for row in found if isinstance(row, dict)] if isinstance(found, list) else []

    # ---- the order lifecycle -----------------------------------------------
    def create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._as_dict(self._request("POST", "/api/v1/orders/_new", json=body))

    def patch_order(self, order_record_id: str, body: dict[str, Any]) -> dict[str, Any]:
        oid = _require_order_id(order_record_id)
        return self._as_dict(self._request("PATCH", f"/api/v1/orders/_new/{oid}", json=body))

    def upload(self, order_record_id: str, filename: str, blob: bytes, kind: str) -> dict[str, Any]:
        """One PDF onto a draft order, in the vendor's bracket notation."""
        oid = _require_order_id(order_record_id)
        if kind not in UPLOAD_KINDS:
            raise ValueError(f"upload kind must be one of {UPLOAD_KINDS}")
        if len(blob) > MAX_UPLOAD_BYTES:
            raise ValueError("the records vendor accepts files up to 10 MB")
        files = {"files[0][file]": (filename, blob, "application/pdf")}
        data = {"files[0][filename]": filename, "files[0][kind]": kind}
        return self._as_dict(self._request("POST", f"/api/v1/orders/_new/{oid}/upload", files=files, data=data))

    def validate(self, order_record_id: str) -> dict[str, Any]:
        oid = _require_order_id(order_record_id)
        return self._as_dict(self._request("POST", f"/api/v1/orders/_new/{oid}/validate"))

    def finish(self, order_record_id: str) -> dict[str, Any]:
        oid = _require_order_id(order_record_id)
        return self._as_dict(self._request("POST", f"/api/v1/orders/_new/{oid}/finish"))

    def orders_for_matter(self, matter_id: str) -> dict[str, Any]:
        """``GET /api/v1/orders/external:{matter_id}``: the locations the vendor
        holds for this Smokeball matter."""
        return self._as_dict(self._request("GET", f"/api/v1/orders/external:{matter_id}"))

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


def client_from_env(http: httpx.Client | None = None) -> RecordsVendorClient | None:
    """The seat's client, or None when the token or the https URL is not staged.
    Built per call: nothing is cached between tool calls."""
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    url = (os.environ.get(URL_ENV) or "").strip()
    if not token or not url.startswith("https://"):
        return None
    return RecordsVendorClient(token, url, http=http)


def vendor_name() -> str:
    """The display name the read-back uses, as the seat authored it."""
    name = re.sub(r"\s+", " ", os.environ.get(NAME_ENV) or "").strip()
    return name[:60] if name else DEFAULT_VENDOR_NAME


__all__ = [
    "DEFAULT_VENDOR_NAME",
    "MAX_UPLOAD_BYTES",
    "NOT_CONNECTED",
    "TOKEN_ENV",
    "UPLOAD_KINDS",
    "URL_ENV",
    "RecordsVendorApiError",
    "RecordsVendorClient",
    "RecordsVendorNotConnected",
    "client_from_env",
    "scrub",
    "vendor_name",
]
