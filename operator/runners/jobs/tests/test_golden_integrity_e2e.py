"""The integrity job, end to end: a PR that changes runner code AND rewrites
the golden to match must fail it.

A scratch repository is made from this tree (``git archive HEAD operator``):
the base commit as it is, then a PR commit that changes how the proof checks
write a cents figure (``jobs.proof.checks.fmt_cents``) and regenerates the
text-rule goldens to agree. Every golden test in the PR commit passes; the
integrity job, run from the scratch PR commit with ``--base`` the scratch base,
regenerates the goldens from the base code and must exit non-zero, naming the
text-rule paths. A control PR commit that touches nothing the goldens measure
must exit zero, so the failure is the planted change and not the scratch setup.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


TESTS = Path(__file__).resolve().parent
OPERATOR = TESTS.parents[2]
REPO = OPERATOR.parent
GIT = [
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "user.name=golden-e2e",
    "-c",
    "user.email=golden-e2e@example.test",
]


def _git(root: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603 - fixed git argv in a scratch repo


def _scratch(tmp: Path) -> tuple[Path, str]:
    root = tmp / "repo"
    root.mkdir()
    raw = subprocess.run(["git", "archive", "HEAD", "operator"], cwd=REPO, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(root)], input=raw, check=True)  # noqa: S603 - this repo's own archive
    _git(root, "init", "--quiet", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _env(root: Path) -> dict[str, str]:
    op = root / "operator"
    paths = [op / "runners/jobs", op / "connectors/_sdk", op / "connectors/_contract", op / "connectors/smokeball", op]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(map(str, paths))}


def _integrity(root: Path, base: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "bin/goldens/golden-integrity.py", "--base", base, "--python", sys.executable]
    return subprocess.run(  # noqa: S603 - the scratch repo's own integrity script
        [*argv, "--only", "text_rules"], cwd=root / "operator", capture_output=True, text=True, env=_env(root)
    )


def test_a_code_change_with_a_rewritten_golden_fails_the_integrity_job(tmp_path):
    root, base = _scratch(tmp_path)
    op = root / "operator"
    # Control: a PR commit the goldens do not measure passes.
    (op / "runners/jobs/README-e2e.txt").write_text("unrelated\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "unrelated")
    control = _integrity(root, base)
    assert control.returncode == 0, control.stdout[-3000:] + control.stderr[-3000:]
    # The planted PR: the code changes and the golden is rewritten to agree.
    checks = op / "runners/jobs/jobs/proof/checks.py"
    text = checks.read_text(encoding="utf-8")
    planted = 'return f"${c // 100:,}.{c % 100:02d}"'
    assert planted in text
    checks.write_text(text.replace(planted, 'return f"USD {c // 100:,}.{c % 100:02d}"'), encoding="utf-8")
    env = {**_env(root), "JOBS_WRITE_KIT_GOLDENS": "1"}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-c", "runners/jobs/pyproject.toml", "runners/jobs/tests/test_text_rule_goldens.py",
         "-q", "-p", "no:cacheprovider", "-k", "not planted"],
        cwd=op, env=env, check=True, capture_output=True,
    )  # fmt: skip
    assert "USD " in (op / "runners/jobs/tests/goldens/text_rules/synthetic.json").read_text(encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "change fmt_cents and rewrite its golden")
    planted_run = _integrity(root, base)
    assert planted_run.returncode == 1, planted_run.stdout[-3000:]
    assert "text_rules/synthetic: behaviour:" in planted_run.stdout
    assert "text_rules/jobs.proof.checks: behaviour:" in planted_run.stdout
