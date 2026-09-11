"""Regression pins for bin/connect-smokeball.sh (ss#2149 / ss#2171 PR C).

The script's two refusal branches were live-proven at merge (#2184:
vfy_01KZ20HF7AMCCZTT0N49WJVNAR currency-refusal, #2192 rescoped) but carried
ZERO test coverage — a regression in the seat-currency gate would have gone
uncaught until the next live connect attempt. These tests pin the gate
hermetically by stubbing `fly` on PATH; `git show origin/main:...` runs
against the real repo (CI checkouts carry origin).

What is pinned:
  * arg validation (usage, slug shape, missing customer.yaml)
  * currency REFUSAL: seat ref != origin/main pin -> exit 3, "REFUSED"
  * fail-closed: unreadable seat ref -> exit 3 (a seat whose gateway is down
    must not receive a token)
  * currency PASS falls through to the next gate (missing env -> exit 2),
    proving the gate passes on a matching ref rather than refusing everything
    (Law 12: the check must be able to pass, or the refusal tests measure
    nothing).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "bin" / "connect-smokeball.sh"

# A syntactically-valid 40-hex ref that will never equal a real pin.
_STALE_REF = "deadbeef" * 5


def _origin_main_overlay_ref() -> str:
    """Read the pin exactly the way the script does."""
    out = subprocess.run(  # noqa: S603 - literal git argv in a test, no shell; the path is the repo root
        ["git", "-C", str(REPO_ROOT), "show", "origin/main:operator/contracts/overlay-pairs.json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return str(json.loads(out)["overlayRef"])


def _write_fly_stub(bin_dir: Path, ref_output: str | None) -> None:
    """A fake `fly` that prints ``ref_output`` for the ssh env probe (or
    nothing at all when ``ref_output`` is None — the unreadable-seat case)."""
    stub = bin_dir / "fly"
    body = "#!/usr/bin/env bash\n"
    if ref_output is not None:
        body += f"echo '{ref_output}'\n"
    body += "exit 0\n"
    stub.write_text(body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _run(args: list[str], bin_dir: Path | None = None, env_extra: dict | None = None):
    env = dict(os.environ)
    # The env gate must see ABSENCE, not inherited operator creds.
    env.pop("OPERATOR_OAUTH_STATE_MASTER", None)
    env.pop("SMOKEBALL_STAGING_CLIENT_ID", None)
    env.pop("SMOKEBALL_PROD_CLIENT_ID", None)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(  # noqa: S603 - test runs the script under bash with test-authored args, no shell
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_no_args_is_usage_error() -> None:
    result = _run([])
    assert result.returncode == 1
    assert "Usage" in result.stderr or "usage" in result.stderr.lower()


def test_invalid_slug_refused() -> None:
    result = _run(["Bad_Slug!"])
    assert result.returncode == 1
    assert "invalid slug" in result.stderr


def test_missing_customer_yaml_refused() -> None:
    result = _run(["no-such-customer"])
    assert result.returncode == 1
    assert "no customer.yaml" in result.stderr


def test_stale_seat_is_refused_exit_3(tmp_path: Path) -> None:
    _write_fly_stub(tmp_path, _STALE_REF)
    result = _run(["pilot-smokeball"], bin_dir=tmp_path)
    assert result.returncode == 3, result.stderr
    assert "REFUSED" in result.stderr
    assert "reprovision.sh pilot-smokeball" in result.stderr  # remediation printed


def test_unreadable_seat_ref_fails_closed_exit_3(tmp_path: Path) -> None:
    _write_fly_stub(tmp_path, None)  # gateway down / env unreadable
    result = _run(["pilot-smokeball"], bin_dir=tmp_path)
    assert result.returncode == 3, result.stderr
    assert "cannot verify" in result.stderr


def test_current_seat_passes_gate_and_stops_at_env_check(tmp_path: Path) -> None:
    """The pass direction (Law 12): a seat on the pinned ref clears the gate —
    the run proceeds to the yaml/env stage and fails there on MISSING env
    (exit 2), never on the currency gate (exit 3)."""
    _write_fly_stub(tmp_path, _origin_main_overlay_ref())
    result = _run(["pilot-smokeball"], bin_dir=tmp_path)
    assert "proceeding" in result.stderr  # the gate's success line
    assert result.returncode == 2, result.stderr
    assert "missing required env" in result.stderr
