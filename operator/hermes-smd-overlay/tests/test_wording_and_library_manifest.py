"""Two root writers share one manifest, and neither drops the other's record.

The spec install writes ``wordings`` and ``wording_schema`` (shared/wording_rules.py)
and carries the library's keys forward (spec_applier/library.py ``carried_keys``);
the library install rewrites the manifest through its own writer, which keeps
every key it does not own. Each direction is proven by reading the installed
store back through its own reader. Names are made up.
"""

from __future__ import annotations

import json
from pathlib import Path

from shared import library_ledger as L
from shared import wording_rules_store
from spec_applier import library as lib
from spec_applier.applier import apply as apply_specs
from tests.library_fixtures import FakeVault, docx, p, sha

SLUG = "example-firm"
COPY = docx(p("[[a]]"))


def _ledger(vault: FakeVault) -> None:
    entry = L.Entry(
        kind="memo",
        attorney=None,
        file_id="f-memo-firm",
        version="v1",
        sha256=sha(COPY),
        contract=L.MARKED,
        accepted_by="admin@firm.example",
        accepted_at="2026-11-06T17:00:00Z",
    )
    vault.objects[lib.copy_key(SLUG, entry.sha256)] = COPY
    vault.objects[lib.ledger_key(SLUG)] = L.dump_ledger(L.accept(L.Ledger(), entry))


def _wording_rule(vault: FakeVault) -> None:
    adjustment = {
        "id": "5e1f00aa",
        "text": "Write persuasively.",
        "instructed_by": "admin@firm.example",
        "at": "2026-12-01T00:00:00Z",
        "doc_kinds": ["memo"],
    }
    doc = {
        "schema_version": 1,
        "classes": {"work_product": {"voice": {"adjustments": [adjustment]}}},
    }
    vault.objects[f"vaults/{SLUG}/output-classes.json"] = json.dumps(doc).encode()


def _specs(vault: FakeVault, spec_dir: Path) -> None:
    assert apply_specs(s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=spec_dir).applied


def _library(vault: FakeVault, spec_dir: Path) -> None:
    assert lib.apply_library(
        s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=spec_dir
    ).applied


def test_a_wording_install_after_a_library_install_keeps_the_library_keys(tmp_path: Path):
    vault = FakeVault()
    _ledger(vault)
    _library(vault, tmp_path)
    _wording_rule(vault)
    _specs(vault, tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert all(key in manifest for key in lib.OWNED_KEYS)
    assert L.read_installed(tmp_path).status == L.OK
    assert wording_rules_store.read(tmp_path).status == wording_rules_store.OK


def test_a_library_install_after_a_wording_install_keeps_the_wording_keys(tmp_path: Path):
    vault = FakeVault()
    _wording_rule(vault)
    _specs(vault, tmp_path)
    _ledger(vault)
    _library(vault, tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert wording_rules_store.WORDING_REL in manifest[wording_rules_store.MANIFEST_KEY]
    assert manifest["wording_schema"] == wording_rules_store.SCHEMA_VERSION
    store = wording_rules_store.read(tmp_path)
    assert store.status == wording_rules_store.OK
    assert [r["rule_id"] for r in store.rules] == ["5e1f00aa"]
    assert L.read_installed(tmp_path).status == L.OK
