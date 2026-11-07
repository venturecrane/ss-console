"""A firm's accepted library file, end to end through the intake: a broker-written
run of scope ``library_accept`` is dispatched to the root writer, which checks
the bytes, writes the vault and waits for the applier's manifest. Made-up firm,
fake vault, the real applier against it."""

from __future__ import annotations

import json
import os
from pathlib import Path

from establish_intake import library_accept as LA
from establish_intake.intake import EstablishIntake
from shared import library_ledger as L
from spec_applier import library as lib
from tests.library_fixtures import FIXED, FakeVault, docx, p, sha

SLUG = "example-firm"
FORM = docx()


def _submission(data: bytes, run_id: str, **over) -> dict:
    acceptance = {
        "kind": "mediation_brief",
        "attorney": None,
        "file_id": "lib-file-1",
        "version": "ver-2",
        "contract": "marked",
        "accepted_by": "admin@firm.example",
        "accepted_at": "2026-11-07T10:00:00Z",
        "sha256": sha(data),
        "fixed_strings": list(FIXED),
        "replaces": None,
        **over,
    }
    return {
        "scope": LA.SCOPE,
        "phase": "install",
        "run_id": run_id,
        "created_at": "x",
        "acceptance": acceptance,
    }


def _intake(
    tmp_path: Path, vault: FakeVault, *, apply: bool = True, uid: int | None = None
) -> EstablishIntake:
    spec_dir = tmp_path / "specs"

    def sleep(_s: float) -> None:  # the applier's poll, run in place of waiting
        if apply:
            lib.apply_library(s3_client=vault, bucket=vault.bucket, slug=SLUG, spec_dir=spec_dir)

    clock = iter(range(10_000))
    return EstablishIntake(
        spool_dir=tmp_path / "spool",
        s3_client=vault,
        bucket=vault.bucket,
        slug=SLUG,
        spec_dir=spec_dir,
        broker_uid=os.getuid() if uid is None else uid,
        broker_gid=None,
        sleep_fn=sleep,
        now_fn=lambda: next(clock),
        converge_timeout=5,
        converge_interval=1,
    )


def _spool(intake: EstablishIntake, run_id: str, data: bytes, **over) -> None:
    run = intake.runs_dir / run_id
    run.mkdir(parents=True)
    (run / "file.docx").write_bytes(data)
    (run / "submission.json").write_text(json.dumps(_submission(data, run_id, **over)))


def _result(intake: EstablishIntake, run_id: str) -> dict:
    return json.loads((intake.results_dir / f"{run_id}.json").read_text())


def test_a_spooled_acceptance_is_installed_and_the_seat_holds_its_copy(tmp_path: Path) -> None:
    vault = FakeVault()
    intake = _intake(tmp_path, vault)
    _spool(intake, "run-one-0001", FORM)
    assert intake.poll_once() == ["run-one-0001"]
    result = _result(intake, "run-one-0001")
    assert result["status"] == "installed", result["reasons"]
    assert result["scope"] == LA.SCOPE and result["sha256"] == sha(FORM)
    ledger = L.parse_ledger(vault.objects[lib.ledger_key(SLUG)])
    entry = ledger.entry_for("mediation_brief", None)
    assert entry is not None and (entry.file_id, entry.version, entry.sha256) == (
        "lib-file-1",
        "ver-2",
        sha(FORM),
    )
    assert (tmp_path / "specs" / "library" / "files" / f"{sha(FORM)}.docx").read_bytes() == FORM
    assert not (intake.runs_dir / "run-one-0001").exists()  # the run is purged on every path


def test_an_applier_that_has_not_polled_yet_is_accepted_pending_install(tmp_path: Path) -> None:
    intake = _intake(tmp_path, FakeVault(), apply=False)
    _spool(intake, "run-two-0001", FORM)
    intake.poll_once()
    assert _result(intake, "run-two-0001")["status"] == "accepted_pending_install"


def test_a_file_with_client_text_is_rejected_and_nothing_is_written(tmp_path: Path) -> None:
    vault = FakeVault()
    intake = _intake(tmp_path, vault)
    leaky = docx(p("Jordan Placeholder slipped at Example Mart"))
    _spool(intake, "run-three-01", leaky)
    intake.poll_once()
    assert _result(intake, "run-three-01")["status"] == "rejected"
    assert vault.puts == []


def test_a_run_another_uid_wrote_is_rejected(tmp_path: Path) -> None:
    vault = FakeVault()
    intake = _intake(tmp_path, vault, uid=os.getuid() + 1)
    _spool(intake, "run-four-001", FORM)
    intake.poll_once()
    assert _result(intake, "run-four-001")["status"] == "rejected" and vault.puts == []
