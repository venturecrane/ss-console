"""Purge one firm's accepted library files and their ledger from its vault.

    python -m spec_applier.library_purge purge --slug <slug> --dry-run
    python -m spec_applier.library_purge purge --slug <slug>

Removes every object under ``vaults/<slug>/library/``: the ledger first (so the
seat's next applier tick prunes the installed ledger and every copy, the "gone
means gone" path of ``spec_applier/library.py``), then its previous generation
and the copies. ``--dry-run`` lists exactly the keys the real run deletes, in
the order it deletes them, and deletes nothing. After a real run the prefix is
listed again and the command fails if anything is left.

R2 from the same environment the applier reads (``R2_ENDPOINT_URL``,
``R2_ACCESS_KEY_ID``, ``R2_SECRET_ACCESS_KEY``, ``R2_BUCKET_CONFIG``). Proving
the seat side is a separate probe: ``<SMD_SPEC_DIR>/library/`` absent and
``manifest.json`` naming no ``library_*`` key.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any

from spec_applier.applier import SpecApplyError
from spec_applier.library import ledger_key, library_prefix


def _list(s3_client: Any, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            page = s3_client.list_objects_v2(**kwargs)
        except Exception as exc:  # noqa: BLE001 (a list fault stops the purge before any delete)
            raise SpecApplyError(
                f"library_purge: could not list s3://{bucket}/{prefix}: {exc}"
            ) from exc
        for entry in page.get("Contents") or []:
            if isinstance(entry, dict) and isinstance(entry.get("Key"), str):
                keys.append(entry["Key"])
        token = page.get("NextContinuationToken") if page.get("IsTruncated") else None
        if not token:
            return keys


def plan(s3_client: Any, bucket: str, slug: str) -> list[str]:
    """The keys a purge of ``slug`` deletes, in order: the ledger, then the rest."""
    keys = sorted(set(_list(s3_client, bucket, library_prefix(slug))))
    first = ledger_key(slug)
    return ([first] if first in keys else []) + [k for k in keys if k != first]


def purge(s3_client: Any, bucket: str, slug: str, *, dry_run: bool) -> list[str]:
    """Delete (or, dry, only list) every library object of ``slug``; returns the keys."""
    keys = plan(s3_client, bucket, slug)
    if dry_run:
        return keys
    for key in keys:
        s3_client.delete_object(Bucket=bucket, Key=key)
    left = _list(s3_client, bucket, library_prefix(slug))
    if left:
        raise SpecApplyError(
            f"library_purge: {len(left)} object(s) remain under {library_prefix(slug)}"
        )
    return keys


def _client() -> tuple[Any, str]:
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )
    return client, os.environ["R2_BUCKET_CONFIG"]


def main(argv: Sequence[str] | None = None, client: tuple[Any, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m spec_applier.library_purge")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser(
        "purge", help="remove a firm's ledger and accepted-file copies from its vault"
    )
    cmd.add_argument("--slug", required=True)
    cmd.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    s3_client, bucket = client if client is not None else _client()
    try:
        keys = purge(s3_client, bucket, args.slug, dry_run=args.dry_run)
    except SpecApplyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    verb = "would remove" if args.dry_run else "removed"
    for key in keys:
        print(f"{verb} {key}")
    print(f"{verb} {len(keys)} object(s) under {library_prefix(args.slug)}")
    if not args.dry_run:
        print("the seat prunes its installed ledger and copies on the applier's next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
