"""Purging one firm's accepted library from its vault (spec_applier/library_purge.py).

Only a fake vault and ``tmp_path``. The dry run lists exactly what the real run
removes, another firm's objects are never touched, and the applier then prunes
the seat.
"""

from __future__ import annotations

from pathlib import Path

from shared import library_ledger as L
from spec_applier import library as lib
from spec_applier import library_purge
from tests.library_fixtures import FakeVault, docx, p, sha

SLUG = "example-firm"
OTHER = "other-firm"
A = docx(p("[[a]]"))


def _seeded() -> FakeVault:
    entry = L.Entry(
        "memo", None, "f-memo", "v1", sha(A), L.STYLES, "admin@firm.example", "2026-11-06T17:00:00Z"
    )
    raw = L.dump_ledger(L.accept(L.Ledger(), entry))
    return FakeVault(
        {
            lib.ledger_key(SLUG): raw,
            lib.previous_ledger_key(SLUG): raw,
            lib.copy_key(SLUG, sha(A)): A,
            lib.ledger_key(OTHER): raw,
            lib.copy_key(OTHER, sha(A)): A,
            f"vaults/{SLUG}/output-classes.json": b"{}",
        }
    )


def test_the_dry_run_lists_exactly_what_the_real_run_removes(tmp_path: Path) -> None:
    vault = _seeded()
    planned = library_purge.purge(vault, vault.bucket, SLUG, dry_run=True)
    assert vault.deletes == []
    removed = library_purge.purge(vault, vault.bucket, SLUG, dry_run=False)
    assert removed == planned == vault.deletes
    assert planned[0] == lib.ledger_key(SLUG)
    assert set(planned) == {
        lib.ledger_key(SLUG),
        lib.previous_ledger_key(SLUG),
        lib.copy_key(SLUG, sha(A)),
    }
    assert (
        lib.ledger_key(OTHER) in vault.objects
        and f"vaults/{SLUG}/output-classes.json" in vault.objects
    )


def test_the_purge_then_the_applier_leaves_the_seat_with_no_library(tmp_path: Path) -> None:
    vault = _seeded()
    assert lib.apply_library(
        s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=tmp_path
    ).applied
    library_purge.purge(vault, vault.bucket, SLUG, dry_run=False)
    lib.apply_library(s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=tmp_path)
    assert not (tmp_path / "library").exists()
    assert L.read_installed(tmp_path).status == L.EMPTY


def test_the_command_prints_the_plan_and_refuses_a_bad_slug(tmp_path: Path, capsys) -> None:
    vault = _seeded()
    assert (
        library_purge.main(["purge", "--slug", SLUG, "--dry-run"], client=(vault, vault.bucket))
        == 0
    )
    out = capsys.readouterr().out
    assert f"would remove {lib.copy_key(SLUG, sha(A))}" in out and vault.deletes == []
    assert library_purge.main(["purge", "--slug", "../x"], client=(vault, vault.bucket)) == 1
    assert vault.deletes == []


def test_a_list_fault_deletes_nothing() -> None:
    vault = _seeded()
    vault.fail.add("list")
    assert library_purge.main(["purge", "--slug", SLUG], client=(vault, vault.bucket)) == 1
    assert vault.deletes == []
