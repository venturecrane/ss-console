#!/usr/bin/env python3
"""Write customer-owned operator identity facts into each Hermes SOUL.md.

The overlay-generated SOUL.md carries persona name, title, vertical, and tone.
For a customer Machine that is not enough: the agent also needs the authored
customer-owned account it acts through, and which connector path is authoritative.

This guard appends an idempotent managed block derived from customer.yaml after
``hermes-smd bootstrap`` writes profiles.

Usage:
  ensure-operator-identity.py CUSTOMER_YAML [HERMES_HOME]
  (HERMES_HOME defaults to $HERMES_HOME or /opt/data.)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml


START = "<!-- smd-operator-identity:start -->"
END = "<!-- smd-operator-identity:end -->"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"FATAL: {path} is not a YAML mapping")
    return data


def _active_personas(data: dict[str, Any]) -> list[dict[str, Any]]:
    personas = data.get("personas") or []
    if not isinstance(personas, list):
        return []
    active = [p for p in personas if isinstance(p, dict) and p.get("status") == "active"]
    return active or [p for p in personas if isinstance(p, dict)]


def _managed_block(data: dict[str, Any]) -> str:
    google_auth = data.get("google_auth") or {}
    subject = ""
    scopes: list[str] = []
    managed: list[str] = []
    if isinstance(google_auth, dict) and google_auth.get("mode") == "dwd":
        subject = str(google_auth.get("subject") or "").strip()
        scopes = [str(s).strip() for s in (google_auth.get("scopes") or []) if str(s).strip()]
        for mailbox in google_auth.get("managed_mailboxes") or []:
            if isinstance(mailbox, dict) and str(mailbox.get("address") or "").strip():
                managed.append(str(mailbox["address"]).strip())

    # Google Workspace capability is authored by google_auth (NOT a connector entry):
    # Gmail/Calendar/Drive are served by the governed, broker-mediated workspace_*
    # tools. Derive which capabilities are live from the authored scopes.
    def _has(token: str) -> bool:
        return any(token in scope for scope in scopes)

    caps: list[str] = []
    if _has("gmail"):
        caps.append("Gmail")
    if _has("calendar"):
        caps.append("Calendar")
    if _has("drive") or _has("documents") or _has("spreadsheets"):
        caps.append("Drive, Docs, and Sheets")

    lines = [
        START,
        "## Operator Identity",
        "",
    ]
    if subject:
        lines.append(f"- Your customer-owned Google Workspace email address is {subject}.")
        lines.append(
            "- You can read Gmail and create review drafts as that Workspace user within your "
            "action ceilings. Wave A does not provide a send tool."
        )
    if managed:
        lines.append(
            "- You also manage these mailboxes on the principal's behalf (executive-assistant "
            f"access): {', '.join(managed)}."
        )
        lines.append(
            "- To act on an authored managed mailbox, pass `mailbox=<address>` to each "
            "`workspace_gmail_*` call. Omit `mailbox` for Crane's own mailbox."
        )
    if caps:
        lines.append(
            f"- Your Google Workspace capabilities ({', '.join(caps)}) are served by the governed "
            "workspace_* tools (e.g. workspace_gmail_search, workspace_calendar_list, "
            "workspace_drive_list), mediated by the Workspace broker."
        )
    if subject or caps:
        lines.extend(
            [
                "- Reach Google ONLY through the workspace_* tools. There is no Google credential on "
                "your filesystem and no connector CLI to shell out to — never use terminal or "
                "execute_code to read or send Gmail / Calendar / Drive.",
                "- Do not infer mail is unconfigured from absent local mail clients, IMAP/SMTP config, or Himalaya.",
                "- AgentMail addresses are not your primary customer Workspace identity unless customer.yaml binds them as Email.",
            ]
        )
    lines.append(END)
    return "\n".join(lines) + "\n"


def _replace_managed_block(text: str, block: str) -> str:
    if START in text and END in text:
        before = text.split(START, 1)[0].rstrip()
        after = text.split(END, 1)[1].lstrip()
        return f"{before}\n\n{block}{after}".rstrip() + "\n"
    return text.rstrip() + "\n\n" + block


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Append customer-owned operator identity facts to SOUL.md.")
    parser.add_argument("customer_yaml", help="path to customer.yaml")
    parser.add_argument(
        "hermes_home",
        nargs="?",
        default=os.environ.get("HERMES_HOME", "/opt/data"),
        help="Hermes home dir (default: $HERMES_HOME or /opt/data)",
    )
    args = parser.parse_args(argv[1:])

    tag = "ensure-operator-identity"
    data = _load_yaml(Path(args.customer_yaml))
    block = _managed_block(data)
    home = Path(args.hermes_home)

    changed = 0
    for persona in _active_personas(data):
        slug = str(persona.get("slug") or "").strip()
        if not slug:
            continue
        soul_path = home / "profiles" / slug / "SOUL.md"
        if not soul_path.exists():
            print(f"[{tag}] SOUL.md not found for persona {slug}: {soul_path}", file=sys.stderr)
            continue
        current = soul_path.read_text(encoding="utf-8")
        updated = _replace_managed_block(current, block)
        if updated != current:
            soul_path.write_text(updated, encoding="utf-8")
            changed += 1
            print(f"[{tag}] updated {soul_path}")
        else:
            print(f"[{tag}] already current: {soul_path}")

    print(f"[{tag}] done; {changed} SOUL.md file(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
