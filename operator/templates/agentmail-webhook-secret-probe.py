#!/usr/bin/env python3
"""Boot-smoke proof that the seat's AgentMail webhook secret is the VENDOR's.

WHY (scott, 2026-09-15, vfy_01M2HXT17Q32RX6TCV5NVZA9D6). The webhook gate
verifies every inbound AgentMail delivery against WEBHOOK_SECRET_AGENTMAIL. On
the scott seat that value had been staged from the GLOBAL Infisical key because
no per-seat key existed, and it was not the secret of the seat's own webhook at
the vendor. Every inbound email was rejected 401 "invalid signature" -- at
warning level, paged nowhere -- while 39 boot-smoke checks passed and the fleet
row read green. The first client-shaped email to the seat in two months was the
instrument that found it.

WHAT THIS DOES. Runs as ROOT under boot-smoke. Reads the agent-uid gateway's
environ for WEBHOOK_SECRET_AGENTMAIL, reads AGENTMAIL_WEBHOOK_READ_API_KEY from
PID 1's environ (Fly's init, root-only, where the Fly secret lives and where
entrypoint.sh's strip cannot reach), asks the vendor for the one webhook whose
URL names this seat's hostname, fetches that webhook's signing secret, and
compares. Only sha256 PREFIXES are printed; no value ever reaches the transcript.

WHY THE KEY COMES FROM PID 1 AND NOT THE AGENT'S ENVIRON (2026-09-17). The
first version asked the vendor with the agent's own AGENTMAIL_API_KEY. That key
is INBOX-SCOPED by design (ss#2258: the vendor itself refuses to let the agent
transmit), and AgentMail's webhooks are ORG-level objects carrying an inbox_ids
filter. Measured that day against the live vendor: the seat key returns 403
missing_permission, and a replacement key minted on the same inbox WITH
webhook_read returns 200 and ZERO webhooks -- permissions are intersected with
scope, so no inbox-scoped key can ever see the seat's webhook. The check could
therefore never pass on any seat whose agent key is correctly narrow; it was
unfalsifiable in the failing direction only, which is the worst kind. The fix is
a credential with the right SCOPE and nothing else: an org-scoped key holding
webhook_read ALONE, staged as AGENTMAIL_WEBHOOK_READ_API_KEY, read here from
PID 1, and unset by entrypoint.sh before the gateway exec so the agent never
holds an org-scoped credential just to satisfy a test.

WHAT MAKES IT ABLE TO FAIL. Stage the global secret onto a seat whose webhook
carries its own (the scott shape) and this exits 1 naming both hash prefixes.
Point the seat at a hostname with no vendor webhook and it exits 1. Two webhooks
for one host is a provisioning mistake and exits 1 rather than guessing. A seat
that carries the webhook secret while PID 1 carries no webhook-read key exits 1
saying so, because "cannot ask the vendor" must never read as "the value is
fine" -- that equivalence is what hid the scott outage for two months.

VACUOUS PASS, STATED. A seat whose agent env carries no WEBHOOK_SECRET_AGENTMAIL
(no AgentMail channel authored: ashton-price, smd-staging) passes with a line
saying so. That is the honest outcome for a webhook that does not exist; the
provisioner's agentmail-webhook-secret-fence is what guarantees an AgentMail
seat never boots without the per-seat value in the first place.

Usage:  agentmail-webhook-secret-probe.py <seat-hostname> [agent-username]
        e.g.  agentmail-webhook-secret-probe.py hermes-scott.fly.dev hermes
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import sys
import urllib.request
from collections.abc import Callable

_DEFAULT_USER = "hermes"
_API_BASE = "https://api.agentmail.to/v0"
_SECRET_VAR = "WEBHOOK_SECRET_AGENTMAIL"
# The org-scoped webhook_read key, read from PID 1 (Fly's init). entrypoint.sh
# strips it from its own environment before the gateway exec, so it is reachable
# here and nowhere agent-side: see WHY THE KEY COMES FROM PID 1, above.
_KEY_VAR = "AGENTMAIL_WEBHOOK_READ_API_KEY"
_INIT_PID = "1"

Fetch = Callable[[str, str], dict]


def _hash8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8] if value else "EMPTY"


def _environ(proc_root: str, pid: str) -> dict[str, str] | None:
    try:
        with open(os.path.join(proc_root, pid, "environ"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    env: dict[str, str] = {}
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        name, _, value = entry.partition(b"=")
        env[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def read_agent_env(proc_root: str, uid: int) -> dict[str, str] | None:
    """The environ of the first agent-uid process carrying the webhook secret.

    Returns None when no agent-uid process carries WEBHOOK_SECRET_AGENTMAIL,
    which is the vacuous case (no AgentMail webhook on this seat).
    """
    for pid in sorted(os.listdir(proc_root)):
        if not pid.isdigit():
            continue
        try:
            if os.stat(os.path.join(proc_root, pid)).st_uid != uid:
                continue
        except OSError:
            continue
        env = _environ(proc_root, pid)
        if env and _SECRET_VAR in env:
            return env
    return None


def find_webhook(webhooks: list[dict], host: str) -> tuple[dict | None, str]:
    """The single vendor webhook whose url names this seat's host, or why not."""
    matches = [w for w in webhooks if isinstance(w, dict) and host in str(w.get("url", ""))]
    if not matches:
        return None, f"no vendor webhook names host {host!r} ({len(webhooks)} webhook(s) visible)"
    if len(matches) > 1:
        return None, f"{len(matches)} vendor webhooks name host {host!r}; refusing to guess"
    return matches[0], ""


