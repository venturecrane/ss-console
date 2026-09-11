#!/usr/bin/env python3
"""Retire output-class spec(s) from a PROVING seat's vault object.

The inverse of ``publish-output-class-spec.py``, with the same seat guard and
the same reason for existing: repeatable runtime proofs. A "voice not
established" proof and an establishment negative test are only runnable when a
previously-installed spec can be removed and its absence observed (gone means
gone) — and no removal tool existed before this one (publish is merge-only,
the applier prunes what the document no longer declares).

Refuses any seat not authored ``seat.kind: proving`` — on a customer seat the
portal write IS the customer's approval (ADR 0083); retiring a client's spec
is a portal act, never a script act.

Usage::

    infisical run --env=prod --path=/ss -- \
        python3 operator/bin/retire-output-class-spec.py \
            --slug pilot-smokeball --class work_product        # one class
    ... --slug pilot-smokeball --all                           # every class

The applier converges the seat tree on the document: a class absent from the
document is pruned from ``$SMD_SPEC_DIR`` at its next pass. Verify absence on
the seat (the manifest), not here — this script only proves the vault object.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Sequence

_REPO = Path(__file__).resolve().parents[2]
_SLUG = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")
_BASENAME = "output-classes.json"


def _assert_proving_seat(slug: str) -> None:
    """Same guard, same rationale, as publish-output-class-spec.py."""
    path = _REPO / "operator" / "customers" / slug / "customer.yaml"
    if not path.exists():
        sys.exit(f"REFUSED: no customer.yaml for '{slug}' — unknown seat")
    text = path.read_text()
    block = re.search(r"^seat:\n((?:[ \t]+.*\n)+)", text, re.MULTILINE)
    kind = re.search(r"^\s+kind:\s*(\w+)", block.group(1), re.MULTILINE) if block else None
    if not kind or kind.group(1) != "proving":
        found = kind.group(1) if kind else "absent"
        sys.exit(
            f"REFUSED: '{slug}' is seat.kind: {found}, not proving.\n"
            "Retiring a customer seat's spec is a portal act (ADR 0083)."
        )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--slug", required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--class", dest="output_class", help="retire one class")
    group.add_argument("--all", action="store_true", help="retire every class")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not _SLUG.match(args.slug):
        sys.exit(f"REFUSED: slug '{args.slug}' outside the permitted charset")
    _assert_proving_seat(args.slug)

    key = f"vaults/{args.slug}/{_BASENAME}"

    import boto3  # late import, mirroring publish

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket = os.environ["R2_BUCKET_CONFIG"]

    try:
        existing = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception:  # noqa: BLE001 — a missing or unreadable object means there is nothing to retire; the script reports that state
        print(f"key    : {key}")
        print("state  : no existing object — nothing to retire")
        return 0

    classes = existing.get("classes") or {}
    before = sorted(classes)
    if args.all:
        classes = {}
    else:
        if args.output_class not in classes:
            print(f"key    : {key}")
            print(f"state  : class '{args.output_class}' not present (has {before}) — nothing to retire")
            return 0
        classes = {k: v for k, v in classes.items() if k != args.output_class}

    doc = {"schema_version": 1, "customer": args.slug, "classes": classes}
    print(f"seat   : {args.slug} (proving)")
    print(f"key    : {key}")
    print(f"before : {before}")
    print(f"after  : {sorted(classes)}")
    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc).encode(), ContentType="application/json")
    back = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    print(f"wrote  : classes now {sorted(back.get('classes') or {})}")
    print("NOTE   : verify absence ON THE SEAT (specs manifest) — the applier converges on its next pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
