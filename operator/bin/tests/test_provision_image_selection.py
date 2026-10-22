"""Guard which image provision-customer.sh deploys (2026-10-21).

Reprovisioning an existing seat re-applies that seat's config and secrets; it
must never move the seat's software. On 2026-10-21 it deployed `fleet
latest-image` unconditionally, so one seat's failed rollout (no release could
complete) blocked every seat's reprovision for a day, and a seat already on a
newer image would have been DOWNGRADED. These tests pin the decision, run
against the real lib/select-release-image.sh with a stub `fleet`:

  1. an existing seat (seat-image exit 0) gets the image it runs now;
  2. a new seat (seat-image exit 1) gets `fleet latest-image`;
  3. any other seat-image exit refuses (could-not-tell is never "new seat");
  4. a new seat with no completed release refuses;
and that provision-customer.sh step 7 deploys exactly what the function chose,
still as `fly deploy --config ... --image` (test_deploy_ordering.py depends on it).

Run::

    cd operator && python -m pytest bin/tests/test_provision_image_selection.py -v
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_BIN = Path(__file__).resolve().parents[1]
_LIB = _BIN / "lib" / "select-release-image.sh"
_SCRIPT = _BIN / "provision-customer.sh"


def _select(tmp_path: Path, seat_rc: int, latest_rc: int = 0) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "fleet"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        f"  seat-image) [ {seat_rc} = 0 ] && echo seat-own-image; exit {seat_rc} ;;\n"
        f"  latest-image) [ {latest_rc} = 0 ] && echo newest-completed; exit {latest_rc} ;;\n"
        "esac\nexit 99\n"
    )
    stub.chmod(0o755)
    script = f'source "{_LIB}"; select_release_image "{tmp_path}" s'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)  # noqa: S603 - literal bash argv; the repo's own lib plus this test's stub


def test_existing_seat_keeps_its_own_image(tmp_path: Path) -> None:
    out = _select(tmp_path, 0)
    assert out.returncode == 0 and out.stdout.strip() == "seat-own-image"


def test_new_seat_gets_the_newest_completed_release(tmp_path: Path) -> None:
    out = _select(tmp_path, 1)
    assert out.returncode == 0 and out.stdout.strip() == "newest-completed"


def test_could_not_tell_refuses(tmp_path: Path) -> None:
    out = _select(tmp_path, 3)
    assert out.returncode != 0 and out.stdout == "" and "refusing to guess" in out.stderr


def test_new_seat_without_a_completed_release_refuses(tmp_path: Path) -> None:
    out = _select(tmp_path, 1, latest_rc=1)
    assert out.returncode != 0 and out.stdout == "" and "no fleet release has completed" in out.stderr


def _code() -> str:
    return "\n".join(
        "" if line.lstrip().startswith("#") else line for line in _SCRIPT.read_text(encoding="utf-8").splitlines()
    )


def test_step7_deploys_what_the_function_chose() -> None:
    code = _code()
    assert re.search(r'RELEASE_IMAGE="\$\(select_release_image "\$\{BIN_DIR\}" "\$\{SLUG\}"\)" \|\| die ', code)
    assert re.search(r'fly deploy --config "\$\{RENDERED_DIR\}/fly.toml" --image "\$\{RELEASE_IMAGE\}"', code)
    assert "latest-image" not in code, "provisioning must choose the image only through select_release_image"
