#!/usr/bin/env python3
"""Mint the two inbox-scoped AgentMail keys for one seat (ss#2258).

WHY TWO KEYS. Until 2026-08 every seat shared ONE org-wide, all-permission
AgentMail key. It could send as any inbox on the account, to anyone, and it lived
in the agent process — which is how a rehearsal seat mailed a real client
principal on four days with no audit row. The fix splits the credential by
capability, so the half the agent can reach is incapable of transmitting:

  <slug>-nosend  -> the gateway (AGENTMAIL_API_KEY). Reads and drafts its own
                    inbox. NO message_send, NO draft_send: AgentMail itself
                    refuses a transmit, whatever code path attempts it.
  <slug>-send    -> the broker ONLY (AGENTMAIL_SEND_API_KEY). Materialized to a
                    0600 broker-owned file at boot and unset before the gateway
                    exists. Every send through it passes the broker's recipient
                    fence and leaves an audit row.

Both are scoped to the seat's own inbox by PATH
(``POST /v0/inboxes/{inbox_id}/api-keys``), so neither can touch another seat's
mailbox even if it leaks. Scope and permissions intersect at the vendor.

THE SECRET NEVER PRINTS. AgentMail returns the key value exactly once, at
creation, and it is unrecoverable afterwards. This writes that value straight to
a 0600 file and prints only non-secret metadata, so it never reaches a terminal
transcript or shell history. Hand the file to ``crane_secret_set`` with
``source=file`` (which deletes it after a successful write).

USAGE
    infisical run --env=prod --path=/ss -- \\
        python3 operator/bin/mint-agentmail-keys.py <slug> --out-dir /tmp/amkeys

    # then, per file, and never by pasting the value:
    crane_secret_set path=/ss env=prod name=AGENTMAIL_API_KEY \\
        source=file file=/tmp/amkeys/<slug>-nosend.key
    crane_secret_set path=/ss env=prod name=AGENTMAIL_SEND_API_KEY \\
        source=file file=/tmp/amkeys/<slug>-send.key

    --dry-run prints the exact requests without creating anything.

The CREATING credential is the existing org-wide key in AGENTMAIL_API_KEY (it
holds api_key_create). Retiring that key is Wave 2 — it is what kills the copies
already written to every seat's volume, and it must not happen until every seat
is verified running on scoped keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.agentmail.to/v0"

# The gateway's half. Reads and drafts only.
#
# No `thread_read`: it does not exist. The vendor documents `message_read` as
# "Also required to read threads", and a key granted a permission name AgentMail
# does not define would not fail loudly — it would simply lack thread access.
# Draft permissions are load-bearing, not incidental: the reply channel triggers
# on `create_draft` and the intake skill authors it, so a key that could only
# READ would silently break the Operator's mailbox.
NOSEND_PERMISSIONS = {
    "inbox_read": True,
    "message_read": True,
    "draft_read": True,
    "draft_create": True,
    "draft_update": True,
    "draft_delete": True,
}

# The broker's half. `message_read` because the reply endpoint references its
# source message; `inbox_read` for the listing check that makes a provisioning
# mistake fail closed. `draft_send` stays off — nothing calls it.
SEND_PERMISSIONS = {
    "inbox_read": True,
    "message_read": True,
    "message_send": True,
}


def _post(path: str, body: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise SystemExit(f"AgentMail POST {path} failed: HTTP {exc.code}\n{detail}") from exc


def _inbox_exists(inbox_id: str, api_key: str) -> bool:
    request = urllib.request.Request(
        API_BASE + "/inboxes",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=30) as response:
        listing = json.loads(response.read().decode())
    found = {
        str(entry.get("inbox_id", "")).lower() for entry in (listing.get("inboxes") or []) if isinstance(entry, dict)
    }
    return inbox_id.lower() in found


def mint(inbox_id: str, name: str, permissions: dict, out: Path, api_key: str) -> None:
    quoted = urllib.parse.quote(inbox_id, safe="")
    result = _post(
        f"/inboxes/{quoted}/api-keys",
        {"name": name, "permissions": permissions},
        api_key,
    )
    secret = result.get("api_key")
    if not isinstance(secret, str) or not secret:
        raise SystemExit(f"{name}: AgentMail returned no api_key value; nothing written")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(secret, encoding="utf-8")
    out.chmod(0o600)
    granted = sorted(k for k, v in (result.get("permissions") or {}).items() if v)
    # Everything below is deliberately non-secret. The value went to the file.
    print(f"  created  {name}")
    print(f"    api_key_id : {result.get('api_key_id')}")
    print(f"    inbox_id   : {result.get('inbox_id') or '(not echoed)'}")
    print(f"    granted    : {', '.join(granted) or '(none echoed)'}")
    print(f"    value -> {out}  (0600; never printed)")
    if "message_send" in granted and "nosend" in name:
        raise SystemExit(
            f"REFUSING TO CONTINUE: {name} came back with message_send. The whole "
            "point of this key is that it cannot transmit. Delete it at the vendor."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("slug", help="customer slug, e.g. pilot-smokeball")
    parser.add_argument(
        "--inbox",
        default=None,
        help="inbox address; defaults to <slug>@agentmail.to (the convention)",
    )
    parser.add_argument("--out-dir", default="/tmp/amkeys", help="where the 0600 key files land")
    parser.add_argument("--dry-run", action="store_true", help="print the requests, create nothing")
    args = parser.parse_args()

    inbox_id = args.inbox or f"{args.slug}@agentmail.to"
    out_dir = Path(args.out_dir)
    plan = [
        (f"{args.slug}-nosend", NOSEND_PERMISSIONS, out_dir / f"{args.slug}-nosend.key"),
        (f"{args.slug}-send", SEND_PERMISSIONS, out_dir / f"{args.slug}-send.key"),
    ]

    if args.dry_run:
        print(f"DRY RUN — would create 2 keys scoped to inbox {inbox_id!r}\n")
        for name, permissions, out in plan:
            print(f"  POST {API_BASE}/inboxes/{urllib.parse.quote(inbox_id, safe='')}/api-keys")
            print(f"    body: {json.dumps({'name': name, 'permissions': permissions})}")
            print(f"    -> {out}\n")
        return 0

    api_key = os.environ.get("AGENTMAIL_API_KEY")
    if not api_key:
        raise SystemExit("AGENTMAIL_API_KEY unset — run under `infisical run --env=prod --path=/ss`")

    # Fail before creating anything if the inbox is not there. A seat whose inbox
    # has not been provisioned yet would otherwise get two keys scoped to nothing.
    if not _inbox_exists(inbox_id, api_key):
        raise SystemExit(
            f"inbox {inbox_id!r} does not exist on this AgentMail account. "
            "Create the seat's inbox first (Captain-side, via the console or API); "
            "keys scoped to a non-existent inbox are useless."
        )

    print(f"Minting 2 inbox-scoped keys for {inbox_id}\n")
    for name, permissions, out in plan:
        mint(inbox_id, name, permissions, out, api_key)
    print(
        "\nNext: store each with crane_secret_set (source=file, which deletes the "
        "file after a successful write). Do NOT paste the values."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
