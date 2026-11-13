"""The integrity job, end to end, on a scratch repository made from this tree
(``git archive HEAD operator``), for the text-rule set:

* **Introduced by the PR.** The base commit has no text-rule goldens or kit;
  the PR adds them. Nothing is regenerated against the base. With a golden that
  disagrees with the PR's own code, the job exits 1 (the set's own tests fail on
  the PR's tree); with the goldens as captured, it exits 0 and prints a NOTICE.
* **Present in the base.** With the base holding the set, a control PR commit
  that touches nothing the goldens measure exits 0, and a PR that changes how
  the proof checks write a cents figure (``jobs.proof.checks.fmt_cents``) AND
  rewrites the goldens to agree exits 1, naming the text-rule paths: the base's
  behaviour, regenerated, no longer equals what the PR committed.
"""

from __future__ import annotations

import os
import shutil
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
#: The text-rule set and its kit, under ``operator/``.
TEXT_RULES = (
    "runners/jobs/tests/goldens/text_rules",
    "runners/jobs/tests/test_text_rule_goldens.py",
    "runners/jobs/tests/text_rule_kit.py",
    "runners/jobs/tests/text_rule_harvest.py",
)
SYNTHETIC = "runners/jobs/tests/goldens/text_rules/synthetic.json"


def _git(root: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603 - fixed git argv in a scratch repo


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _scratch(tmp: Path) -> tuple[Path, str]:
    """A repo whose first commit is this tree WITHOUT the text-rule set (kept aside in ``tmp/aside``)."""
    root = tmp / "repo"
    root.mkdir()
    raw = subprocess.run(["git", "archive", "HEAD", "operator"], cwd=REPO, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(root)], input=raw, check=True)  # noqa: S603 - this repo's own archive
    for rel in TEXT_RULES:
        _move(root / "operator" / rel, tmp / "aside" / rel)
    _git(root, "init", "--quiet", "-b", "main")
    return root, _commit(root, "base without the text-rule set")


def _env(root: Path) -> dict[str, str]:
    op = root / "operator"
    paths = [op / "runners/jobs", op / "connectors/_sdk", op / "connectors/_contract", op / "connectors/smokeball", op]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(map(str, paths))}


def _integrity(root: Path, base: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "bin/goldens/golden-integrity.py", "--base", base, "--python", sys.executable]
    return subprocess.run(  # noqa: S603 - the scratch repo's own integrity script
        [*argv, "--only", "text_rules"], cwd=root / "operator", capture_output=True, text=True, env=_env(root)
    )


def _introduce(root: Path, tmp: Path, base: str) -> str:
    """The PR adds the set: first with a golden its code disagrees with (exit 1), then as captured (exit 0)."""
    op = root / "operator"
    for rel in TEXT_RULES:
        _move(tmp / "aside" / rel, op / rel)
    good = (op / SYNTHETIC).read_text(encoding="utf-8")
    (op / SYNTHETIC).write_text(good.replace('"$1,234,567.89"', '"$1,234,567.80"', 1), encoding="utf-8")
    assert (op / SYNTHETIC).read_text(encoding="utf-8") != good
    _commit(root, "introduce the text-rule set, one answer wrong")
    bad = _integrity(root, base)
    assert bad.returncode == 1, bad.stdout[-3000:]
    assert "text_rules: introduced by this PR, and its own tests fail" in bad.stdout
    (op / SYNTHETIC).write_text(good, encoding="utf-8")
    head = _commit(root, "introduce the text-rule set")
    ok = _integrity(root, base)
    assert ok.returncode == 0, ok.stdout[-3000:] + ok.stderr[-3000:]
    assert "NOTICE text_rules: introduced by this PR" in ok.stdout
    return head


def test_an_introduced_set_runs_its_own_tests_and_a_present_set_is_held_to_the_base(tmp_path):
    root, base = _scratch(tmp_path)
    base = _introduce(root, tmp_path, base)
    op = root / "operator"
    # Control: a PR commit the goldens do not measure passes against a base that has the set.
    (op / "runners/jobs/README-e2e.txt").write_text("unrelated\n", encoding="utf-8")
    _commit(root, "unrelated")
    control = _integrity(root, base)
    assert control.returncode == 0, control.stdout[-3000:] + control.stderr[-3000:]
    assert "introduced" not in control.stdout
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
    assert "USD " in (op / SYNTHETIC).read_text(encoding="utf-8")
    _commit(root, "change fmt_cents and rewrite its golden")
    planted_run = _integrity(root, base)
    assert planted_run.returncode == 1, planted_run.stdout[-3000:]
    assert "text_rules/synthetic: behaviour:" in planted_run.stdout
    assert "text_rules/jobs.proof.checks: behaviour:" in planted_run.stdout
