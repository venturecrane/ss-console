#!/usr/bin/env python3
"""Hydrate the staging tenant with the Settlement Closeout proving matters (ss #2455).

    cd operator/customers/pilot-smokeball/seed
    infisical run --env=prod --path=/ss -- python3 seed_closeout.py --dry-run
    infisical run --env=prod --path=/ss -- python3 seed_closeout.py

Reads the SAME authored source the keying sheet and the offline fixtures come
from (``operator/fixtures/law-firm/pi/lien-ledger-tracker/seed/closeout-seed.yaml``),
so what lands in the tenant, what a person is asked to key by hand, and what the
tests assert cannot drift apart.

WHAT THIS CREATES, AND WHAT IT CANNOT
-------------------------------------
Creates: the client contacts, and the matters at the trigger status with their
numbers, titles, opened dates and matter types.

Does NOT create: the Medicals and Settlement Details provider rows. Those are
layout values, and ``layouts/write`` is in neither app's grant - not the seat's
connector app, and not this seeding app, which is far broader and holds
firm/write, invoices/write and roles/write (vfy_01M0EQ6020PAF48S59BBAHRC5W).
Two independent identities lacking it is the evidence that the vendor catalog
has no settlement-detail write surface at all. The provider rows are keyed by
hand, and the keying sheet covers only them.

Idempotent via ``closeout-manifest.json`` beside this script: every created
resource is recorded under a stable key and skipped on re-run. Delete a key
(or the file) to re-create. Deliberately a SEPARATE manifest from the wedge
seed's, so re-running either cannot disturb the other's resources.

Seeding is test-infrastructure hydration on OUR OWN staging tenant, distinct
from delivery writes on a client account (the standing gate; Captain 2026-07-04).
This script has no path to any tenant but staging: the host is a module constant
inherited from seed_staging.
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SEED_YAML = os.path.join(
    REPO_ROOT,
    "operator/fixtures/law-firm/pi/lien-ledger-tracker/seed/closeout-seed.yaml",
)
MANIFEST = os.path.join(HERE, "closeout-manifest.json")

sys.path.insert(0, HERE)
from seed_staging import Api  # noqa: E402 - module-local helper in the same directory, after the sys.path shim above
from seed_data import MVA_PLAINTIFF_CA, PI_PLAINTIFF_CA  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = {}
    data.setdefault("contacts", {})
    data.setdefault("matters", {})
    return data


def save_manifest(data: dict) -> None:
    with open(MANIFEST, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def matter_type_for(title: str) -> str:
    """Motor Vehicle Accident vs Personal Injury, from the authored title."""
    return MVA_PLAINTIFF_CA if "Motor Vehicle" in title else PI_PLAINTIFF_CA


def as_datetime(day: str) -> str:
    """The API takes an ISO datetime; the seed authors plain dates."""
    return f"{day}T17:00:00Z"


def contact_key(name: str) -> str:
    return "sct-" + name.lower().replace(" ", "-").replace(".", "")


def person_body(name: str) -> dict:
    parts = name.split()
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else parts[0]
    return {"person": {"firstName": first, "lastName": last}}


def main() -> None:
    dry = "--dry-run" in sys.argv
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML required: python3 -m pip install pyyaml")

    with open(SEED_YAML, encoding="utf-8") as handle:
        seed = yaml.safe_load(handle)

    matters = seed["matters"]
    print(f"authored source: {os.path.relpath(SEED_YAML, REPO_ROOT)}")
    print(f"matters to seed: {len(matters)}  (status {matters[0]['status']})")
    if dry:
        print("\nDRY RUN - nothing will be created.\n")

    manifest = load_manifest()
    manifest["created_at"] = manifest.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["source"] = os.path.relpath(SEED_YAML, REPO_ROOT)

    api = None if dry else Api()

    # 1. client contacts, one per distinct authored client name
    wanted: dict[str, str] = {}
    for matter in matters:
        for client in matter["clients"]:
            wanted[contact_key(client)] = client

    for key, name in sorted(wanted.items()):
        if key in manifest["contacts"]:
            print(f"contact {key}: exists ({manifest['contacts'][key]})")
            continue
        if dry:
            print(f"contact {key}: WOULD CREATE  {person_body(name)}")
            continue
        resource = api.create_async(
            "/contacts",
            {**person_body(name), "externalSystemId": f"seed-{key}"},
            f"contact {key}",
        )
        manifest["contacts"][key] = resource["id"]
        save_manifest(manifest)
        print(f"contact {key}: created {resource['id']}")

    # 2. matters, at the authored trigger status
    for matter in matters:
        key = matter["seed_id"]
        if key in manifest["matters"]:
            print(f"matter {key}: exists ({manifest['matters'][key]})")
            continue
        body = {
            "matterTypeId": matter_type_for(matter["title"]),
            "clientIds": [manifest["contacts"].get(contact_key(c)) for c in matter["clients"]],
            "status": matter["status"],
            "number": matter["number"],
            "description": "[SEED] " + matter["title"],
            "openedDate": as_datetime(str(matter["opened"])),
        }
        if matter.get("closed"):
            body["closedDate"] = as_datetime(str(matter["closed"]))
        if dry:
            print(
                f"matter {key} ({matter['number']}): WOULD CREATE  status={body['status']} "
                f"type={'MVA' if body['matterTypeId'] == MVA_PLAINTIFF_CA else 'PI'} "
                f"clients={len(matter['clients'])} closed={'yes' if matter.get('closed') else 'no'}"
            )
            continue
        resource = api.create_async("/matters", body, f"matter {key}")
        manifest["matters"][key] = resource["id"]
        save_manifest(manifest)
        print(f"matter {key}: created {resource['id']} (number {matter['number']})")

    if dry:
        print("\nDRY RUN complete - nothing created.")
        return

    save_manifest(manifest)
    print(f"\nmanifest: {os.path.relpath(MANIFEST, REPO_ROOT)}")
    print("\nSTILL NEEDED BY HAND: the Medicals and Settlement Details provider rows.")
    print("layouts/write is in no app's grant; see the keying sheet for those rows only.")


if __name__ == "__main__":
    main()
