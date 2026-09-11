"""Google credential loading owned exclusively by the broker process."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import yaml


def materialize_credential(credential_path: Path) -> None:
    """Decode the configured Google credential into the broker-owned store."""
    encoded = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or os.environ.get("GOOGLE_TOKEN_JSON")
    if not encoded:
        return
    raw = base64.b64decode(encoded, validate=True)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Google credential must decode to a JSON object")
    credential_path.write_bytes(raw)
    credential_path.chmod(0o600)


def _customer_google_auth(customer_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(customer_path.read_text(encoding="utf-8")) or {}
    config = data.get("google_auth") or {}
    if not isinstance(config, dict):
        raise RuntimeError("customer.yaml google_auth must be an object")
    return config


def authored_identities(config: dict[str, Any]) -> tuple[str, set[str], dict[str, set[str]]]:
    """Derive the authorized impersonation surface from authored `google_auth`.

    Returns `(default_subject, allowed_subjects, send_as_by_subject)` where
    `allowed_subjects` is the default subject plus every authored
    `managed_mailboxes[].address`, and `send_as_by_subject` maps each managed
    address to its authored "Send mail as" allowlist. This is the broker's own
    copy of the authorization policy — it is read from the customer.yaml the
    broker already trusts, never from the request, so the broker can validate a
    requested subject/From independently of the gateway (the credential holder
    must not delegate the "may this be impersonated?" decision to the
    uncredentialed, injection-exposed gateway).
    """
    default = str(config.get("subject") or "").strip()
    allowed: set[str] = {default} if default else set()
    send_as: dict[str, set[str]] = {}
    for mailbox in config.get("managed_mailboxes") or []:
        if not isinstance(mailbox, dict):
            continue
        address = str(mailbox.get("address") or "").strip()
        if not address:
            continue
        allowed.add(address)
        send_as[address] = {
            str(identity).strip()
            for identity in mailbox.get("send_as") or []
            if isinstance(identity, str) and identity.strip()
        }
    return default, allowed, send_as


def credentials(credential_path: Path, customer_path: Path, subject: str = ""):
    """Build credentials from broker-owned secret material and authored config.

    `subject` optionally selects which mailbox to impersonate; empty ⇒ the
    authored default. The broker fail-closes on any subject not in the authored
    surface (default + `managed_mailboxes`), so a requested subject the gateway
    can name but the customer never authored never reaches Google.
    """
    info = json.loads(credential_path.read_text(encoding="utf-8"))
    if info.get("type") == "service_account":
        config = _customer_google_auth(customer_path)
        default, allowed, _ = authored_identities(config)
        scopes = [scope.strip() for scope in config.get("scopes") or [] if isinstance(scope, str) and scope.strip()]
        effective = str(subject or "").strip() or default
        if not effective or not scopes:
            raise RuntimeError("DWD requires authored subject and scopes")
        if effective not in allowed:
            raise RuntimeError(f"subject {effective!r} is not an authored impersonation target")
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_info(info, scopes=scopes, subject=effective)

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_info(info)
    if not creds.valid:
        if not creds.expired or not creds.refresh_token:
            raise RuntimeError("Google token is invalid and not refreshable")
        creds.refresh(Request())
        credential_path.write_text(creds.to_json(), encoding="utf-8")
        credential_path.chmod(0o600)
    return creds


def service(api: str, version: str, credential_path: Path, customer_path: Path, subject: str = ""):
    """Build a Google API client within the broker security domain."""
    from googleapiclient.discovery import build

    return build(
        api,
        version,
        credentials=credentials(credential_path, customer_path, subject),
        cache_discovery=False,
    )
