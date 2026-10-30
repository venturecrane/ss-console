"""A fleet hold names the session that set it (owner.py, 2026-10-30).

Each test names its falsifier: the one change to the code that turns it red.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bin.lib.fleet import owner, status


def _git_tree(path: Path) -> Path:
    path.mkdir(parents=True)
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(path)}  # no inherited GIT_* (pre-push sets them)
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)  # noqa: S603 - fixed git argv, never a shell
    argv = ["git", "-C", str(path), "rev-parse", "--show-toplevel"]
    out = subprocess.run(argv, capture_output=True, text=True, check=True, env=env)  # noqa: S603 - fixed git argv, never a shell
    return Path(out.stdout.strip())


def test_a_hold_from_a_board_session_names_it_and_its_mission(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path / "worktrees" / "sos-2026-10-30-x")
    board = tmp_path / "board"
    board.mkdir()
    (board / "wt-1.json").write_text(json.dumps({"worktree": str(tree), "mission": "A&P gets its print-out"}))
    (board / "wt-2.json").write_text(json.dumps({"worktree": "/elsewhere", "mission": "other"}))
    (board / "bounce-1").write_text("not json")
    got = owner.held_by(cwd=str(tree), board_dir=board, env={})
    # FALSIFIER: record only the login (the 10-30 hold read "scottdurgan") and the session is gone.
    assert got.startswith("session sos-2026-10-30-x (") and got.endswith("): A&P gets its print-out")


def test_a_hold_from_outside_any_session_says_so(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path / "plain")
    got = owner.held_by(cwd=str(tree), board_dir=tmp_path / "no-board", env={})
    assert "from no board session" in got  # FALSIFIER: fall back silently and the gap reads as a name


def test_a_hold_from_ci_names_the_run() -> None:
    got = owner.held_by(env={"GITHUB_RUN_ID": "41117208234", "GITHUB_WORKFLOW": "release-tick"})
    assert got == "github-actions run 41117208234 (release-tick)"


def test_status_lists_each_hold_with_who_when_and_why() -> None:
    text = status.render_holds({
        "ashton-price": {"held_by": "session sos-2026-10-30-x (scott)", "held_at": "2026-10-30T15:42:08Z",
                         "reason": "print-out hotfix. Lift when: #3625 is proven on pilot"},
    })  # fmt: skip
    assert "ashton-price: held 2026-10-30T15:42:08Z by session sos-2026-10-30-x" in text
    assert "Lift when: #3625 is proven on pilot" in text
    assert status.render_holds({}).strip() == "holds: none"