def verdict(env_secret: str, vendor_secret: str) -> tuple[bool, str]:
    """Compare by value, report by hash prefix only."""
    line = f"seat sha8={_hash8(env_secret)} vendor sha8={_hash8(vendor_secret)}"
    if not vendor_secret:
        return False, f"vendor returned no signing secret for the webhook ({line})"
    if env_secret == vendor_secret:
        return True, f"agentmail webhook secret matches the vendor ({line})"
    return False, f"agentmail webhook secret does NOT match the vendor ({line})"


def _urllib_fetch(path: str, key: str) -> dict:
    # The host is a constant https URL; `path` is one of two literals built in
    # run() ("/webhooks", "/webhooks/<id from the vendor's own listing>"). Refuse
    # anything else so no scheme or host can ever be smuggled in.
    if not path.startswith("/webhooks") or "://" in path:
        raise ValueError("unexpected vendor path")
    url = _API_BASE + path
    req = urllib.request.Request(  # noqa: S310 - fixed https vendor host; the path is allowlisted just above
        url, headers={"Authorization": "Bearer " + key}
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https vendor host; the path is allowlisted just above
        return json.load(resp)


def read_init_key(proc_root: str, pid: str = _INIT_PID) -> str:
    """The org-scoped webhook_read key from PID 1's environ, or "" when absent."""
    env = _environ(proc_root, pid)
    return (env or {}).get(_KEY_VAR, "").strip()


def run(
    host: str,
    user: str,
    proc_root: str = "/proc",
    fetch: Fetch = _urllib_fetch,
) -> int:
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        print(f"FAIL: agent user {user!r} does not exist")
        return 1
    env = read_agent_env(proc_root, uid)
    if env is None:
        print(f"vacuous: no {user}-uid process carries {_SECRET_VAR} (seat authors no AgentMail webhook)")
        return 0
    key = read_init_key(proc_root)
    if not key:
        print(
            f"FAIL: agent env carries {_SECRET_VAR} but PID 1 carries no {_KEY_VAR}; "
            "cannot ask the vendor. Provisioning stages it as a Fly secret (org-scoped, "
            "webhook_read only) - an inbox-scoped key cannot read this seat's webhook."
        )
        return 1
    try:
        listing = fetch("/webhooks", key)
        webhook, why = find_webhook(list(listing.get("webhooks") or []), host)
        if webhook is None:
            print(f"FAIL: {why}")
            return 1
        one = fetch(f"/webhooks/{webhook.get('webhook_id')}", key)
    except Exception as exc:  # noqa: BLE001 - any vendor failure is a fail-closed verdict
        print(f"FAIL: vendor lookup failed: {type(exc).__name__}")
        return 1
    ok, msg = verdict(env.get(_SECRET_VAR, ""), str(one.get("secret") or ""))
    print(("PASS: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: agentmail-webhook-secret-probe.py <seat-hostname> [agent-username]")
        return 2
    host = argv[1]
    user = argv[2] if len(argv) > 2 else _DEFAULT_USER
    return run(host, user)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
