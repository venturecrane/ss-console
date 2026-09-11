"""Microsoft Graph send credential and pinned mailbox, owned only by the broker.

The msgraph half of ss#2258. ``agentmail_auth`` states the incident; this module
states the ONE way this channel differs, because that difference decides how much
of the fix is available to us and how much is not.

WHY MSGRAPH ONLY GETS ONE OF THE TWO FENCES TODAY.

The AgentMail fix stacks two independent fences: the vendor makes the agent's key
*incapable* of transmitting (an inbox-scoped key with ``message_send`` withheld),
and the broker fences the recipient. Fence 1 there is a gift of that vendor's
credential model, not a property of our design.

Microsoft Graph app-only auth has no equivalent. The client-credentials flow must
request ``/.default``, which means "every application permission already granted
and admin-consented for this app registration" — there is no narrower token, and
no per-request scope-down. One app registration is therefore one permission set:
if it can read mail it can also send mail, whenever ``Mail.Send`` is among its
grants.

And the agent process legitimately needs Graph credentials. Two consumers, both
read-side and both real: the delta poller that pulls inbound mail
(``shared/msgraph_poller``, running in the gateway) and the ``msgraph-mail`` MCP
server that is the agent's own mail tool surface. Stripping ``MSGRAPH_*`` from the
gateway today would not harden the seat; it would blind it.

The way out is TWO app registrations in the tenant — a read-only one
(``Mail.ReadWrite``, no ``Mail.Send``) whose credential the agent holds, and a
send-capable one (``Mail.Send``) whose secret only ever reaches the broker store,
both pinned to the one mailbox by an Exchange ``ApplicationAccessPolicy``. That is
a tenant action requiring admin consent, so on a client seat it is the client's to
grant. As of 2026-08-13 it is REQUIRED rather than hoped for: the Captain made two
registrations a precondition of the channel, and ``provision-customer.sh`` refuses
to stand up an msgraph seat whose ``MSGRAPH_SEND_*`` values are unstaged, equal to
the read app's, or half-staged.

THE BROKER HOLDS BOTH CREDENTIALS; THE AGENT HOLDS ONLY THE READ APP.

The reply verb must verify the ORIGINAL SENDER itself (a caller that could name
the sender could name any sender), and under the two-app fence the send app
cannot read — its ``GET /messages/{id}`` is 403 by construction, observed live
on the first production two-app seat (hermes-ashton-price 2026-08-19,
overlay#280). So the broker store carries a SECOND file: the read app's
credential, materialized from the same ``MSGRAPH_*`` env the gateway keeps.
This grants the broker nothing novel — reading is the LOWER privilege, and the
agent already holds this exact credential — while keeping the send secret where
it always was. Do not "simplify" the read file away: without it no reply can
ever transmit on a two-app seat.

So the position on a seat that provisioned successfully:

* **Fence 2 is live** — every governed send and reply crosses this boundary, is
  checked against the seat's authored counterparty surface, and leaves a row
  written by the credential holder.
* **Fence 1 is live too.** The agent's ``MSGRAPH_CLIENT_SECRET`` belongs to a
  registration without ``Mail.Send``, so a rogue in-agent path that mints its own
  Graph token and POSTs ``/sendMail`` is refused by Microsoft, not by us. Proven
  on the sandbox seat 2026-08-13 (``vfy_01KZXX523V6JNWEETG4PSZDQY3``): the read
  app got ``403 ErrorAccessDenied``, this file's credential got ``202``.

Setup for the client's tenant admin:
``docs/runbooks/operator/ms-graph-azure-ad-setup.md`` — "Client-custody app-only
registrations".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .recipient_policy import normalize_address

#: The send-capable Graph app credential, named apart from the gateway's
#: ``MSGRAPH_*`` on purpose. Sharing the names would make the split invisible in
#: config and let a future entrypoint edit hand the agent the send app by
#: accident — the exact failure the AgentMail key split exists to prevent.
#: The MAILBOX is deliberately absent: it comes from customer.yaml (below), so a
#: caller cannot express it and a secret cannot contradict the authored config.
SEND_ENV = (
    "MSGRAPH_SEND_TENANT_ID",
    "MSGRAPH_SEND_CLIENT_ID",
    "MSGRAPH_SEND_CLIENT_SECRET",
)

#: The read app's credential — the SAME registration the gateway/agent already
#: holds (Mail.ReadWrite, no Mail.Send). The broker needs it because the reply
#: verb's sender-verification GET cannot run on the send app under the two-app
#: fence (overlay#280).
READ_ENV = (
    "MSGRAPH_TENANT_ID",
    "MSGRAPH_CLIENT_ID",
    "MSGRAPH_CLIENT_SECRET",
)


def _materialize(env_names: tuple[str, ...], credential_path: Path, label: str) -> None:
    """Write one Graph credential into the broker-owned store, 0600.

    Mirrors ``google_auth`` / ``agentmail_auth``: root calls this under the broker
    venv while it still holds the secrets in env, then chowns the file to the
    broker uid. The broker reads the FILE, so a respawn needs nothing the parent
    later dropped.

    None of the three present ⇒ no-op. A seat with no msgraph connector never
    stages them, and absence becomes a fail-closed refusal at use time.

    SOME but not all present ⇒ raise. A half-staged credential is a provisioning
    mistake, and the seat must not boot believing it has a path it does not
    have. Names only in the error; never a value.
    """
    tenant, client, secret = env_names
    present = {name: (os.environ.get(name) or "").strip() for name in env_names}
    missing = [name for name, value in present.items() if not value]
    if len(missing) == len(env_names):
        return
    if missing:
        raise RuntimeError(
            f"msgraph {label} credential is partially staged; refusing to boot a seat "
            f"with a half-wired {label} path. Missing: {', '.join(sorted(missing))}"
        )
    credential_path.write_text(
        json.dumps(
            {
                "tenant_id": present[tenant],
                "client_id": present[client],
                "client_secret": present[secret],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    credential_path.chmod(0o600)


def materialize_credential(credential_path: Path) -> None:
    """The send app's credential (``MSGRAPH_SEND_*``) → the broker store."""
    _materialize(SEND_ENV, credential_path, "send")


