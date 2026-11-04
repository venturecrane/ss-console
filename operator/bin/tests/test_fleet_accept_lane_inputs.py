"""A person's call that the seats' staged firm inputs fit a release's schema is merged once in
operator/fleet/lane-inputs-accepted.yaml, and the tick's preflight reads it from the checkout
(2026-11-04: ADR 0103 renamed the runner's package, every lane's digest changed in name only, and the
tick halted at staging)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bin.lib.fleet.preflight import (
    ACCEPTED_LANE_INPUTS_PATH,
    LaneInputDeclarations,
    PreflightChecker,
    accepted_lanes,
    check_lanes,
    covers,
)
from bin.lib.fleet.r2 import image_key
from bin.lib.fleet.runner import CommandRunner
from bin.tests.fleet_fakes import DIGEST, OLD_DIGEST, FakeFly, FakeR2, release

DECL = {"since": "1" * 40, "lanes": ["records"], "by": "scott", "date": "2026-11-04", "why": "names only"}


def test_accepted_lanes_and_covers() -> None:
    assert accepted_lanes(None) == set()
    assert accepted_lanes({"lanes": "*"}) == {"*"}
    assert accepted_lanes({"lanes": ["records", "demand"]}) == {"records", "demand"}
    assert accepted_lanes({"lanes": 3}) == set()
    assert covers([DECL], "records") is DECL and covers([DECL], "demand") is None
    assert covers([{**DECL, "lanes": "*"}], "demand") is not None and covers([], "records") is None


def test_check_lanes_takes_a_declared_lane_as_a_note_and_the_rest_as_before() -> None:
    problems, notes = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, [DECL])
    assert problems == [] and "declared unchanged since 11111111 by scott" in notes[0]
    problems, notes = check_lanes("s", {"demand": "new"}, {"demand": "old"}, ["demand"], False, [DECL])
    assert notes == [] and "schema changed" in problems[0]
    # FALSIFIER: no declaration, or a current image with no digests at all, refuses exactly as before.
    problems, _ = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, [])
    assert "schema changed" in problems[0]
    problems, _ = check_lanes("s", {"records": "new"}, None, ["records"], False, [DECL])
    assert problems == []  # the declaration covers "recorded no digests" too: the inputs still fit


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str, str]:
    """Commits A < S < T on one line; the declaration lands at S."""
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": os.environ["PATH"]}  # fmt: skip

    def git(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(tmp_path), *args], env=env, check=True, capture_output=True, text=True)  # noqa: S603 - argv list of fixed git args, never a shell
        return out.stdout.strip()

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "A")
    a = git("rev-parse", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "S")
    s = git("rev-parse", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "T")
    t = git("rev-parse", "HEAD")
    path = tmp_path / ACCEPTED_LANE_INPUTS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        f"- since: {s}\n  lanes: '*'\n  by: scott\n  date: '2026-11-04'\n  why: names only\n- since: nonsense\n  lanes: '*'\n"
    )
    return tmp_path, a, s, t


def test_a_declaration_applies_between_its_commit_and_the_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real git. FALSIFIER: drop the ancestry check and a seat already past the change, or a release
    from before it, is covered too."""
    repo, a, s, t = _repo(tmp_path, monkeypatch)
    decl = LaneInputDeclarations(CommandRunner(), repo)
    assert [d["since"] for d in decl.load()] == [s]  # the malformed entry is dropped
    assert len(decl.applying(t, a)) == 1
    assert len(decl.applying(s, a)) == 1  # built at the change itself
    assert decl.applying(a, a) == []  # the release predates the change
    assert decl.applying(t, s) == [] and decl.applying(t, t) == []  # the seat already has it
    assert decl.applying(t, None) == [] and decl.applying(None, a) == []
    assert decl.applying(t, "0" * 40) == []  # a commit this clone never saw is not before the change
    assert LaneInputDeclarations(CommandRunner(), tmp_path / "nowhere").load() == []


def test_the_checker_passes_a_declared_change_and_refuses_an_undeclared_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, a, s, t = _repo(tmp_path, monkeypatch)
    r2 = FakeR2()
    r2.put_json("vaults/smd-staging/records/records-firm.yaml", {})
    r2.put_json("vaults/smd-staging/demand/demand-firm.yaml", {})
    r2.put_json(image_key(DIGEST, "required-env.json"), {"required": []})
    r2.put_json(image_key(DIGEST, "lane-inputs.json"), {"records": "new", "demand": "d-new"})
    r2.put_json(image_key(OLD_DIGEST, "lane-inputs.json"), {"records": "old", "demand": "d-new"})
    fly = FakeFly()
    fly.secrets["hermes-smd-staging"] = (set(), set())
    checker = PreflightChecker(fly, r2, LaneInputDeclarations(CommandRunner(), repo))  # type: ignore[arg-type]
    ok = checker.run("smd-staging", release(ss_console_sha=t), "", OLD_DIGEST, False, current_sha=a)
    assert ok.ok and any("declared unchanged since" in n for n in ok.notes)
    past = checker.run("smd-staging", release(ss_console_sha=t), "", OLD_DIGEST, False, current_sha=s)
    assert not past.ok and "lane records" in past.problems[0]
    unknown = checker.run("smd-staging", release(ss_console_sha=t), "", OLD_DIGEST, False, current_sha=None)
    assert not unknown.ok
    bare = PreflightChecker(fly, r2)  # type: ignore[arg-type]
    assert not bare.run("smd-staging", release(ss_console_sha=t), "", OLD_DIGEST, False, current_sha=a).ok


def test_the_repo_declaration_for_adr_0103_is_well_formed() -> None:
    repo = Path(__file__).resolve().parents[3]
    entries = LaneInputDeclarations(CommandRunner(), repo).load()
    r1 = [d for d in entries if str(d["since"]).startswith("a137f20f")]
    assert len(r1) == 1 and r1[0]["lanes"] == "*" and r1[0]["why"] and r1[0]["by"]
