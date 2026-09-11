"""AgentMail send credential and seat inbox identity, owned only by the broker.

WHY THIS EXISTS (ss#2258). On 2026-08-03/05/07/09 the pilot rehearsal seat sent
four fabricated emails to a real client principal, with **no audit row for any of
them**. Zero rows means the sending path never traversed the gateway's trust hook
— so no in-agent control could have stopped it, including controls we might add,
because the agent process holds a credential that answers to no one. The Captain's
requirement afterwards was exact: the seat "should never have been able to send to
an unapproved email address no matter where it came from."

"No matter where it came from" is only enforceable by whoever holds the key. That
is this module and its ``agentmail_ops`` sibling. Two fences stack, and neither is
sufficient alone:

1. **The agent-reachable key cannot send at all** — vendor-enforced. The gateway's
   AgentMail key is inbox-scoped with `message_send`/`draft_send` withheld, so no
   code path on the Machine can transmit regardless of how it is reached. That
   fence lives at AgentMail, not here.
2. **The send-capable key fences the recipient** — the authored counterparty
   surface, now shared with the msgraph channel in ``recipient_policy``. An
   inbox-scoped send key still sends anywhere, and the incident was the seat's own
   inbox mailing an unapproved human.

Note that fence 1 is a GIFT OF THIS VENDOR, not a property of the design.
AgentMail issues per-inbox keys with a permission whitelist; Microsoft Graph
app-only auth does not, which is why the msgraph channel gets fence 2 alone until
a second app registration exists. See ``msgraph_auth`` for that argument in full.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# Re-exported for the callers and tests that predate the msgraph wave, when the
# counterparty fence lived here. Its home is ``recipient_policy`` now — an
# address is an address whichever transport carries it, and a second copy would
# be a second fence that can disagree with this one.
from .recipient_policy import (
    RecipientPolicy,
    authored_policy,
    canonicalize,
    normalize_address,
    sender_key,
)

#: Env var carrying the SEND-capable, inbox-scoped AgentMail key. Deliberately a
#: DIFFERENT name from the gateway's ``AGENTMAIL_API_KEY``: the two keys are not
#: interchangeable, and a shared name would let a future entrypoint edit silently
#: hand the send key to the agent — the exact failure this design exists to make
#: impossible. Root materializes this to a 0600 broker-owned file and unsets it
#: before the exec-drop, so it never reaches the gateway environment.
SEND_KEY_ENV = "AGENTMAIL_SEND_API_KEY"


def materialize_credential(credential_path: Path) -> None:
    """Write the send key into the broker-owned store, 0600.

    Mirrors ``google_auth.materialize_credential``: root calls this under the
    broker venv while it still holds the secret in env, then chowns the file to
    the broker uid and unsets the variable. The broker itself reads the FILE, so
    a respawn needs nothing the parent later dropped.

    A missing key is not an error here — a seat with no AgentMail connector never
    stages one. It becomes an error at send time, where it is fail-closed.
    """
    key = (os.environ.get(SEND_KEY_ENV) or "").strip()
    if not key:
        return
    credential_path.write_text(key, encoding="utf-8")
    credential_path.chmod(0o600)


def load_send_key(credential_path: Path) -> str:
    """Read the send key from the broker-owned file. Empty ⇒ caller fail-closes."""
    try:
        return credential_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def seat_inbox_address(customer_path: Path, customer_slug: str) -> str:
    """The seat's OWN inbox address, from authored config or the slug convention.

    Taken from the broker's trusted customer.yaml and **never from the request**.
    Under overlay#244 the gateway resolved this itself and a bug there had it
    sending from whichever inbox the account listed first; here the identity is
    not something a caller can express, so that class cannot recur.
    """
    data = yaml.safe_load(customer_path.read_text(encoding="utf-8")) or {}
    connectors = data.get("connectors") or {}
    email = connectors.get("Email") if isinstance(connectors, dict) else None
    if isinstance(email, dict):
        authored = normalize_address(email.get("inbox_address"))
        if authored:
            return authored
    return f"{customer_slug.strip().lower()}@agentmail.to"


__all__ = [
    "SEND_KEY_ENV",
    "RecipientPolicy",
    "authored_policy",
    "canonicalize",
    "load_send_key",
    "materialize_credential",
    "normalize_address",
    "seat_inbox_address",
    "sender_key",
]
