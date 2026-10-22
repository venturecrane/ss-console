"""Guard which image provision-customer.sh deploys (2026-10-21).

Reprovisioning an existing seat re-applies that seat's config and secrets; it
must never move the seat's software. On 2026-10-21 it deployed `fleet
latest-image` unconditionally, so one seat's failed rollout (no release could
complete) blocked every seat's reprovision for a day, and a seat already on a
newer image would have been DOWNGRADED. These tests pin the decision:

  1. an existing seat is redeployed on `fleet seat-image <slug>` (what it runs now);
  2. `fleet latest-image` is reached only on seat-image exit 1 (no Machine: a new seat);
  3. any other seat-image exit dies (could-not-tell is never "new seat");
  4. the image roll is still `fly deploy --config ... --image`, ahead of the
     staged-secret guard that test_deploy_ordering.py depends on.

Run::

    cd operator && python -m pytest bin/tests/test_provision_image_selection.py -v
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "provision-customer.sh"


def _code() -> list[str]:
    return ["" if line.lstrip().startswith("#") else line for line in _SCRIPT.read_text(encoding="utf-8").splitlines()]


def _step7() -> str:
    """The non-build image-selection branch of step 7, as code (comments blanked)."""
    lines = _code()
    start = next(i for i, line in enumerate(lines) if re.search(r"BUILD_FROM_SOURCE.*= 1 \]", line))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi" and lines[i].startswith("fi"))
    return "\n".join(lines[start : end + 1])


def test_existing_seat_redeploys_its_own_image() -> None:
    block = _step7()
    assert re.search(r'RELEASE_IMAGE="\$\("\$\{BIN_DIR\}/fleet" seat-image "\$\{SLUG\}"\)"', block), block


def test_latest_image_only_for_a_new_seat() -> None:
    block = _step7()
    latest = [i for i, line in enumerate(block.splitlines()) if 'fleet" latest-image' in line]
    assert len(latest) == 1, "latest-image must appear exactly once, on the new-seat branch"
    before = "\n".join(block.splitlines()[: latest[0]])
    assert re.search(r'elif \[ "\$\{seat_rc\}" = 1 \]; then\s*$', before), "latest-image must sit under seat_rc = 1"


def test_could_not_tell_dies() -> None:
    block = _step7()
    assert re.search(r"else\s*\n\s*die .*seat-image", block), "a seat-image exit other than 0/1 must die"


def test_roll_is_still_fly_deploy_config_image() -> None:
    block = _step7()
    assert re.search(r'fly deploy --config "\$\{RENDERED_DIR\}/fly.toml" --image "\$\{RELEASE_IMAGE\}"', block)


def _decide(seat_rc: int, latest_rc: int = 0) -> subprocess.CompletedProcess[str]:
    """Run the real selection branch with stub `fleet` and `fly`; report which image it deployed."""
    stub = f"""
die() {{ echo "DIE $*"; exit 9; }}
log() {{ :; }}
BUILD_FROM_SOURCE=0; SLUG=s; APP_NAME=hermes-s; REPO_ROOT=/; RENDERED_DIR=/r; BIN_DIR=/stub
fleet_stub() {{
  case "$1" in
    seat-image) [ {seat_rc} = 0 ] && echo seat-own-image; return {seat_rc} ;;
    latest-image) [ {latest_rc} = 0 ] && echo newest-completed; return {latest_rc} ;;
  esac
}}
fly() {{ echo "DEPLOYED $*"; }}
"""
    body = _step7().replace('"${BIN_DIR}/fleet"', "fleet_stub")
    return subprocess.run(["bash", "-c", stub + body], capture_output=True, text=True, timeout=30)  # noqa: S603 - literal bash argv; the script is this test's own text plus the repo's own step 7


def test_selection_behaves_as_documented() -> None:
    existing = _decide(0)
    assert "--image seat-own-image" in existing.stdout and "newest-completed" not in existing.stdout
    new = _decide(1)
    assert "--image newest-completed" in new.stdout
    unknown = _decide(3)
    assert unknown.returncode == 9 and "DEPLOYED" not in unknown.stdout
    new_no_release = _decide(1, latest_rc=1)
    assert new_no_release.returncode == 9 and "DEPLOYED" not in new_no_release.stdout
