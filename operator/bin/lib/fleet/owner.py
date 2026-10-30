"""Who set a fleet hold: the session, not only the login.

On 2026-10-30 ashton-price sat held while several sessions waited for "the
hold" to lift, and nobody could say whose it was: every laptop session records
as the same login, so ``held_by`` read ``scottdurgan`` and named no one. The
session board (``~/.claude/ss-board/``, one record per worktree, written by
``.claude/bin/mission``) already knows which session runs in which worktree and
what it is for, so a hold names that session and its mission line.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
from pathlib import Path

MISSION_CHARS = 120


def login() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - a username only labels the hold row, never a reason to stop
        return os.environ.get("USER", "unknown")


def _toplevel(cwd: str) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 - fixed git argv, never a shell
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def board_session(cwd: str, board_dir: Path) -> tuple[str, str] | None:
    """(session name, mission line) for the board record of ``cwd``'s worktree, if there is one."""
    top = _toplevel(cwd)
    if top is None or not board_dir.is_dir():
        return None
    for path in board_dir.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("worktree") == top:
            return os.path.basename(top), str(rec.get("mission") or "")[:MISSION_CHARS]
    return None


def held_by(cwd: str | None = None, board_dir: Path | None = None, env: dict[str, str] | None = None) -> str:
    """The ``held_by`` a new hold records: the session and what it is doing, then the login.

    A GitHub run names its run. A laptop shell outside any board session says
    so in words, so the gap is visible rather than read as a name.
    """
    env = dict(os.environ) if env is None else env
    if env.get("GITHUB_RUN_ID"):
        return f"github-actions run {env['GITHUB_RUN_ID']} ({env.get('GITHUB_WORKFLOW', 'workflow')})"
    found = board_session(cwd or os.getcwd(), board_dir or Path.home() / ".claude" / "ss-board")
    who = login()
    if found is None:
        return f"{who}, from no board session (a shell outside any Claude session's worktree)"
    name, mission = found
    return f"session {name} ({who}): {mission}" if mission else f"session {name} ({who})"
