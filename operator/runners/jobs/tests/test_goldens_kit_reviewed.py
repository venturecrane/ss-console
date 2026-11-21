"""The kit goldens' allowed list (``goldens_kit.allowed``) honours only this
change's own entries: an entry the merge-base already carried excuses nothing.

Each case is a small git repo whose ``origin/main`` is the merge-base, with a
golden family under it standing in for ``goldens/``."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import goldens_kit as gk
import pytest
from bin.lib.golden_diff import BASE_ENV, forget_base


def _git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", *cfg, *args], cwd=repo, env=env, check=True, capture_output=True)  # noqa: S603 - fixed git argv in a temp repo


@pytest.fixture
def family(tmp_path, monkeypatch):
    """``family(base, head)``: the ``fam`` set's list at the merge-base and at
    HEAD, with a golden ``cell.json`` of ``{"state": "old"}``."""
    monkeypatch.delenv(BASE_ENV, raising=False)
    monkeypatch.delenv(gk.WRITE_ENV, raising=False)
    monkeypatch.setattr(gk, "GOLDENS", tmp_path / "goldens")
    d = tmp_path / "goldens" / "fam"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps({"state": "old"}), encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")

    def make(base: list[str], head: list[str]) -> Path:
        for paths, msg in ((base, "base"), (head, "change")):
            (d / "allowed-changes.json").write_text(json.dumps({"_why": "x", "cell": paths}), encoding="utf-8")
            _git(tmp_path, "add", "-A")
            _git(tmp_path, "commit", "-q", "--allow-empty", "-m", msg)
            if msg == "base":
                _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
        forget_base()
        return d / "cell.json"

    yield make
    forget_base()


def test_a_change_that_names_its_path_passes(family):
    golden = family([], ["state"])
    assert gk.compare(golden, {"state": "new"}, gk.allowed("fam")["cell"]) == []


def test_a_leftover_entry_does_not_excuse_a_code_only_change(family):
    """FALSIFIER: the old reader (the raw file) excuses it; this one does not."""
    golden = family(["state"], ["state"])
    raw = json.loads((golden.parent / "allowed-changes.json").read_text(encoding="utf-8"))
    assert gk.compare(golden, {"state": "new"}, raw["cell"]) == []
    assert gk.compare(golden, {"state": "new"}, gk.allowed("fam")["cell"]) == ["state"]


def test_a_leftover_entry_alone_blocks_nothing(family):
    golden = family(["state"], ["state"])
    assert gk.compare(golden, {"state": "old"}, gk.allowed("fam")["cell"]) == []


def test_under_the_write_switch_every_entry_is_read_without_a_base(tmp_path, monkeypatch):
    """The integrity job writes in a base tree with no commit: no git lookup."""
    monkeypatch.setenv(gk.WRITE_ENV, "1")
    monkeypatch.setattr(gk, "GOLDENS", tmp_path)
    (tmp_path / "fam").mkdir()
    (tmp_path / "fam" / "allowed-changes.json").write_text(json.dumps({"cell": ["state"]}), encoding="utf-8")
    assert gk.allowed("fam") == {"cell": ["state"]}
