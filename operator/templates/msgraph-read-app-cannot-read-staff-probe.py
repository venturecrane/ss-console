#!/usr/bin/env python3
"""Boot-smoke proof that the agent's Graph credential cannot read a rostered
staff member's mailbox, on a seat that may send AS staff (ADR 0089).

WHY. The approval email carries approve buttons, and a button is a signed
one-decision link (amendment 5a). The link is a key, so the question is who can
hold it. The broker sends that email with no Sent Items copy, precisely so the
only mailbox holding the link is the staff member's own. That control is worth
exactly as much as the answer to this question: can the agent read THAT mailbox?
An app-only Graph registration reads every mailbox in the tenant unless the
tenant scopes it with an ApplicationAccessPolicy. Where the policy is missing,
the Operator can read its approver's inbox, lift its own approve link, and press
its own button. No plugin fence can prevent that; only the tenant can.

WHAT THIS DOES. Runs as ROOT under boot-smoke. Reads the agent-uid gateway's
environ for the msgraph read credential, mints an app-only token, and asks Graph
for the INBOX METADATA (id only, never a message, never a body) of EVERY
address in ``scope.staff_send_as``, in authored order. Each approver must answer
403 or 404: the policy is scoping this app away from that inbox. The first
approver answering anything else is the failure, and it names the mailbox. Every
approver, not the first: the scope group may lawfully hold named staff
mailboxes (``staff_mailbox_reads``), so one refused approver says nothing about
the next.

WHAT MAKES IT ABLE TO FAIL. Remove the ApplicationAccessPolicy for the read app
in the tenant, or add any approver to the read app's scope group, and this
exits 1. A seat that authors staff_send_as and carries no
msgraph credential exits 1, because "could not ask" must never read as "cannot
read". A failed token mint exits 1, for the same reason.

VACUOUS PASS, STATED. A seat authoring no staff_send_as passes with a line
saying so: it mints no approve links, because it proposes no staff sends.

Usage:  msgraph-read-app-cannot-read-staff-probe.py [customer-yaml] [agent-username]
"""

from __future__ import annotations

import json
import os
import pwd
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

_DEFAULT_YAML = "/var/lib/smd-config/customer.yaml"
_DEFAULT_USER = "hermes"
_VARS = ("MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET")

Mint = Callable[[str, str, str], str]
Ask = Callable[[str, str], int]


def staff_addresses(yaml_text: str) -> list[str]:
    """Every address in ``scope.staff_send_as``. Parsed, not grepped."""
    import yaml

    data = yaml.safe_load(yaml_text) or {}
    scope = data.get("scope") if isinstance(data, dict) else None
    entries = scope.get("staff_send_as") if isinstance(scope, dict) else None
    if not isinstance(entries, list):
        return []
    return [str(e["address"]).strip() for e in entries if isinstance(e, dict) and str(e.get("address") or "").strip()]


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
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed https login host; tenant id is url-quoted
        return str(json.loads(response.read().decode("utf-8")).get("access_token") or "")


def _urllib_ask(token: str, mailbox: str) -> int:
    """The HTTP status of an inbox METADATA read. Never a message, never a body."""
    url = (
        "https://graph.microsoft.com/v1.0/users/"
        + urllib.parse.quote(mailbox, safe="")
        + "/mailFolders/inbox?$select=id"
    )
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310 - fixed https graph host; mailbox is url-quoted
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed https graph host; mailbox is url-quoted
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def run(
    yaml_path: str,
    user: str,
    proc_root: str = "/proc",
    mint: Mint = _urllib_mint,
    ask: Ask = _urllib_ask,
) -> int:
    try:
        yaml_text = open(yaml_path, encoding="utf-8").read()
    except OSError as exc:
        print(f"FAIL: cannot read {yaml_path}: {exc}")
        return 1
    staff = staff_addresses(yaml_text)
    if not staff:
        print("vacuous: seat authors no scope.staff_send_as; it mints no approve links")
        return 0
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        print(f"FAIL: no such user {user}")
        return 1
    credential = read_agent_credential(proc_root, uid)
    if credential is None:
        print("FAIL: seat authors staff_send_as but no agent process carries the msgraph read credential; cannot ask")
        return 1
    try:
        token = mint(
            credential["MSGRAPH_TENANT_ID"],
            credential["MSGRAPH_CLIENT_ID"],
            credential["MSGRAPH_CLIENT_SECRET"],
        )
    except Exception as exc:  # noqa: BLE001 - could not ask is a failure, not a pass
        print(f"FAIL: could not mint an app-only token to ask: {type(exc).__name__}")
        return 1
    if not token:
        print("FAIL: the tenant returned no token; cannot ask")
        return 1
    answers: list[str] = []
    for mailbox in staff:
        status = ask(token, mailbox)
        if status not in (403, 404):
            print(
                f"FAIL: the read app can read {mailbox}'s mailbox (HTTP {status}), and "
                f"{mailbox} approves drafts sent in their name. The read app's "
                "ApplicationAccessPolicy scope group may hold the Operator's own mailbox and "
                "the staff mailboxes the firm authored under staff_mailbox_reads, never a "
                "person on scope.staff_send_as: their inbox holds the approve links, and "
                "the agent could lift its own link and press it. Take this approver out of "
                "the scope group."
            )
            return 1
        answers.append(f"{mailbox} HTTP {status}")
    print(f"pass: the read app is refused every approver's mailbox ({'; '.join(answers)})")
    return 0


def main(argv: list[str]) -> int:
    yaml_path = argv[1] if len(argv) > 1 else _DEFAULT_YAML
    user = argv[2] if len(argv) > 2 else _DEFAULT_USER
    return run(yaml_path, user)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
