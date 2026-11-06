"""The seat's copy of each accepted library file, installed by root (spec_applier/library.py).

Every side effect is faked: a dict vault and ``tmp_path`` as the spec store.
The tests that matter are the refusals and the prunes: a copy whose bytes do
not match is never installed and the previous library stands; a copy no entry
names is removed; a ledger removed from the vault removes everything; and a
store the applier cannot read is "cannot look", never "none".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import library_ledger as L
from spec_applier import library as lib
from spec_applier.applier import SpecApplyError, SpecApplyOutcome
from spec_applier.applier import apply as apply_specs
from tests.library_fixtures import FakeVault, docx, p, sha

SLUG = "example-firm"
_SDK_LEDGER = (
    Path(__file__).resolve().parents[2]
    / "connectors"
    / "_sdk"
    / "operator_connector_sdk"
    / "library"
    / "ledger.py"
)


def _entry(kind: str, data: bytes, attorney: str | None = None) -> L.Entry:
    return L.Entry(
        kind=kind,
        attorney=attorney,
        file_id=f"f-{kind}-{attorney or 'firm'}",
        version="v1",
        sha256=sha(data),
        contract=L.MARKED,
        accepted_by="admin@firm.example",
        accepted_at="2026-11-06T17:00:00Z",
    )


A = docx(p("[[a]]"))
B = docx(p("[[b]]"))


def _vault(*entries_and_bytes: tuple[L.Entry, bytes]) -> FakeVault:
    ledger = L.Ledger()
    vault = FakeVault()
    for entry, data in entries_and_bytes:
        ledger = L.accept(ledger, entry)
        vault.objects[lib.copy_key(SLUG, entry.sha256)] = data
    vault.objects[lib.ledger_key(SLUG)] = L.dump_ledger(ledger)
    return vault


def _apply(vault: FakeVault, spec_dir: Path):
    return lib.apply_library(s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=spec_dir)


def _manifest(spec_dir: Path) -> dict:
    return json.loads((spec_dir / "manifest.json").read_text())


def _sdk_read(spec_dir: Path) -> L.LedgerRead:
    return L.read_installed(spec_dir)


def test_the_overlay_ledger_is_byte_equal_to_the_sdk_ledger() -> None:
    assert (
        Path(__file__).resolve().parents[1] / "shared" / "library_ledger.py"
    ).read_bytes() == _SDK_LEDGER.read_bytes()


def test_a_ledger_with_two_entries_installs_both_copies_and_the_manifest_names_them(
    tmp_path: Path,
) -> None:
    vault = _vault((_entry("memo", A), A), (_entry("letter", B, "atty-dana"), B))
    result = _apply(vault, tmp_path)
    assert result.outcome is SpecApplyOutcome.APPLIED
    for data in (A, B):
        rel = lib.copy_rel(sha(data))
        assert (tmp_path / rel).read_bytes() == data
        assert _manifest(tmp_path)[lib.FILES_KEY][rel] == {"sha256": sha(data), "bytes": len(data)}
    assert (tmp_path / lib.COPIES_REL).stat().st_mode & 0o777 == 0o755
    assert (tmp_path / lib.copy_rel(sha(A))).stat().st_mode & 0o777 == 0o644
    read = _sdk_read(tmp_path)  # the jobs-side reader vouches for what root wrote
    assert read.status == L.OK and len(read.ledger.entries) == 2
    assert (
        _manifest(tmp_path)["specs"] == {}
    )  # a library-only seat still reads as "no spec installed"
    assert _apply(vault, tmp_path).outcome is SpecApplyOutcome.UNCHANGED


def test_a_copy_whose_bytes_do_not_match_is_not_installed_and_the_prior_state_stands(
    tmp_path: Path,
) -> None:
    vault = _vault((_entry("memo", A), A))
    assert _apply(vault, tmp_path).applied
    before = {
        p.relative_to(tmp_path).as_posix(): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    entry_b = _entry("letter", B)
    ledger = L.accept(L.parse_ledger(vault.objects[lib.ledger_key(SLUG)]), entry_b)
    vault.objects[lib.ledger_key(SLUG)] = L.dump_ledger(ledger)
    vault.objects[lib.copy_key(SLUG, entry_b.sha256)] = B + b"torn"
    result = _apply(vault, tmp_path)
    assert result.outcome is SpecApplyOutcome.REJECTED
    assert any("do not hash" in r for r in result.reasons)
    after = {
        p.relative_to(tmp_path).as_posix(): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    assert after == before
    assert not (tmp_path / lib.copy_rel(entry_b.sha256)).exists()


def test_a_copy_the_vault_does_not_hold_refuses_the_cycle(tmp_path: Path) -> None:
    vault = _vault((_entry("memo", A), A))
    del vault.objects[lib.copy_key(SLUG, sha(A))]
    result = _apply(vault, tmp_path)
    assert result.outcome is SpecApplyOutcome.REJECTED
    assert not (tmp_path / "library").exists()


def test_an_invalid_ledger_is_rejected_and_installs_nothing(tmp_path: Path) -> None:
    vault = FakeVault({lib.ledger_key(SLUG): b'{"schema_version": 9}'})
    assert _apply(vault, tmp_path).outcome is SpecApplyOutcome.REJECTED
    assert not (tmp_path / "manifest.json").exists()


def test_removing_an_entry_prunes_its_copy(tmp_path: Path) -> None:
    vault = _vault((_entry("memo", A), A), (_entry("letter", B), B))
    _apply(vault, tmp_path)
    only_a = L.accept(L.Ledger(), _entry("memo", A))
    vault.objects[lib.ledger_key(SLUG)] = L.dump_ledger(only_a)
    result = _apply(vault, tmp_path)
    assert result.pruned == (lib.copy_rel(sha(B)),)
    assert not (tmp_path / lib.copy_rel(sha(B))).exists()
    assert (tmp_path / lib.copy_rel(sha(A))).exists()
    assert set(_manifest(tmp_path)[lib.FILES_KEY]) == {lib.copy_rel(sha(A))}


def test_a_replaced_entrys_copy_is_pruned(tmp_path: Path) -> None:
    vault = _vault((_entry("memo", A), A))
    _apply(vault, tmp_path)
    replaced = L.accept(L.parse_ledger(vault.objects[lib.ledger_key(SLUG)]), _entry("memo", B))
    vault.objects[lib.ledger_key(SLUG)] = L.dump_ledger(replaced)
    vault.objects[lib.copy_key(SLUG, sha(B))] = B
    assert _apply(vault, tmp_path).pruned == (lib.copy_rel(sha(A)),)


def test_removing_the_ledger_prunes_everything(tmp_path: Path) -> None:
    vault = _vault((_entry("memo", A), A), (_entry("letter", B), B))
    _apply(vault, tmp_path)
    vault.objects.clear()
    result = _apply(vault, tmp_path)
    assert result.outcome is SpecApplyOutcome.APPLIED
    assert not (tmp_path / "library").exists()
    assert not any(k.startswith("library") for k in _manifest(tmp_path))
    assert _sdk_read(tmp_path).status == L.EMPTY
    assert _apply(vault, tmp_path).outcome is SpecApplyOutcome.UNCHANGED


def test_a_vault_fault_removes_nothing(tmp_path: Path) -> None:
    vault = _vault((_entry("memo", A), A))
    _apply(vault, tmp_path)
    vault.fail.add("get")
    with pytest.raises(SpecApplyError):
        _apply(vault, tmp_path)
    assert (tmp_path / lib.copy_rel(sha(A))).exists()


def test_an_unreadable_installed_manifest_is_cannot_look_never_none(tmp_path: Path) -> None:
    vault = _vault((_entry("memo", A), A))
    _apply(vault, tmp_path)
    (tmp_path / "manifest.json").write_text("{torn")
    with pytest.raises(SpecApplyError, match="cannot be read"):
        _apply(vault, tmp_path)
    assert (
        tmp_path / "manifest.json"
    ).read_text() == "{torn"  # not rewritten as "nothing installed"
    assert _sdk_read(tmp_path).status == L.UNUSABLE
    vault.objects.clear()  # even a removal does not rewrite an unreadable manifest
    with pytest.raises(SpecApplyError):
        _apply(vault, tmp_path)
    assert (tmp_path / lib.copy_rel(sha(A))).exists()


def test_a_spec_install_keeps_the_librarys_manifest_record(tmp_path: Path) -> None:
    vault = _vault((_entry("memo", A), A))
    _apply(vault, tmp_path)
    body = "Write plainly."
    doc = {
        "schema_version": 1,
        "classes": {"staff": {"voice": {"body": body, "sha256": sha(body.encode())}}},
    }
    vault.objects["vaults/example-firm/output-classes.json"] = json.dumps(doc).encode()
    assert apply_specs(s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=tmp_path).applied
    manifest = _manifest(tmp_path)
    assert "classes/staff/voice.md" in manifest["specs"]
    assert lib.copy_rel(sha(A)) in manifest[lib.FILES_KEY]
    assert _sdk_read(tmp_path).status == L.OK


def test_a_slug_outside_the_charset_is_refused() -> None:
    for bad in ("", "../x", "a/b", "Firm"):
        with pytest.raises(SpecApplyError):
            lib.library_prefix(bad)


def test_the_poll_tick_applies_the_library_and_caches_only_a_handled_signature(
    tmp_path: Path,
) -> None:
    from spec_applier.__main__ import SpecPollLoop

    vault = _vault((_entry("memo", A), A))
    loop = SpecPollLoop(s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=tmp_path)
    vault.head_object = lambda **_: (_ for _ in ()).throw(RuntimeError("no spec object"))  # type: ignore[attr-defined]
    assert loop._run_library_once() is SpecApplyOutcome.APPLIED
    assert loop._run_library_once() is None  # nothing moved
    vault.fail.add("get")
    vault.objects[lib.copy_key(SLUG, "0" * 64)] = b"new object"
    assert loop._run_library_once() is None  # a fault: signature not cached
    vault.fail.clear()
    assert loop._run_library_once() is SpecApplyOutcome.UNCHANGED
