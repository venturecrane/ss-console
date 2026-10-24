"""The lane fetch records its verdict where the in-image self-test reads it.

``entrypoint-fetch-inputs.sh`` is SOURCED here and ``_fetch_lane_inputs`` run
against a fake ``aws`` whose answers each case scripts. ``install`` and
``sleep`` are shimmed (an unprivileged runner cannot ``install -o root``, and
the retry backoff would cost 15 s a case). The state file
``<SMD_LANE_INPUTS_STATE_DIR>/<lane>.inputs`` is what ``selftest.sh
lane_authored`` narrows on: only a clean ``absent`` turns a lane's presence
check off, so the fetch must never write ``absent`` for a vault it could not
list (the 2026-10-14 A&P v141 falsifier), and a stale earlier verdict must not
survive a boot whose write failed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "operator" / "templates" / "entrypoint-fetch-inputs.sh"

# cp: "ok" lands the marker, anything else fails. ls of the marker key and of
# the slug prefix answer from their own knobs.
AWS_SHIM = """#!/bin/sh
case "$2" in
  cp)
    dest=""; for a in "$@"; do dest="$a"; done
    if [ "$FAKE_CP" = ok ]; then mkdir -p "$dest" && echo x > "$dest/demand-firm.yaml"; exit 0; fi
    echo "fake cp failed" >&2; exit 1 ;;
  ls)
    for a in "$@"; do last="$a"; done
    case "$last" in
      */demand-firm.yaml) [ "$FAKE_LS_MARKER" = ok ] && exit 0; exit 1 ;;
      *) [ "$FAKE_LS_SLUG" = ok ] && exit 0; exit 255 ;;
    esac ;;
esac
exit 2
"""

INSTALL_SHIM = """#!/bin/sh
# Drop -o/-g (root ownership is the seat's; the test user cannot chown).
args=""
while [ $# -gt 0 ]; do
  case "$1" in -o|-g) shift 2 ;; *) args="$args $1"; shift ;; esac
done
# shellcheck disable=SC2086
exec /usr/bin/install $args
"""


def _shim(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text(body)
    p.chmod(0o755)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    shims = tmp_path / "shims"
    shims.mkdir()
    _shim(shims, "aws", AWS_SHIM)
    _shim(shims, "install", INSTALL_SHIM)
    _shim(shims, "sleep", "#!/bin/sh\nexit 0\n")
    return {
        "PATH": f"{shims}:/usr/bin:/bin",
        "SMD_LANE_INPUTS_STATE_DIR": str(tmp_path / "run/smd-lanes"),
        "DEST": str(tmp_path / "config/demand"),
        "FAKE_CP": "fail",
        "FAKE_LS_MARKER": "fail",
        "FAKE_LS_SLUG": "ok",
    }


def _fetch(env: dict[str, str], **over: str) -> tuple[int, str]:
    script = (
        'log() { echo "$*" >&2; }\n'
        "R2_BUCKET_CONFIG=b CUSTOMER_SLUG=pilot R2_ACCESS_KEY_ID=k R2_SECRET_ACCESS_KEY=s _seed_endpoint=http://e\n"
        f'. "{HELPER}"\n'
        '_fetch_lane_inputs demand "$DEST" demand-firm.yaml\n'
    )
    done = subprocess.run(  # noqa: S603 - the repo's own entrypoint helper, against a tmp tree
        ["bash", "-c", script], env={**env, **over}, capture_output=True, text=True, timeout=60
    )
    state = Path(env["SMD_LANE_INPUTS_STATE_DIR"]) / "demand.inputs"
    return done.returncode, state.read_text().strip() if state.exists() else "<none>"


def test_a_landed_fetch_records_fetched(env: dict[str, str]) -> None:
    assert _fetch(env, FAKE_CP="ok") == (0, "fetched")


def test_a_clean_empty_vault_listing_records_absent(env: dict[str, str]) -> None:
    assert _fetch(env) == (1, "absent")


def test_a_vault_that_holds_the_inputs_but_failed_to_copy_records_unreachable(env: dict[str, str]) -> None:
    assert _fetch(env, FAKE_LS_MARKER="ok") == (1, "unreachable")


def test_a_vault_that_cannot_be_listed_records_unreachable_never_absent(env: dict[str, str]) -> None:
    assert _fetch(env, FAKE_LS_SLUG="fail") == (1, "unreachable")


def test_a_failed_fetch_over_an_earlier_copy_records_kept(env: dict[str, str]) -> None:
    dest = Path(env["DEST"])
    dest.mkdir(parents=True)
    (dest / "demand-firm.yaml").write_text("x\n")
    assert _fetch(env, FAKE_LS_SLUG="fail") == (1, "kept")


def test_an_earlier_verdict_never_survives_a_boot_that_could_not_record(env: dict[str, str]) -> None:
    state_dir = Path(env["SMD_LANE_INPUTS_STATE_DIR"])
    state_dir.mkdir(parents=True)
    (state_dir / "demand.inputs").write_text("absent\n")
    # This boot's write fails (mktemp refuses); the stale "absent" must be gone.
    shims = Path(env["PATH"].split(":", 1)[0])
    _shim(shims, "mktemp", "#!/bin/sh\nexit 1\n")
    assert _fetch(env, FAKE_LS_MARKER="ok") == (1, "<none>")
