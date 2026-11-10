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

import hashlib
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
# BundleSpec.layout bumped. Change a path, bump the number, then update the pin.
PINNED = {
    "volume": {"epoch": "2", "paths": "93ef6c85040787f2"},
    "bundle_mount": {"layout": "2", "paths": "a73e2c5f59fafb65"},
}


def _fingerprint(flag: str) -> str:
    paths = sorted(e.path for e in LAYOUT.entries.values() if getattr(e, flag))
    return hashlib.sha256("\n".join(paths).encode()).hexdigest()[:16]


def test_every_generated_output_is_in_sync() -> None:
    stale = [
        str(path.relative_to(ROOT))
        for path, text in gen.outputs(ROOT).items()
        if not path.is_file() or path.read_text(encoding="utf-8") != text
    ]
    assert stale == [], f"run python3 operator/bin/gen-seat-layout.py; stale: {stale}"


def test_check_mode_fails_on_a_stale_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # FALSIFIER for the test above: a copy of the tree with one output edited.
    for rel in (gen.CONTRACT_REL, gen.SHELL_REL, gen.JSON_REL, *gen.PYTHON_RELS):
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
    text = (ROOT / row["file"]).read_text(encoding="utf-8")
    for pid in row["ids"]:
        path = LAYOUT.entries[pid].path
        assert re.search(rf"[\"']{re.escape(path)}[\"']", text), f"{row['file']} no longer spells {pid} = {path}"


def test_every_seat_path_the_census_finds_is_declared_or_left_out() -> None:
    baseline = json.loads((ROOT / "scripts/sprawl/literal-baseline.json").read_text(encoding="utf-8"))
    declared = {e.path for e in LAYOUT.entries.values()}
    left_out = {row["path"] for row in LAYOUT.left_out}
    undecided = sorted(p for p in baseline if p.startswith("/") and p not in declared | left_out)
    assert undecided == [], f"declare in {gen.CONTRACT_REL}, or add to left_out with a reason: {undecided}"


def test_a_volume_path_moves_only_with_the_epoch() -> None:
    epoch = (ROOT / "operator/fleet/volume-epoch").read_text(encoding="utf-8").strip()
    now = {"epoch": epoch, "paths": _fingerprint("volume")}
    assert now == PINNED["volume"], f"volume paths or epoch changed together? pin {now} after bumping the epoch"


def test_a_bundle_mount_moves_only_with_the_bundle_layout() -> None:
    manifest = (ROOT / "operator/hermes-smd-overlay/bundle_installer/manifest.py").read_text(encoding="utf-8")
    found = re.search(r"^\s+layout: int = (\d+)$", manifest, re.MULTILINE)
    assert found, "BundleSpec.layout not found in bundle_installer/manifest.py"
    now = {"layout": found.group(1), "paths": _fingerprint("bundle_mount")}
    assert now == PINNED["bundle_mount"], f"bundle mounts or layout changed? pin {now} after bumping BundleSpec.layout"


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
