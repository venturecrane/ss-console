#!/usr/bin/env python3
"""Read the Settlement Closeout seed back and report what actually landed (ss #2455).

    cd operator/customers/pilot-smokeball/seed
    infisical run --env=prod --path=/ss -- python3 verify_closeout.py

Exists because a write the API accepts and silently does not apply looks
identical to success at the call site. This asserts the fields that the closeout
gate actually keys off, one at a time, against the authored source:

  * the matter exists at all
  * its status is the authored trigger status, not whatever the API defaulted to
  * its client count matches (the multi-plaintiff matter is the one that matters)
  * its opened date, and whether a closed date is present or absent as authored

Read-only. Exit 1 on any mismatch, so it can gate a later step.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SEED_YAML = os.path.join(
    REPO_ROOT,
    "operator/fixtures/law-firm/pi/lien-ledger-tracker/seed/closeout-seed.yaml",
)

sys.path.insert(0, HERE)
from seed_staging import Api  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


def listing(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "value", "results", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def main() -> int:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML required")
    with open(SEED_YAML, encoding="utf-8") as handle:
        seed = yaml.safe_load(handle)
    authored = {m["number"]: m for m in seed["matters"]}
    trigger = seed["matters"][0]["status"]

    api = Api()
    code, payload = api.call("GET", f"/matters?Status={trigger}&Limit=200")
    if code != 200:
        print(f"GET /matters?Status={trigger} -> {code}")
        return 1
    rows = {str(r.get("number")): r for r in listing(payload) if isinstance(r, dict)}
    print(f"matters at status {trigger} in the tenant: {len(rows)}")

    problems: list[str] = []
    print(f"\n{'matter':<14}{'status':<10}{'clients':<9}{'opened':<12}{'closed':<12}verdict")
    for number in sorted(authored):
        want = authored[number]
        got = rows.get(number)
        if got is None:
            print(f"{number:<14}{'-':<10}{'-':<9}{'-':<12}{'-':<12}MISSING")
            problems.append(f"{number}: not present at status {trigger}")
            continue
        clients = got.get("clients") or []
        opened = str(got.get("openedDate") or "")[:10]
        closed = str(got.get("closedDate") or "")[:10]
        issues = []
        if got.get("status") != want["status"]:
            issues.append(f"status {got.get('status')} != {want['status']}")
        if len(clients) != len(want["clients"]):
            issues.append(f"clients {len(clients)} != {len(want['clients'])}")
        if opened != str(want["opened"]):
            issues.append(f"opened {opened} != {want['opened']}")
        want_closed = str(want["closed"])[:10] if want.get("closed") else ""
        if closed != want_closed:
            issues.append(f"closed {closed or '(blank)'} != {want_closed or '(blank)'}")
        verdict = "OK" if not issues else "; ".join(issues)
        problems.extend(f"{number}: {i}" for i in issues)
        print(f"{number:<14}{str(got.get('status')):<10}{len(clients):<9}{opened:<12}{closed or '-':<12}{verdict}")

    print("\nNOT CHECKED HERE: the Medicals and Settlement Details provider rows.")
    print("They are layout values, no app holds layouts/write, and they are keyed by hand.")
    print("The gate's own read-back reconciliation covers them once they are in.")

    if problems:
        print(f"\n{len(problems)} MISMATCH(ES):")
        for p in problems:
            print("  -", p)
        return 1
    print(f"\nAll {len(authored)} seeded matters match the authored source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
