"""The root-side writer of a firm's acceptance (establish_intake/library_accept.py).

An acceptance produces exactly the ledger ``accept()`` would and stores bytes
whose sha256 matches; every refusal writes nothing to the vault. The applier is
run against the same fake vault to show the write converges on the seat.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from establish_intake import library_accept as LA
from shared import library_ledger as L
from spec_applier import library as lib
from tests.library_fixtures import FIXED, FakeVault, docx, p, sha

SLUG = "example-firm"
FORM = docx()


def _acc(data: bytes = FORM, **over) -> LA.Acceptance:
    base = LA.Acceptance(
        kind="mediation_brief",
        attorney=None,
        file_id="f-brief",
        version="v3",
        contract=L.MARKED,
        accepted_by="admin@firm.example",
        accepted_at="2026-11-06T17:00:00Z",
        data=data,
        fixed_strings=FIXED,
    )
    return replace(base, **over)


def _vault_and_seat(
    tmp_path: Path, vault: FakeVault | None = None, *, apply: bool = True
) -> tuple[FakeVault, LA.Vault]:
    vault = vault or FakeVault()
    spec_dir = tmp_path / "specs"

    def sleep(_s: float) -> None:  # the applier's poll tick, run in place of waiting
        if apply:
            lib.apply_library(s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=spec_dir)

    clock = iter(range(1000))
    return vault, LA.Vault(
        vault,
        vault.bucket,
        SLUG,
        spec_dir,
        sleep_fn=sleep,
        now_fn=lambda: next(clock),
        converge_timeout=5,
    )


def test_an_acceptance_writes_the_ledger_accept_would_and_bytes_whose_sha_matches(
    tmp_path: Path,
) -> None:
    vault, target = _vault_and_seat(tmp_path)
    acc = _acc()
    result = LA.accept_file(acc, target)
    assert result.status == "installed", result.reasons
    expected = L.accept(
        L.Ledger(),
        L.Entry(
            acc.kind,
            None,
            acc.file_id,
            acc.version,
            sha(FORM),
            acc.contract,
            acc.accepted_by,
            acc.accepted_at,
        ),
    )
    assert vault.objects[lib.ledger_key(SLUG)] == L.dump_ledger(expected)
    stored = vault.objects[lib.copy_key(SLUG, sha(FORM))]
    assert stored == FORM and sha(stored) == result.sha256
    assert vault.puts == [
        lib.copy_key(SLUG, sha(FORM)),
        lib.ledger_key(SLUG),
    ]  # the copy before the ledger
    assert (target.spec_dir / lib.copy_rel(sha(FORM))).read_bytes() == FORM
    assert "IN THE SUPERIOR COURT OF THE STATE OF EXAMPLE" in result.fixed_used


def test_a_second_acceptance_for_the_slot_replaces_and_keeps_the_previous_generation(
    tmp_path: Path,
) -> None:
    vault, target = _vault_and_seat(tmp_path)
    LA.accept_file(_acc(), target)
    first_raw = vault.objects[lib.ledger_key(SLUG)]
    newer = docx(p("[[party.plaintiff]]"))
    old = L.Replaced("f-brief", "v3", sha(FORM))
    result = LA.accept_file(_acc(newer, version="v4", replaces=old), target)
    assert result.status == "installed", result.reasons
    assert vault.objects[lib.previous_ledger_key(SLUG)] == first_raw
    ledger = L.parse_ledger(vault.objects[lib.ledger_key(SLUG)])
    assert ledger.entries[0].replaces == old and ledger.superseded[0].sha256 == sha(FORM)
    assert not (
        target.spec_dir / lib.copy_rel(sha(FORM))
    ).exists()  # the seat prunes the replaced copy


def test_accepting_the_same_file_again_writes_nothing(tmp_path: Path) -> None:
    vault, target = _vault_and_seat(tmp_path)
    LA.accept_file(_acc(), target)
    puts = list(vault.puts)
    result = LA.accept_file(_acc(replaces=L.Replaced("f-brief", "v3", sha(FORM))), target)
    assert result.unchanged and result.status == "installed"
    assert vault.puts == puts


@pytest.mark.parametrize(
    ("acc", "why"),
    [
        (_acc(kind="settlement_brochure"), "not in the document-kind registry"),
        (_acc(contract="template"), "contract"),
        (_acc(kind="demand_letter.settlement"), "not one the registry lists"),
        (_acc(kind="demand_letter"), "a slot names one"),
        (_acc(kind="memo.pre_suit"), "not one the registry lists"),
        (_acc(contract=L.PROTOTYPES), "cannot be accepted yet"),
        (_acc(b"PK not really a docx"), "not a Word document package"),
        (_acc(docx(p("Jordan Placeholder fell at Example Mart"))), "no-client-text check"),
        (_acc(docx(creator="Pat Example")), "no-client-text check"),
        (_acc(replaces=L.Replaced("f-other", "v1", "0" * 64)), "says it replaces"),
        (_acc(accepted_at="yesterday"), "accepted_at"),
    ],
)
def test_each_refusal_refuses_and_writes_nothing(
    tmp_path: Path, acc: LA.Acceptance, why: str
) -> None:
    vault, target = _vault_and_seat(tmp_path)
    result = LA.accept_file(acc, target)
    assert result.status == "rejected"
    assert any(why in r for r in result.reasons), result.reasons
    assert vault.puts == [] and vault.objects == {}


def test_an_invalid_ledger_in_the_vault_is_never_overwritten(tmp_path: Path) -> None:
    vault, target = _vault_and_seat(tmp_path, FakeVault({lib.ledger_key(SLUG): b"{torn"}))
    result = LA.accept_file(_acc(), target)
    assert result.status == "error" and vault.puts == []


def test_a_vault_that_cannot_be_read_writes_nothing(tmp_path: Path) -> None:
    vault, target = _vault_and_seat(tmp_path)
    vault.fail.add("get")
    assert LA.accept_file(_acc(), target).status == "error"
    assert vault.puts == []


def test_a_seat_that_has_not_applied_answers_pending_not_installed(tmp_path: Path) -> None:
    vault, target = _vault_and_seat(tmp_path, apply=False)
    result = LA.accept_file(_acc(), target)
    assert result.status == "accepted_pending_install"
    assert lib.ledger_key(SLUG) in vault.objects


def _run(tmp_path: Path, sub: dict, data: bytes) -> Path:
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    (run / LA.SUBMISSION).write_text(json.dumps(sub))
    (run / LA.COPY_NAME).write_bytes(data)
    return run


def _submission(data: bytes = FORM, **over) -> dict:
    acceptance = {
        "kind": "memo",
        "attorney": "atty-dana",
        "file_id": "f-memo",
        "version": "v1",
        "contract": "styles",
        "accepted_by": "admin@firm.example",
        "accepted_at": "2026-11-06T17:00:00Z",
        "sha256": sha(data),
        "fixed_strings": list(FIXED),
        "replaces": None,
    }
    acceptance.update(over)
    return {
        "scope": LA.SCOPE,
        "phase": "install",
        "run_id": "r1",
        "created_at": "2026-11-06T17:00:00Z",
        "acceptance": acceptance,
    }


def test_a_run_in_the_spool_schema_reads_as_an_acceptance(tmp_path: Path) -> None:
    acc, problems = LA.read_run(_run(tmp_path, _submission(), FORM), os.getuid())
    assert problems == [] and acc is not None
    assert (acc.kind, acc.attorney, acc.data) == ("memo", "atty-dana", FORM)
    assert set(LA.SUBMISSION_FIELDS) >= {f"acceptance.{k}" for k in _submission()["acceptance"]}


def test_a_run_not_written_by_the_broker_is_refused(tmp_path: Path) -> None:
    acc, problems = LA.read_run(_run(tmp_path, _submission(), FORM), os.getuid() + 1)
    assert acc is None and any("not written by the workspace broker" in x for x in problems)


@pytest.mark.parametrize(
    "sub",
    [
        _submission(sha256="0" * 64),
        _submission(extra="field"),
        {**_submission(), "scope": "firm"},
        _submission(fixed_strings="Plaintiff,"),
    ],
)
def test_a_malformed_submission_is_refused(tmp_path: Path, sub: dict) -> None:
    acc, problems = LA.read_run(_run(tmp_path, sub, FORM), os.getuid())
    assert acc is None and problems


def test_the_intake_does_not_dispatch_the_scope_yet() -> None:
    intake = (Path(LA.__file__).parent / "intake.py").read_text()
    assert "library_accept" not in intake


def test_a_variant_slot_the_registry_lists_is_accepted(tmp_path: Path) -> None:
    """The slot is kind, variant and attorney: a demand's variant is its own slot."""
    _vault, target = _vault_and_seat(tmp_path)
    result = LA.accept_file(_acc(kind="demand_letter.pre_suit"), target)
    assert result.status == "installed", result.reasons


def test_the_writers_registry_is_every_kind_with_its_variants() -> None:
    assert tuple(LA.REGISTRY) == LA.DOCUMENT_CLASSES
    assert set(LA.KIND_VARIANTS) <= set(LA.DOCUMENT_CLASSES)
