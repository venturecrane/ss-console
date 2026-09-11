#!/usr/bin/env python3
"""Publish an output-class spec body to a customer's vault object.

WHAT THIS IS FOR, and what it must never be used for
-----------------------------------------------------
The portal is the AUTHORING surface for output-class specs, and on a customer
seat the portal write IS the customer's approval — ADR 0083 requires a customer
to approve a spec before it applies, and a write that skips the portal skips the
approval. So this script exists for exactly one case: installing a FIXTURE on a
PROVING seat, where the "firm" is fictional and there is no approval to respect.

It refuses a seat whose `customer.yaml` does not declare `seat.kind: proving`.
That refusal is the point. A convenience script that can reach a client seat is
not a convenience, it is a way to put an unapproved spec in front of a real
firm's clients, and the fact that nobody INTENDS to do that is not a control.

WHY IT EXISTS AT ALL
--------------------
The vault object is not portal-exclusive — it only looked that way. The git
publisher (`scripts/ci-publish-customer-configs.sh`) is structurally barred from
this key, which stops IT clobbering the portal's object, and that one-way bar was
mistaken for a two-way one during the 2026-08-01 build. Anything holding the R2
credentials can write here. Naming that plainly, in a script with a seat guard,
is better than leaving it as folklore that the key is unreachable.

Usage::

    infisical run --env=prod --path=/ss -- \\
        python3 operator/bin/publish-output-class-spec.py \\
            --slug pilot-smokeball \\
            --class work_product --property voice \\
            --body operator/customers/pilot-smokeball/seed/voice/spec/work_product.voice.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Sequence

_REPO = Path(__file__).resolve().parents[2]

#: Mirrors the console writer's key shape exactly. One basename, one charset.
_SLUG = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")
_BASENAME = "output-classes.json"

#: Mirrors the applier's vocabulary. A property outside this set installs nowhere.
_PROPERTIES = ("voice", "format")

#: Console + applier ceiling. Restated so this script refuses what they would.
_MAX_BODY_BYTES = 256 * 1024


def _assert_proving_seat(slug: str) -> None:
    """Refuse any seat not authored `seat.kind: proving`.

    Read from git rather than from the projection: git is where the seat
    descriptor is authoritative, and a projection can lag a change that just
    demoted a seat out of proving.
    """
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
            "On a customer seat the portal write IS the customer's approval "
            "(ADR 0083). Use the portal."
        )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--class", dest="output_class", required=True)
    ap.add_argument("--property", default="voice", choices=_PROPERTIES)
    ap.add_argument("--body", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not _SLUG.match(args.slug):
        sys.exit(f"REFUSED: slug '{args.slug}' outside the permitted charset")
    _assert_proving_seat(args.slug)

    # LF-only, normalised BEFORE hashing — the console does this because a
    # browser textarea submits CRLF, the digest would match its own CRLF body,
    # and the seat would hold a file whose every line carries a trailing \r.
    body = args.body.read_text().replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip():
        sys.exit("REFUSED: empty body")
    encoded = body.encode()
    if len(encoded) > _MAX_BODY_BYTES:
        sys.exit(f"REFUSED: {len(encoded)} bytes exceeds the {_MAX_BODY_BYTES}-byte ceiling")

    key = f"vaults/{args.slug}/{_BASENAME}"
    doc = {
        "schema_version": 1,
        "customer": args.slug,
        "classes": {args.output_class: {args.property: {"body": body, "sha256": hashlib.sha256(encoded).hexdigest()}}},
    }
    raw = json.dumps(doc).encode()

    print(f"seat   : {args.slug} (proving)")
    print(f"key    : {key}")
    print(f"class  : {args.output_class}.{args.property}")
    print(f"body   : {len(encoded)} bytes, sha256 {doc['classes'][args.output_class][args.property]['sha256'][:16]}…")
    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    import boto3  # imported late so --dry-run works without it

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket = os.environ["R2_BUCKET_CONFIG"]

    # MERGE, never clobber. Another class's spec may already be in this object,
    # and a blind put would silently delete it — the applier prunes anything the
    # document no longer declares, so the loss would reach the seat.
    try:
        existing = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        merged = existing.get("classes") or {}
        merged.setdefault(args.output_class, {})[args.property] = doc["classes"][args.output_class][args.property]
        doc["classes"] = merged
        raw = json.dumps(doc).encode()
        print(f"merged : into an existing object; classes now {sorted(merged)}")
    except Exception:  # noqa: BLE001 — absent object is the ordinary case
        print("merged : no existing object; creating")

    s3.put_object(Bucket=bucket, Key=key, Body=raw, ContentType="application/json")
    back = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    inner = json.loads(back)["classes"][args.output_class][args.property]
    ok = back == raw and inner["sha256"] == hashlib.sha256(inner["body"].encode()).hexdigest()
    print(f"\nwrote  : {len(raw)} bytes | round-trip identical and hash agrees: {ok}")
    print("\nThe applier polls this key. Confirm the install on the seat with:")
    print(f"  operator/bin/seat-probe.sh {args.slug} sh -c 'ls -laR $SMD_SPEC_DIR'")
    return 0 if ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
