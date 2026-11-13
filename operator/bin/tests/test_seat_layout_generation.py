"""The seat layout is declared once and every generated copy agrees with it.

operator/contracts/seat-layout.yaml declares every seat path; operator/bin/
gen-seat-layout.py writes one copy per install tree. These tests are the CI
guard that a yaml edit cannot reach a seat without its regenerated outputs,
that the files allowed to spell a path still spell the declared one, that a
seat path the sprawl census finds is a decision (declared or left out with a
reason), and that a volume or bundle-mount path cannot change without the
ADR 0103 epoch or layout bump that makes the change safe on a live seat.

Run::

    cd operator && python3 -m pytest bin/tests/test_seat_layout_generation.py -v
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from bin.lib import seat_layout_gen as gen

ROOT = gen.repo_root()
LAYOUT = gen.load(ROOT / gen.CONTRACT_REL)

# The ADR 0103 pairing. A volume path lives on the Fly volume, so moving it needs
# operator/fleet/volume-epoch bumped (the rollout then refuses a lower epoch); a
# bundle mount is baked into the bundle's venvs, so moving it needs
# BundleSpec.layout bumped. Each pin maps an id to the path it had at that
# number. A NEW id is an addition, not a move: it passes and belongs in the pin.
# A pinned id whose path changed, or that left the yaml, fails until the number
# is bumped (then re-pin every id at the new number).
PINNED = {
    "volume": {
        "number": "2",
        "paths": {
            "attachment_spool": "/opt/data/attachment-spool",
            "casework_ledger": "/opt/data/audit/casework-ledger.jsonl",
            "data": "/opt/data",
            "escalation_ledger": "/opt/data/audit/escalation-ledger.jsonl",
            "installs": "/opt/data/installs",
            "jobs_data": "/opt/data/jobs",
            "profiles": "/opt/data/profiles",
            "records_queue_volume": "/opt/data/jobs/records-queue",
            "records_state": "/opt/data/jobs/records-state",
            "requests_db": "/opt/data/requests.db",
            "selftest_state": "/opt/data/selftest-state",
            "skills": "/opt/data/skills",
            "smokeball_extract_cache": "/opt/data/smokeball-extract-cache",
            "smokeball_refresh_token": "/opt/data/.smokeball-mcp/refresh_token",
        },
    },
    "bundle_mount": {
        "number": "2",
        "paths": {"jobs_bundle": "/opt/jobs", "smokeball_bundle": "/opt/connectors/smokeball"},
    },
}


def _moved(flag: str, number: str, layout: gen.Layout = LAYOUT) -> list[str]:
    """Pinned ids of ``flag`` that moved or vanished while the number stood still."""
    pin = PINNED[flag]
    if number != pin["number"]:
        return [f"the number is {number}, the pin says {pin['number']}: re-pin every {flag} id at it"]
    now = {k: e.path for k, e in layout.entries.items() if getattr(e, flag)}
    return [
        f"{pid}: pinned {path}, now {now.get(pid, 'gone')}"
        for pid, path in sorted(pin["paths"].items())
        if now.get(pid) != path
    ]


def _unpinned(flag: str) -> list[str]:
    return sorted(k for k, e in LAYOUT.entries.items() if getattr(e, flag) and k not in PINNED[flag]["paths"])


def _bundle_layout() -> str:
    manifest = (ROOT / "operator/hermes-smd-overlay/bundle_installer/manifest.py").read_text(encoding="utf-8")
    found = re.search(r"^\s+layout: int = (\d+)$", manifest, re.MULTILINE)
    assert found, "BundleSpec.layout not found in bundle_installer/manifest.py"
    return found.group(1)


def test_every_generated_output_is_in_sync() -> None:
    stale = [
        str(path.relative_to(ROOT))
        for path, text in gen.outputs(ROOT).items()
        if not path.is_file() or path.read_text(encoding="utf-8") != text
    ]
    assert stale == [], f"run python3 operator/bin/gen-seat-layout.py; stale: {stale}"


def test_check_mode_fails_on_a_stale_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # FALSIFIER for the test above: a copy of the tree with one output edited.
    for rel in (gen.CONTRACT_REL, gen.SHELL_REL, gen.JSON_REL, gen.TS_REL, *gen.PYTHON_RELS):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / gen.SHELL_REL).write_text("SMD_LAYOUT_DATA='/opt/elsewhere'\n", encoding="utf-8")
    monkeypatch.setattr(gen, "repo_root", lambda: tmp_path)
    cli = _load("gen_seat_layout_under_test", ROOT / "operator/bin/gen-seat-layout.py")
    assert cli.main(["gen-seat-layout.py", "--check"]) == 1


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("rel", gen.PYTHON_RELS)
def test_each_tree_module_imports_and_matches_the_yaml(rel: str) -> None:
    module = _load("seat_layout_under_test", ROOT / rel)
    assert module.PATHS == {k: e.path for k, e in LAYOUT.entries.items()}


def test_the_shell_file_assigns_and_exports_nothing() -> None:
    text = (ROOT / gen.SHELL_REL).read_text(encoding="utf-8")
    assert "export" not in text.replace("exports nothing", "")
    done = subprocess.run(  # noqa: S603 - sources the repo's own generated file
        ["bash", "-c", f'set -u; . "{ROOT / gen.SHELL_REL}"; printf %s "$SMD_LAYOUT_JOBS_RUN"; env'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.startswith(LAYOUT.entries["jobs_run"].path)
    assert "SMD_LAYOUT_" not in done.stdout[len(LAYOUT.entries["jobs_run"].path) :]


@pytest.mark.parametrize("row", LAYOUT.restated, ids=lambda r: r["file"])
def test_a_restated_file_spells_the_declared_path(row: dict) -> None:
    # The whole path as one token (quoted in code, bare in a Dockerfile RUN line):
    # /opt/jobs is not spelled by /opt/jobs/.venv/bin/python nor by /opt/jobsx.
    text = (ROOT / row["file"]).read_text(encoding="utf-8")
    for pid in row["ids"]:
        path = LAYOUT.entries[pid].path
        token = rf"(?<![\w./-]){re.escape(path)}(?![\w./-])"
        assert re.search(token, text), f"{row['file']} no longer spells {pid} = {path}"


def test_every_seat_path_the_census_finds_is_declared_or_left_out() -> None:
    baseline = json.loads((ROOT / "scripts/sprawl/literal-baseline.json").read_text(encoding="utf-8"))
    declared = {e.path for e in LAYOUT.entries.values()}
    left_out = {row["path"] for row in LAYOUT.left_out}
    undecided = sorted(p for p in baseline if p.startswith("/") and p not in declared | left_out)
    assert undecided == [], f"declare in {gen.CONTRACT_REL}, or add to left_out with a reason: {undecided}"


def test_a_volume_path_moves_only_with_the_epoch() -> None:
    epoch = (ROOT / "operator/fleet/volume-epoch").read_text(encoding="utf-8").strip()
    assert _moved("volume", epoch) == [], "a volume path moved without a bump of operator/fleet/volume-epoch"
    assert _unpinned("volume") == [], "a new volume id: add it to PINNED['volume'] with its path"


def test_a_bundle_mount_moves_only_with_the_bundle_layout() -> None:
    assert _moved("bundle_mount", _bundle_layout()) == [], "a bundle mount moved without a bump of BundleSpec.layout"
    assert _unpinned("bundle_mount") == [], "a new bundle mount id: add it to PINNED['bundle_mount'] with its path"


def test_the_pin_fails_on_a_moved_or_vanished_id_and_passes_an_addition(tmp_path: Path) -> None:
    # FALSIFIER for the two tests above, on a copy of the yaml.
    text = (ROOT / gen.CONTRACT_REL).read_text(encoding="utf-8")
    cases = {
        "moved": text.replace("path: '{data}/jobs'", "path: '{data}/jobs2'", 1),
        "vanished": text.replace("  records_state:\n    path: '{jobs_data}/records-state'\n    volume: true\n", "", 1),
        "added": text.replace(
            "paths:\n", "paths:\n  extra_on_volume:\n    path: '{data}/extra'\n    volume: true\n", 1
        ),
    }
    found = {}
    for name, body in cases.items():
        assert body != text, f"the {name} mutation did not apply"
        mutated = tmp_path / f"{name}.yaml"
        mutated.write_text(body, encoding="utf-8")
        found[name] = _moved("volume", PINNED["volume"]["number"], gen.load(mutated))
    assert found["moved"] and "jobs_data" in found["moved"][0]
    assert found["vanished"] and "records_state" in found["vanished"][0]
    assert found["added"] == []
    assert _moved("volume", "3") != [], "a bumped number must make the pin stale"


def test_a_bad_layout_is_refused(tmp_path: Path) -> None:
    for body, why in (
        ("paths:\n  a: {path: '{b}/x'}\n  b: {path: '{a}/y'}\n", "cycle"),
        ("paths:\n  a: {path: relative}\n", "clean absolute"),
        ("paths:\n  a: {path: /x, env: [V]}\n  b: {path: /y, env: [V]}\n", "more than one id"),
        ("paths:\n  a: {path: /x, owner: root}\n", "unknown fields"),
        ("paths:\n  a: {path: /x}\nleft_out:\n  - {path: /x, why: dup}\n", "declared path"),
    ):
        bad = tmp_path / "seat-layout.yaml"
        bad.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match=why):
            gen.load(bad)
