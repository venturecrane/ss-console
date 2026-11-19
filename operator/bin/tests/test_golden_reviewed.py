"""An allowed entry excuses a difference only in the change that adds it
(``bin/lib/golden_diff.py``: ``this_change``, ``reviewed``).

A golden test reads its allowed list through ``reviewed``, which drops every
entry the merge-base tree already carried. Each case below is a small git repo
whose ``origin/main`` is the merge-base and whose HEAD is the change."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from bin.lib.golden_diff import BASE_ENV, BaseUnknownError, forget_base, reviewed, this_change, unexplained

SET = "goldens/lane"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null"]
    out = subprocess.run(["git", *cfg, *args], cwd=repo, env=env, check=True, capture_output=True, text=True)  # noqa: S603 - fixed git argv in a temp repo
    return out.stdout.strip()


def _commit(repo: Path, allowed: dict[str, list[str]] | None, msg: str) -> str:
    d = repo / SET
    d.mkdir(parents=True, exist_ok=True)
    f = d / "allowed-changes.json"
    if allowed is None:
        f.unlink(missing_ok=True)
    else:
        f.write_text(json.dumps({"_why": "x", **allowed}), encoding="utf-8")
    (repo / "touch").write_text(msg, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def change(tmp_path, monkeypatch):
    """``change(base, head)``: a repo whose merge-base carries ``base`` (None:
    no list at all) and whose HEAD carries ``head``; returns ``reviewed``."""
    monkeypatch.delenv(BASE_ENV, raising=False)
    forget_base()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    def make(base: dict[str, list[str]] | None, head: dict[str, list[str]]) -> dict[str, list[str]]:
        sha = _commit(repo, base, "base")
        _git(repo, "update-ref", "refs/remotes/origin/main", sha)
        _git(repo, "checkout", "-q", "-b", "pr")
        _commit(repo, head, "change")
        forget_base()
        return reviewed(repo / SET)

    yield make
    forget_base()


def test_this_change_keeps_every_key_and_drops_what_the_base_carried():
    assert this_change({"a": ["0.x", "0.y"], "b": []}, {"a": ["0.x"]}) == {"a": ["0.y"], "b": []}
    assert this_change({"a": ["0.x"]}, {}) == {"a": ["0.x"]}


def test_a_change_that_names_its_own_path_is_excused(change):
    """Risk 2: an entry the base did not carry excuses its difference, as before."""
    own = change({"cell": []}, {"cell": ["0.state"]})
    assert unexplained(["0.state"], own["cell"]) == []


def test_a_leftover_entry_excuses_a_later_code_only_change_nothing(change):
    """Risk 3, the hole. FALSIFIER: read the list with ``load_allowed`` (every
    entry) and the difference is excused; through ``reviewed`` it fails."""
    own = change({"cell": ["0.state"]}, {"cell": ["0.state"]})
    assert own == {"cell": []}
    assert unexplained(["0.state"], own["cell"]) == ["0.state"]


def test_a_leftover_entry_with_nothing_changed_blocks_nothing(change):
    """Risk 4: another change's leftover, and no difference: nothing fails."""
    own = change({"cell": ["0.state"]}, {"cell": ["0.state"], "other": []})
    assert unexplained([], own["cell"]) == [] and own["other"] == []


def test_an_entry_edited_in_place_honours_only_the_new_paths(change):
    """Risk 5: same key, a path added beside a leftover one."""
    own = change({"cell": ["0.state"]}, {"cell": ["0.state", "1.wake"]})
    assert own == {"cell": ["1.wake"]}
    assert unexplained(["0.state", "1.wake"], own["cell"]) == ["0.state"]


def test_a_set_the_base_does_not_have_is_all_this_changes(change):
    """Risk 6: a golden family introduced by the change behaves as today."""
    assert change(None, {"cell": ["0.state"]}) == {"cell": ["0.state"]}


def test_an_explicit_base_wins_over_origin_main(change, tmp_path, monkeypatch):
    """``GOLDENS_BASE`` names the merge-base when origin/main cannot."""
    change({"cell": []}, {"cell": ["0.state"]})
    repo = tmp_path / "repo"
    monkeypatch.setenv(BASE_ENV, "HEAD")  # the change itself as base: every entry inherited
    forget_base()
    assert reviewed(repo / SET) == {"cell": []}


def test_no_base_fails_loudly_and_never_takes_every_entry_as_new(tmp_path, monkeypatch):
    """With an entry named and no origin/main, the reader raises with how to
    supply the base; it never falls back to excusing every entry."""
    monkeypatch.delenv(BASE_ENV, raising=False)
    forget_base()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"cell": ["0.state"]}, "only")
    with pytest.raises(BaseUnknownError, match=BASE_ENV):
        reviewed(repo / SET)
    monkeypatch.setenv(BASE_ENV, "no-such-ref")
    forget_base()
    with pytest.raises(BaseUnknownError, match="not a commit"):
        reviewed(repo / SET)
    forget_base()


def test_an_empty_list_needs_no_base(tmp_path, monkeypatch):
    """Nothing to excuse: no git, no base, no error (a tree with no commits)."""
    monkeypatch.delenv(BASE_ENV, raising=False)
    forget_base()
    d = tmp_path / SET
    d.mkdir(parents=True)
    (d / "allowed-changes.json").write_text(json.dumps({"_why": "x", "cell": []}), encoding="utf-8")
    assert reviewed(d) == {"cell": []}
    forget_base()
