#!/usr/bin/env python3
"""Boot-smoke proof that the agent's Graph credential cannot send, on a seat
that may send AS staff (ADR 0089).

WHY. A seat that authors ``scope.staff_send_as`` has had the firm grant its
Operator mailbox Exchange Send As on real people. From then on, any credential
that can call ``sendMail`` on that mailbox can put a staff member's name on a
message. The broker holds the one credential meant to (the send app), and the
agent holds the READ app for its poller and mail tools. If the read app ever
carried ``Mail.Send``, ``execute_code`` could mint a token and send as any
rostered staff member to anyone, with no approval and no row. The two-app fence
at provisioning refuses a seat whose read and send apps are the SAME
registration; it cannot see what roles an app was granted in the tenant. This
asks Microsoft.

WHAT THIS DOES. Runs as ROOT under boot-smoke. Reads the agent-uid gateway's
environ for MSGRAPH_TENANT_ID / MSGRAPH_CLIENT_ID / MSGRAPH_CLIENT_SECRET, mints
an app-only token, decodes its ``roles`` claim (the JWT payload, no signature
check needed: we asked Microsoft directly over TLS), and fails if ``Mail.Send``
is among them. Prints role NAMES only; never the secret or the token.

WHAT MAKES IT ABLE TO FAIL. Grant Mail.Send to the read registration in the
tenant and this exits 1 naming the role. A seat authoring staff_send_as whose
gateway carries no msgraph credential exits 1, because "could not ask" must
never read as "cannot send". A failed token mint exits 1.

VACUOUS PASS, STATED. A seat whose customer.yaml authors no staff_send_as passes
with a line saying so: without Send As on anybody, the read app's reach is the
seat's own mailbox, which the existing fences already bound.

Usage:  msgraph-read-app-cannot-send-probe.py [customer-yaml] [agent-username]
"""

from __future__ import annotations

import base64
import json
import os
import pwd
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable

_DEFAULT_YAML = "/var/lib/smd-config/customer.yaml"
_DEFAULT_USER = "hermes"
_VARS = ("MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET")
_FORBIDDEN_ROLES = ("Mail.Send",)

Mint = Callable[[str, str, str], str]


def authors_staff_send_as(yaml_text: str) -> bool:
    """Whether the seat names anyone the Operator may send as. Parsed, not grepped."""
    import yaml

    data = yaml.safe_load(yaml_text) or {}
    scope = data.get("scope") if isinstance(data, dict) else None
    entries = scope.get("staff_send_as") if isinstance(scope, dict) else None
    return isinstance(entries, list) and any(isinstance(e, dict) and e.get("address") for e in entries)


def _environ(proc_root: str, pid: str) -> dict[str, str] | None:
    try:
        with open(os.path.join(proc_root, pid, "environ"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    env: dict[str, str] = {}
    for entry in raw.split(b"\x00"):
        if entry:
            name, _, value = entry.partition(b"=")
            env[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def read_agent_credential(proc_root: str, uid: int) -> dict[str, str] | None:
    """The msgraph read credential from the first agent-uid process carrying all three vars."""
    for pid in sorted(os.listdir(proc_root)):
        if not pid.isdigit():
            continue
        try:
            if os.stat(os.path.join(proc_root, pid)).st_uid != uid:
                continue
        except OSError:
            continue
        env = _environ(proc_root, pid) or {}
        if all(env.get(v, "").strip() for v in _VARS):
            return {v: env[v].strip() for v in _VARS}
    return None


def _urllib_mint(tenant: str, client_id: str, secret: str) -> str:
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
        }
    ).encode()
    url = f"https://login.microsoftonline.com/{urllib.parse.quote(tenant, safe='')}/oauth2/v2.0/token"
    request = urllib.request.Request(url, data=data, method="POST")  # noqa: S310 - fixed https login host; tenant id is url-quoted
    # Fixed https login host; the tenant id is url-quoted.
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed https login host; tenant id is url-quoted
        return str(json.loads(response.read().decode("utf-8")).get("access_token") or "")


def roles_of(token: str) -> list[str]:
    """The ``roles`` claim of an app-only access token (JWT payload, base64url)."""
    parts = token.split(".")
    if len(parts) < 2:
        return []
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))
    except ValueError:
        return []
    roles = claims.get("roles") if isinstance(claims, dict) else None
    return [str(r) for r in roles] if isinstance(roles, list) else []


def run(yaml_path: str, user: str, proc_root: str = "/proc", mint: Mint = _urllib_mint) -> int:
    try:
        yaml_text = open(yaml_path, encoding="utf-8").read()
    except OSError as exc:
        print(f"FAIL: cannot read {yaml_path}: {type(exc).__name__}")
        return 1
    if not authors_staff_send_as(yaml_text):
        print("vacuous: seat authors no scope.staff_send_as; the read app holds Send As on nobody")
        return 0
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        print(f"FAIL: agent user {user!r} does not exist")
        return 1
    credential = read_agent_credential(proc_root, uid)
    if credential is None:
        print("FAIL: seat authors staff_send_as but no agent process carries the msgraph read credential; cannot ask")
        return 1
    try:
        token = mint(
            credential["MSGRAPH_TENANT_ID"], credential["MSGRAPH_CLIENT_ID"], credential["MSGRAPH_CLIENT_SECRET"]
        )
    except Exception as exc:  # noqa: BLE001 - any mint failure is a fail-closed verdict
        print(f"FAIL: token mint failed: {type(exc).__name__}")
        return 1
    if not token:
        print("FAIL: token mint returned no access token")
        return 1
    roles = roles_of(token)
    held = [r for r in roles if r in _FORBIDDEN_ROLES]
    if held:
        print(f"FAIL: the agent's Graph app holds {', '.join(held)}; with Send As granted it could send as staff")
        return 1
    print(f"PASS: agent Graph app roles are {', '.join(sorted(roles)) or '(none)'}; no Mail.Send")
    return 0


def main(argv: list[str]) -> int:
    yaml_path = argv[1] if len(argv) > 1 else _DEFAULT_YAML
    user = argv[2] if len(argv) > 2 else _DEFAULT_USER
    return run(yaml_path, user)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
