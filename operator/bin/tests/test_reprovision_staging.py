"""reprovision-staging.sh provisions only a release twin, and isolates its client's secrets (ADR 0107).

Runs the wrapper with REPROVISION_STAGING_DRY_RUN set, which stops after the
refusal and the isolate set, before anything reaches Infisical or Fly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from bin.lib import seat_purpose
from bin.lib.secret_custody import _seat_suffix

SCRIPT = Path(__file__).resolve().parents[1] / "reprovision-staging.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "REPROVISION_STAGING_DRY_RUN": "1", "REPROVISION_STAGING_PYTHON": sys.executable}
    argv = ["bash", str(SCRIPT), *args]
    return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=120)  # noqa: S603 - the repo's own script, fixed argv


def test_with_no_argument_it_takes_the_one_declared_twin_and_isolates_its_client() -> None:
    # FALSIFIER: isolate only the twin's own per-seat names and the client's
    # ANTHROPIC_API_KEY__<CLIENT> is left in scope on the twin's provision.
    twin = seat_purpose.release_twins()[0]
    client = seat_purpose.twin_of(twin)
    res = _run()
    assert res.returncode == 0, res.stderr
    names = set(res.stdout.split())
    assert f"ANTHROPIC_API_KEY__{_seat_suffix(twin)}" in names
    assert f"ANTHROPIC_API_KEY__{_seat_suffix(client)}" in names
    assert f"is the release twin of {client}" in res.stderr


def test_it_refuses_every_seat_that_is_not_a_release_twin() -> None:
    for slug, entry in seat_purpose.load().items():
        if entry["purpose"] == "release-twin":
            continue
        res = _run(slug)
        assert res.returncode == 1 and f"refusing {slug}" in res.stderr, (slug, res.stderr)
        assert res.stdout == ""


def test_it_refuses_an_unknown_slug_and_extra_arguments() -> None:
    assert _run("no-such-seat").returncode == 1
    assert _run("a", "b").returncode == 2
