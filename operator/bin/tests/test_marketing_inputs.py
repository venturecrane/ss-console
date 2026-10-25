"""A marketing seat's advertising-gate registers: checked before upload, fetched only on a marketing seat.

Two halves of the lane input (ADR 0098):

* ``bin/lib/check-marketing-registers.py``, run by ``lib/stage-marketing-inputs.sh``
  before the registers reach R2, refuses exactly what the gate on the seat would
  refuse (it loads the gate's own ``advertising_gate_registers.py``).
* ``templates/entrypoint-marketing.sh`` decides from the live customer.yaml
  whether this seat fetches registers at all, so the other seats do not pay a
  retried fetch of a prefix they never author; a config it cannot read still
  fetches.

FALSIFIER: make the checker return 0 on a load error and the malformed case
passes; flip the role test in the entrypoint (or treat a crash as "not
marketing") and the role-gate cases fail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPERATOR = Path(__file__).resolve().parents[2]
CHECKER = OPERATOR / "bin" / "lib" / "check-marketing-registers.py"
EXAMPLE = OPERATOR / "templates" / "drafting" / "fixtures" / "marketing-registers.example.yaml"
ENTRYPOINT = OPERATOR / "templates" / "entrypoint-marketing.sh"


def _check(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the repo's checker under sys.executable with a test-authored path
        [sys.executable, str(CHECKER), str(path)], capture_output=True, text=True, check=False
    )


def test_the_example_registers_pass():
    res = _check(EXAMPLE)
    assert res.returncode == 0, res.stderr
    assert "marketing registers OK" in res.stdout


def test_malformed_registers_are_refused(tmp_path):
    bad = tmp_path / "marketing-firm.yaml"
    bad.write_text("schema: 2\nfirm: {}\n", encoding="utf-8")
    res = _check(bad)
    assert res.returncode == 1
    assert "schema must be 1" in res.stderr


def test_a_missing_file_is_refused(tmp_path):
    res = _check(tmp_path / "absent.yaml")
    assert res.returncode == 1
    assert "cannot read" in res.stderr


def _run_entrypoint(tmp_path: Path, customer_yaml: str | None) -> str:
    """Source entrypoint-marketing.sh with stubs; return what it logged."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    venv_python = tmp_path / "opt/hermes/.venv/bin/python3"
    venv_python.parent.mkdir(parents=True)
    # A wrapper, not a symlink: a symlinked venv interpreter loses its venv
    # (no pyvenv.cfg beside the link) and with it PyYAML.
    venv_python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    venv_python.chmod(0o755)
    config = tmp_path / "config"
    config.mkdir()
    live = config / "customer.yaml"
    if customer_yaml is not None:
        live.write_text(customer_yaml, encoding="utf-8")
    src = ENTRYPOINT.read_text(encoding="utf-8").replace("/opt/hermes/.venv/bin/python3", str(venv_python))
    script = tmp_path / "entrypoint-marketing.sh"
    script.write_text(src, encoding="utf-8")
    harness = f"""
set -u
log() {{ echo "LOG $*"; }}
chown() {{ :; }}
_fetch_lane_inputs() {{ echo "FETCH $1"; _FETCH_INPUTS_STATE=absent; return 1; }}
CONFIG_DIR={config}
LIVE_CUSTOMER_YAML={live}
CUSTOMER_SLUG=acme
. {script}
"""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    res = subprocess.run(  # noqa: S603 - literal bash argv; the harness is this test's own text
        ["bash", "-c", harness], capture_output=True, text=True, env=env, check=False
    )
    assert res.returncode == 0, res.stderr
    return res.stdout


needs_yaml = pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None, reason="the seat's interpreter carries PyYAML"
)


@needs_yaml
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash")
def test_a_marketing_seat_fetches(tmp_path):
    out = _run_entrypoint(tmp_path, "seat:\n  kind: customer\n  product: operator\n  role: marketing\n")
    assert "FETCH marketing" in out
    assert "advertising gate refuses every marketing draft" in out


@needs_yaml
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash")
def test_another_seat_does_not_fetch_and_keeps_no_copy(tmp_path):
    stale = tmp_path / "config" / "marketing"
    out = _run_entrypoint(tmp_path, "seat:\n  kind: customer\n  product: operator\n")
    assert "FETCH" not in out
    assert not stale.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash")
def test_a_config_that_cannot_be_read_still_fetches(tmp_path):
    out = _run_entrypoint(tmp_path, None)
    assert "FETCH marketing" in out
