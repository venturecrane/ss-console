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
    assert _run("--pre-flip").returncode == 2
    assert _run("--pre-flip", "a", "b").returncode == 2


_SANDBOX = ("SMOKEBALL_STAGING_CLIENT_ID", "SMOKEBALL_STAGING_CLIENT_SECRET", "SMOKEBALL_STAGING_API_KEY")
_PROD = ("SMOKEBALL_PROD_CLIENT_ID", "SMOKEBALL_PROD_CLIENT_SECRET", "SMOKEBALL_PROD_API_KEY")


def test_a_release_twin_keeps_smds_sandbox_credentials_and_blanks_the_production_app() -> None:
    # A twin authored `environment: staging` reads SMOKEBALL_STAGING_* in
    # bin/lib/stage-smokeball.sh; blanking them booted the twin with no
    # Smokeball. FALSIFIER: put the three back in secret_custody._CUSTOMER_EXACT
    # (the isolate set's source) and they reappear here.
    res = _run()
    assert res.returncode == 0, res.stderr
    names = set(res.stdout.split())
    assert not names & set(_SANDBOX), names & set(_SANDBOX)
    assert set(_PROD) <= names


def test_pre_flip_isolates_the_declared_client_and_keeps_the_twins_own_names() -> None:
    # ap-staging is `internal` until its flip; twin.yaml declares ashton-price.
    # FALSIFIER: drop twin_of from operator/customers/ap-staging/twin.yaml and the
    # run refuses; isolate the twin's own names and ANTHROPIC_API_KEY__AP_STAGING
    # (the new app's only source for its model key) is blanked.
    assert seat_purpose.purpose_of("ap-staging") == "internal"
    client = seat_purpose.declared_twin_of("ap-staging")
    res = _run("--pre-flip", "ap-staging")
    assert res.returncode == 0, res.stderr
    names = set(res.stdout.split())
    assert f"ANTHROPIC_API_KEY__{_seat_suffix(client)}" in names
    assert f"ANTHROPIC_API_KEY__{_seat_suffix('ap-staging')}" not in names
    assert set(_PROD) <= names and not names & set(_SANDBOX)
    assert "MSGRAPH_CLIENT_SECRET" in names  # the bare name; the per-seat one survives
    assert f"exact twin of {client} (pre-flip)" in res.stderr


def test_pre_flip_refuses_a_seat_that_is_not_an_internal_declared_twin() -> None:
    for slug, entry in seat_purpose.load().items():
        if slug == "ap-staging":
            continue
        res = _run("--pre-flip", slug)
        assert res.returncode == 1 and f"refusing --pre-flip {slug}" in res.stderr, (slug, entry, res.stderr)
        assert res.stdout == ""


def test_the_plain_reprovision_refuses_an_exact_twin() -> None:
    # reprovision.sh injects /ss un-isolated; an exact twin goes through the
    # wrapper above. The refusal runs before the busy gate, so nothing is called.
    script = SCRIPT.parent / "reprovision.sh"
    res = subprocess.run(  # noqa: S603 - the repo's own script, fixed argv
        ["bash", str(script), "ap-staging"], capture_output=True, text=True, timeout=60
    )
    assert res.returncode == 1 and "refusing ap-staging" in res.stderr, res.stderr
