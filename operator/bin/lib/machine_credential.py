"""Mint or rotate a seat's per-tenant Machine credential (migration 0114).

The console verifies a Machine's writes (`POST /api/internal/heartbeat`,
`/runtime-summary`, `/sentry-probe`) against `machine_credentials.key_hash` =
HMAC-SHA256(salt, plaintext), one row per seat, so a seat's key is only ever
valid for its own slug. Before this (ADR 0023 Wave 1) every seat held one
shared key and the tenant was a header any seat could set.

This module is the ONLY writer of those hashes. `src/lib/auth/machine-key.ts`
is the only reader, and the two must agree byte for byte:

    key_hash = hex(HMAC_SHA256(key = bytes.fromhex(salt), message = plaintext.encode()))

`tests/machine-credentials.test.ts` runs this file to mint a row into a real
D1 and then verifies with the TypeScript verifier, so a drift on either side
turns that test red.

Output discipline (ss#2218): the plaintext is written to STDOUT ONLY, never to
argv, never to the SQL file, never to a log. The SQL goes to ``--sql-out``.
The caller captures stdout into a shell variable and stages it as the seat's
MACHINE_HEARTBEAT_KEY Fly secret; the variable is never echoed.

The SQL is one idempotent upsert:

  * first run for a slug  -> INSERT (entity_id comes from customer_configs, so a
    slug the console has not projected yet inserts NOTHING; the caller must
    check the row count and refuse to stage a key the Worker cannot verify);
  * later runs (rotate)   -> the current credential becomes ``prev_*`` with
    ``prev_expires_at`` = now + ``--prev-ttl-hours`` (default 24) and the new
    one becomes current. Both verify until the previous expires, so the D1
    write can land before the Fly secret and the seat never 401s mid-rotation.

Run::

    python3 operator/bin/lib/machine_credential.py --slug <slug> --sql-out /tmp/x.sql
    cd operator && python3 -m pytest bin/tests/test_machine_credential.py -q
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import re
import secrets
import sys
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
KEY_BYTES = 32
SALT_BYTES = 16
DEFAULT_PREV_TTL_HOURS = 24


def key_hash_hex(plaintext: str, salt_hex: str) -> str:
    """HMAC-SHA256(key = salt bytes, message = plaintext utf-8), lowercase hex."""
    return hmac.new(bytes.fromhex(salt_hex), plaintext.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(plaintext: str, salt_hex: str, expected_hash_hex: str) -> bool:
    """Constant-time check; the Python twin of the verifier's ``hmacMatches``."""
    return hmac.compare_digest(key_hash_hex(plaintext, salt_hex), expected_hash_hex)


def mint(slug: str, *, prev_ttl_hours: int = DEFAULT_PREV_TTL_HOURS) -> tuple[str, str]:
    """Return ``(plaintext, sql)``. ``sql`` never contains the plaintext."""
    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid slug {slug!r} (must match {SLUG_RE.pattern})")
    if prev_ttl_hours < 1:
        raise ValueError("prev_ttl_hours must be >= 1")
    plaintext = secrets.token_hex(KEY_BYTES)
    salt_hex = secrets.token_hex(SALT_BYTES)
    digest = key_hash_hex(plaintext, salt_hex)
    sql = upsert_sql(slug, digest, salt_hex, prev_ttl_hours)
    assert plaintext not in sql
    return plaintext, sql


def upsert_sql(slug: str, digest: str, salt_hex: str, prev_ttl_hours: int) -> str:
    # slug is validated against SLUG_RE (lowercase alphanumerics and dashes),
    # digest and salt are hex from this module, and the TTL is an int: nothing
    # inlined here can carry a quote. The console-side sql_text() helper is the
    # right tool for untrusted text; these values are not that.
    return (
        "INSERT INTO machine_credentials (customer_slug, entity_id, key_hash, salt)\n"
        f"SELECT customer_slug, entity_id, '{digest}', '{salt_hex}'\n"
        "  FROM customer_configs\n"
        f" WHERE customer_slug = '{slug}'\n"
        "    ON CONFLICT(customer_slug) DO UPDATE SET\n"
        "      prev_key_hash   = machine_credentials.key_hash,\n"
        "      prev_salt       = machine_credentials.salt,\n"
        f"      prev_expires_at = datetime('now', '+{int(prev_ttl_hours)} hours'),\n"
        "      key_hash        = excluded.key_hash,\n"
        "      salt            = excluded.salt,\n"
        "      rotated_at      = datetime('now');\n"
    )


def count_sql(slug: str) -> str:
    """The row-count probe the caller runs after the upsert (0 = not projected)."""
    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid slug {slug!r}")
    return f"SELECT COUNT(*) AS n FROM machine_credentials WHERE customer_slug = '{slug}';"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="machine_credential", description=__doc__.split("\n\n")[0])
    p.add_argument("--slug", required=True)
    p.add_argument("--sql-out", required=True, type=Path, help="where the upsert SQL is written")
    p.add_argument("--prev-ttl-hours", type=int, default=DEFAULT_PREV_TTL_HOURS)
    args = p.parse_args(argv)
    try:
        plaintext, sql = mint(args.slug, prev_ttl_hours=args.prev_ttl_hours)
    except ValueError as exc:
        print(f"machine_credential: {exc}", file=sys.stderr)
        return 2
    args.sql_out.write_text(sql, encoding="utf-8")
    # The plaintext is the ONLY thing on stdout, with no label and no newline
    # noise, so `$(...)` in the caller captures exactly the key.
    sys.stdout.write(plaintext)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
