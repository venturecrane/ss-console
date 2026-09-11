#!/usr/bin/env python3
"""Point a staging matter's client contact at an authored harness mailbox, via App 1.

WHY THIS EXISTS (2026-08-18, ss#2389/ss#2167). The cross-matter kill test needs a
recipient that is BOTH a party on a tenant matter (so ADR 0086's (matter,
recipient) pair can seed from get_roles_on_matter) AND an authored client-class
address on the seat's outbound roster (so the outbound-authorization gate is held
constant and the scenario isolates the matter gate). The seeded RFC 2606 party
addresses satisfy neither the roster nor deliverability; the roster's client
stand-in (ap-client-standin@agentmail.to) satisfies everything once it is the
client contact's email on the matter.

WHY NOT THE OPERATOR. The connector exposes contact READS only. This is our
tooling editing our own rehearsal tenant with the same App 1 that seeded it —
deliberately a different hand than the agent's.

STAGING ONLY. Refuses unless the resolved host is the staging API.

USAGE
    cd operator/customers/pilot-smokeball/seed
    infisical run --env=prod --path=/ss -- python3 set_client_contact_email.py \
        --matter-number 2026-PI-101 --email ap-client-standin@agentmail.to [--apply]

Dry-run by default: prints the contact it found and the change it would make.
"""

from __future__ import annotations

import argparse
import json
import sys

from seed_staging import API_HOST, Api


def _first(payload, *keys):
    for key in keys:
        value = payload.get(key) if isinstance(payload, dict) else None
        if value:
            return value
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matter-number", required=True)
    ap.add_argument("--email", required=True, help="the authored harness address to set")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    args = ap.parse_args()

    if "staging" not in API_HOST:
        sys.exit(f"refusing: API_HOST is {API_HOST!r}, which is not the staging tenant")

    api = Api()

    code, matters = api.call("GET", "/matters?limit=100")
    if code != 200:
        sys.exit(f"could not list matters: HTTP {code}")
    matter = next(
        (m for m in _first(matters, "value", "items") if isinstance(m, dict) and m.get("number") == args.matter_number),
        None,
    )
    if matter is None:
        sys.exit(f"matter {args.matter_number} not found in staging tenant")
    matter_id = str(matter["id"])
    print(f"matter {args.matter_number} = {matter_id}")

    client_ids = [str(c) for c in (matter.get("clientIds") or [])]
    if not client_ids:
        code, full = api.call("GET", f"/matters/{matter_id}")
        client_ids = [str(c) for c in ((full or {}).get("clientIds") or [])] if code == 200 else []
    if not client_ids:
        sys.exit("matter carries no clientIds; nothing to point at the harness mailbox")

    contact_id = client_ids[0]
    code, contact = api.call("GET", f"/contacts/{contact_id}")
    if code != 200 or not isinstance(contact, dict):
        sys.exit(f"could not read contact {contact_id}: HTTP {code}")

    person = contact.get("person") if isinstance(contact.get("person"), dict) else contact
    before = person.get("email")
    print("CONTACT:")
    print(
        json.dumps(
            {
                "id": contact_id,
                "firstName": person.get("firstName"),
                "lastName": person.get("lastName"),
                "email (before)": before,
                "email (after)": args.email,
            },
            indent=2,
        )
    )

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to write.")
        return

    person["email"] = args.email
    code, resp = api.call("PUT", f"/contacts/{contact_id}", contact)
    print(f"\nPUT /contacts/{contact_id} -> HTTP {code} {json.dumps(resp)[:200] if resp else ''}")

    # Read it back; a write this venture has not verified is a write it does not have.
    code, after = api.call("GET", f"/contacts/{contact_id}")
    person_after = (after or {}).get("person") if isinstance((after or {}).get("person"), dict) else after
    print(f"VERIFY: email now = {person_after.get('email') if isinstance(person_after, dict) else '?'}")


if __name__ == "__main__":
    main()
