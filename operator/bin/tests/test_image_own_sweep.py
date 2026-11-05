"""The seat image's closing ownership sweep is `chmod -R a+rX`, entry by entry.

The Dockerfile's last ownership step used to be `chmod -R a+rX ... && chown -R hermes:hermes ...` over
1.6 GB, re-run on every release (2026-11-05: two of three image builds lost the builder at that step).
It now changes only entries that are not already right. This test runs the shipped find expression
against the command it replaced, on a tree of every mode.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO / "operator" / "templates" / "Dockerfile"


def _sweep() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    block = re.search(r"# OWN-SWEEP-BEGIN\nRUN (.*?)\n# OWN-SWEEP-END", text, re.DOTALL)
    assert block, "the closing ownership sweep is not between its markers"
    return " ".join(block.group(1).replace("\\\n", " ").split())


def _tree(root: Path) -> None:
    root.mkdir()
    file_modes = [
        0o000,
        0o100,
        0o010,
        0o001,
        0o400,
        0o440,
        0o444,
        0o445,
        0o454,
        0o544,
        0o600,
        0o640,
        0o644,
        0o650,
        0o700,
        0o711,
        0o750,
        0o755,
    ]
    for d_mode in (0o700, 0o711, 0o744, 0o750, 0o754, 0o755):
        d = root / f"d{d_mode:o}"
        d.mkdir()
        for f_mode in file_modes:
            f = d / f"f{f_mode:03o}"
            f.write_text("x")
            os.chmod(f, f_mode)
        (d / "link").symlink_to("f000")
        os.chmod(d, d_mode)


def _modes(root: Path) -> dict[str, int]:
    out = {".": stat.S_IMODE(os.lstat(root).st_mode)}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(base) / name
            out[str(p.relative_to(root))] = stat.S_IMODE(os.lstat(p).st_mode)
    return out


def test_the_closing_sweep_leaves_the_modes_chmod_R_a_plus_rX_leaves(tmp_path: Path) -> None:
    """FALSIFIER: drop any of the three clauses from the Dockerfile's find (missing read, a directory
    missing execute, an executable file missing execute for someone) and a mode here differs."""
    sweep = _sweep()
    mode_cmd, owner_cmd = sweep.split(" && ")
    assert mode_cmd.startswith("find /opt/hermes /app ! -type l ") and mode_cmd.endswith("-exec chmod a+rX {} +")
    a, b = tmp_path / "a", tmp_path / "b"
    _tree(a)
    _tree(b)  # built the same way twice: a file nobody may read cannot be copied without root
    before = _modes(b)
    assert before == _modes(a)
    subprocess.run(["chmod", "-R", "a+rX", str(a)], check=True)  # noqa: S603 - fixed argv in a test
    argv = shlex.split(mode_cmd.replace("/opt/hermes /app", shlex.quote(str(b)), 1))
    subprocess.run(argv, check=True)  # noqa: S603 - the Dockerfile's own find, on a temp tree
    assert _modes(b) == _modes(a)
    assert _modes(b) != before  # the tree did need changing: the comparison is not vacuous
    # Idempotent, and a tree already right is not touched at all (no entry matches the predicate).
    probe = shlex.split(
        mode_cmd.replace("/opt/hermes /app", shlex.quote(str(b)), 1).replace("-exec chmod a+rX {} +", "-print")
    )
    assert subprocess.run(probe, check=True, capture_output=True, text=True).stdout == ""  # noqa: S603


def test_the_closing_sweep_owns_the_same_trees_the_recursive_chown_did() -> None:
    """The owner half cannot run without root; its text is pinned instead. FALSIFIER: drop a tree, or
    -h (a recursive chown changes a symlink itself, never its target)."""
    owner_cmd = _sweep().split(" && ")[1].split()
    assert owner_cmd[:6] == [
        "find",
        "/opt/hermes/.venv",
        "/opt/hermes/ui-tui",
        "/opt/hermes/node_modules",
        "/app",
        "/opt/data",
    ]
    assert " ".join(owner_cmd[6:]) == r"\( ! -user hermes -o ! -group hermes \) -exec chown -h hermes:hermes {} +"


def test_the_big_trees_are_swept_once_above_everything_that_changes_per_release() -> None:
    """The early sweep must sit above the first COPY of the overlay, or every release re-runs it.
    FALSIFIER: move it below `COPY operator/hermes-smd-overlay/` and this fails."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    early = text.index("RUN chmod -R a+rX /opt/hermes \\\n && chown -R hermes:hermes")
    assert early < text.index("COPY operator/hermes-smd-overlay/") < text.index("# OWN-SWEEP-BEGIN")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert code.count("chown -R hermes:hermes") == 1, "one recursive sweep of the big trees, the early one"
