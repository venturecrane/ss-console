"""SMD stocks a firm's library from one of its filed papers: the root half
(``establish_intake/library_stock.py``) hands the made file to the same writer a
firm's acceptance uses, with every check, and records who stocked it. Every
name and line of text is made up."""

from __future__ import annotations

import json
from pathlib import Path

from establish_intake import library_accept as LA
from establish_intake import library_stock as S
from shared import library_ledger as L
from spec_applier import library as lib
from tests.library_fixtures import FIXED, FakeVault, docx, p, sha

SLUG = "example-firm"
NOW = "2026-11-24T17:00:00Z"


def _dir(tmp_path: Path, data: bytes, fixed=FIXED, kind: str = "discovery_response") -> Path:
    d = tmp_path / "made"
    d.mkdir()
    (d / S.MADE).write_bytes(data)
    side = {
        "kind": kind,
        "attorney": None,
        "source_file_id": "file-0001",
        "source_version": "filed",
        "contract": "styles",
        "fixed_strings": list(fixed),
    }
    (d / S.SIDECAR).write_text(json.dumps(side), encoding="utf-8")
    return d


def _vault(tmp_path: Path, fake: FakeVault) -> LA.Vault:
    spec_dir = tmp_path / "specs"

    def sleep(_s: float) -> None:
        lib.apply_library(s3_client=fake, bucket=fake.bucket, slug=SLUG, spec_dir=spec_dir)

    clock = iter(range(1000))
    return LA.Vault(
        fake,
        fake.bucket,
        SLUG,
        spec_dir,
        sleep_fn=sleep,
        now_fn=lambda: next(clock),
        converge_timeout=5,
    )


def _ledger(fake: FakeVault) -> L.Ledger:
    return L.parse_ledger(fake.objects[lib.ledger_key(SLUG)])


def test_a_stocked_file_is_written_by_the_root_writer_and_names_smd_and_its_source(
    tmp_path: Path,
) -> None:
    fake, data = FakeVault(), docx()
    out = S.stock(
        _dir(tmp_path, data),
        _vault(tmp_path, fake),
        replace_current=False,
        check_only=False,
        now=NOW,
    )
    assert out["status"] == "installed", out
    entry = _ledger(fake).entry_for("discovery_response", None)
    assert entry is not None and entry.sha256 == sha(data) and entry.contract == "styles"
    assert entry.accepted_by == "smd:from-firm-filing:file-0001" and entry.file_id == "file-0001"
    assert fake.objects[lib.copy_key(SLUG, sha(data))] == data


def test_the_no_client_text_check_still_refuses_and_nothing_is_written(tmp_path: Path) -> None:
    fake = FakeVault()
    leaky = docx(p("Responding party Pat Example answers.") + p("[[body]]"))
    out = S.stock(
        _dir(tmp_path, leaky),
        _vault(tmp_path, fake),
        replace_current=False,
        check_only=False,
        now=NOW,
    )
    assert out["status"] == "rejected"
    assert any("no-client-text check" in r for r in out["reasons"])
    assert lib.ledger_key(SLUG) not in fake.objects


def test_a_slot_that_holds_a_file_is_replaced_only_when_asked(tmp_path: Path) -> None:
    fake, data = FakeVault(), docx()
    vault = _vault(tmp_path, fake)
    S.stock(_dir(tmp_path, data), vault, replace_current=False, check_only=False, now=NOW)
    other = docx(footer=None)
    d2 = tmp_path / "second"
    d2.mkdir()
    for name in (S.MADE, S.SIDECAR):
        (d2 / name).write_bytes((tmp_path / "made" / name).read_bytes())
    (d2 / S.MADE).write_bytes(other)
    refused = S.stock(d2, vault, replace_current=False, check_only=False, now=NOW)
    assert refused["status"] == "rejected" and refused["current"]["sha256"] == sha(data)
    done = S.stock(d2, vault, replace_current=True, check_only=False, now=NOW)
    assert done["status"] == "installed"
    assert _ledger(fake).entry_for("discovery_response", None).sha256 == sha(other)


def test_check_only_writes_nothing(tmp_path: Path) -> None:
    fake = FakeVault()
    out = S.stock(
        _dir(tmp_path, docx()),
        _vault(tmp_path, fake),
        replace_current=False,
        check_only=True,
        now=NOW,
    )
    assert out["status"] == "checked" and not fake.objects


def test_a_kind_outside_the_registry_is_refused(tmp_path: Path) -> None:
    fake = FakeVault()
    d = _dir(tmp_path, docx(), kind="discovery_response.special")
    out = S.stock(d, _vault(tmp_path, fake), replace_current=False, check_only=False, now=NOW)
    assert out["status"] == "rejected" and not fake.objects
