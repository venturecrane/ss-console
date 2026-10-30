"""A cancelled release run frees its leases (2026-10-30).

A release-tick run cancelled at 21:37Z died holding A&P's seat lease. Python's
default SIGTERM ran no ``__exit__``, and the workflow shell never passed the
runner's signal to fleet at all, so the lease sat in D1 for its 2 h TTL and every
tick recorded A&P waiting_idle while nothing reached A&P or the seats behind it.
These run real processes and send real signals: a fake would prove nothing about
either half.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2]
REPO = OPERATOR.parent
WORKFLOW = REPO / ".github/workflows/release-tick.yml"


def _wait_for(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path.name} never appeared")
        time.sleep(0.05)


def _holder(tmp_path: Path, install: bool) -> subprocess.Popen[bytes]:
    """A process holding a 'lease' whose __exit__ writes a file, as SeatLease's deletes the row."""
    script = tmp_path / "hold.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, time
            from pathlib import Path
            from bin.lib.fleet import cli

            class Lease:
                def __enter__(self):
                    Path({str(tmp_path / "ready")!r}).write_text("1")
                def __exit__(self, *exc):
                    Path({str(tmp_path / "released")!r}).write_text("1")

            if {install!r}:
                cli.install_signal_exits()
            with Lease():
                time.sleep(30)
            """
        )
    )
    env = {**os.environ, "PYTHONPATH": str(OPERATOR)}
    return subprocess.Popen([sys.executable, str(script)], cwd=OPERATOR, env=env)  # noqa: S603 - a tmp_path script under sys.executable


def test_sigterm_releases_a_held_lease(tmp_path: Path) -> None:
    proc = _holder(tmp_path, install=True)
    _wait_for(tmp_path / "ready")
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 128 + signal.SIGTERM
    assert (tmp_path / "released").exists()


def test_without_the_handler_sigterm_leaks_the_lease(tmp_path: Path) -> None:
    """The falsifier: the same holder without install_signal_exits leaks, so the test above can fail."""
    proc = _holder(tmp_path, install=False)
    _wait_for(tmp_path / "ready")
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == -signal.SIGTERM
    assert not (tmp_path / "released").exists()


def _run_fleet_function() -> str:
    text = WORKFLOW.read_text()
    m = re.search(r"^( +)run_fleet\(\) \{\n.*?^\1\}\n", text, re.S | re.M)
    assert m, "release-tick.yml lost its run_fleet signal forwarder"
    return textwrap.dedent(m.group(0))


def test_the_workflow_shell_forwards_cancellation_to_fleet(tmp_path: Path) -> None:
    """The runner signals the step's bash; run_fleet must hand fleet a SIGTERM and return its exit."""
    fake = tmp_path / "operator/bin/fleet"
    fake.parent.mkdir(parents=True)
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"trap 'echo 1 > {tmp_path}/got-term; exit 143' TERM\n"
        f"echo 1 > {tmp_path}/ready\n"
        "while :; do sleep 0.1; done\n"
    )
    fake.chmod(0o755)
    step = tmp_path / "step.sh"
    step.write_text("set -euo pipefail\n" + _run_fleet_function() + "set +e\nrun_fleet release latest --tick\nrc=$?\n"
                    f"echo \"$rc\" > {tmp_path}/rc\n")  # fmt: skip
    proc = subprocess.Popen(["bash", str(step)], cwd=tmp_path, start_new_session=True)  # noqa: S603 - the workflow function in a tmp_path script
    _wait_for(tmp_path / "ready")
    os.kill(proc.pid, signal.SIGINT)  # what the runner sends first, to the step's shell only
    proc.wait(timeout=10)
    assert (tmp_path / "got-term").exists()
    assert (tmp_path / "rc").read_text().strip() == "143"