def materialize_read_credential(credential_path: Path) -> None:
    """The read app's credential (``MSGRAPH_*``) → the broker store.

    Written by the ROOT entrypoint only: the broker itself runs under ``env -i``
    with an allowlist that deliberately excludes secrets, so calling this from
    the broker process is a guaranteed no-op (all-absent → return) — kept there
    for shape parity with the send path, nothing more. The file on disk is what
    survives respawns."""
    _materialize(READ_ENV, credential_path, "read")


def load_credential(credential_path: Path) -> dict[str, str]:
    """Read the Graph send credential. ``{}`` ⇒ the caller fail-closes.

    Every failure mode collapses to "no credential" on purpose: an unreadable,
    truncated, or non-JSON credential file must refuse a send, never half-attempt
    one with a partial value.
    """
    try:
        raw = credential_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    fields = ("tenant_id", "client_id", "client_secret")
    values = {key: str(parsed.get(key) or "").strip() for key in fields}
    return values if all(values.values()) else {}


def _email_connector(customer_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(customer_path.read_text(encoding="utf-8")) or {}
    connectors = data.get("connectors") or {}
    email = connectors.get("Email") if isinstance(connectors, dict) else None
    return email if isinstance(email, dict) else {}


def seat_mailbox(customer_path: Path) -> str:
    """The mailbox this seat sends AS, from ``connectors.Email.msgraph_auth``.

    Read from the broker's trusted customer.yaml and **never from the request**,
    for the same reason the AgentMail inbox is pinned: a caller that can name the
    mailbox can name someone else's. Graph's own client also pins the mailbox at
    construction (every path is ``/users/{mailbox}/…``), so this is the second of
    two locks, and the tenant-side ApplicationAccessPolicy is the third.

    Returns ``""`` when the seat authors no msgraph mailbox — which the ops layer
    treats as "this seat has no Graph send path", not as a default.
    """
    email = _email_connector(customer_path)
    if str(email.get("adapter") or "").strip().lower() != "msgraph":
        return ""
    auth = email.get("msgraph_auth")
    if not isinstance(auth, dict):
        return ""
    return normalize_address(auth.get("mailbox"))


__all__ = [
    "SEND_ENV",
    "load_credential",
    "materialize_credential",
    "seat_mailbox",
]
