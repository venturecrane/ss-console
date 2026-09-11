"""machine_credential.py: the hash the console verifies, and the output discipline.

The vector pinned here is the same one `tests/machine-key-auth.test.ts` pins on
the TypeScript side; both must produce
e858ae0a20bbe215e94b7e816abc9f918ec4317aa725c351d26730af9e13071e for the
salt 000102030405060708090a0b0c0d0e0f and the plaintext "test-plaintext-key".
If either side changes its HMAC input shape, its vector test goes red before
the cross-language round trip does.

Run::

    cd operator && python3 -m pytest bin/tests/test_machine_credential.py -q
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import machine_credential as mc

VECTOR_SALT = "000102030405060708090a0b0c0d0e0f"
VECTOR_PLAINTEXT = "test-plaintext-key"
VECTOR_HASH = "e858ae0a20bbe215e94b7e816abc9f918ec4317aa725c351d26730af9e13071e"
MODULE = Path(__file__).resolve().parents[1] / "lib" / "machine_credential.py"


def test_vector_matches_the_typescript_side() -> None:
    assert mc.key_hash_hex(VECTOR_PLAINTEXT, VECTOR_SALT) == VECTOR_HASH
    assert mc.verify(VECTOR_PLAINTEXT, VECTOR_SALT, VECTOR_HASH)
    assert not mc.verify(VECTOR_PLAINTEXT + "x", VECTOR_SALT, VECTOR_HASH)
    assert not mc.verify(VECTOR_PLAINTEXT, "ff" * 16, VECTOR_HASH)


def test_mint_returns_a_fresh_key_and_sql_that_never_carries_it() -> None:
    plaintext, sql = mc.mint("seat-a")
    assert len(plaintext) == mc.KEY_BYTES * 2
    assert plaintext not in sql
    assert "INSERT INTO machine_credentials" in sql
    assert "FROM customer_configs" in sql, "entity_id must come from the projection, not argv"
    assert "WHERE customer_slug = 'seat-a'" in sql
    assert "ON CONFLICT(customer_slug) DO UPDATE" in sql
    assert "prev_key_hash   = machine_credentials.key_hash" in sql
    assert "+24 hours" in sql
    # The hash in the SQL is the hash of the returned plaintext under the
    # salt in the SQL: the row the console stores verifies the key it gets.
    salt = sql.split("', '")[1].split("'")[0]
    digest = sql.split("SELECT customer_slug, entity_id, '")[1].split("'")[0]
    assert mc.verify(plaintext, salt, digest)


def test_two_mints_never_share_key_or_salt() -> None:
    a, sa = mc.mint("seat-a")
    b, sb = mc.mint("seat-a")
    assert a != b
    assert sa != sb


@pytest.mark.parametrize("bad", ["", "Seat", "seat_a", "../x", "a' OR 1=1 --", "x" * 64])
def test_slug_is_validated_before_it_reaches_sql(bad: str) -> None:
    with pytest.raises(ValueError):
        mc.mint(bad)
    with pytest.raises(ValueError):
        mc.count_sql(bad)


def test_prev_ttl_is_honoured_and_bounded() -> None:
    _, sql = mc.mint("seat-a", prev_ttl_hours=6)
    assert "+6 hours" in sql
    with pytest.raises(ValueError):
        mc.mint("seat-a", prev_ttl_hours=0)


def test_cli_writes_sql_to_the_file_and_only_the_plaintext_to_stdout(tmp_path: Path) -> None:
    out = tmp_path / "mint.sql"
    proc = subprocess.run(
        [sys.executable, str(MODULE), "--slug", "seat-a", "--sql-out", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    plaintext = proc.stdout
    assert len(plaintext) == mc.KEY_BYTES * 2 and plaintext.strip() == plaintext
    assert proc.stderr == ""
    sql = out.read_text()
    assert plaintext not in sql
    assert "seat-a" in sql


def test_cli_refuses_a_bad_slug_without_writing(tmp_path: Path) -> None:
    out = tmp_path / "mint.sql"
    proc = subprocess.run(
        [sys.executable, str(MODULE), "--slug", "Bad Slug", "--sql-out", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert not out.exists()
