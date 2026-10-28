"""Keep the Hermes package manager's dependency selection off a seat's volume.

WHY (ss-console#3564, the re-promotion after the #3567 revert). From Hermes
v0.22 every hermes process starts with ``pm.environments.activate_dependencies``
(``hermes_bootstrap.py``). When ``<root>/installs/<key>/facts.json`` records a
venv (``packages.venv.environment``, read by ``pm.environments._recorded_venv``)
that function deletes every ``site-packages`` entry from ``sys.path``, the image
venv's included, and runs the process on the recorded generation instead. The
key is ``sha256("/opt/hermes")[:16]`` (``install_key``), the same for every
image, and the root is the volume (``/opt/data``), so a selection committed by
one boot outlives the image that made it. ``hermes plugins enable`` committed
one on smd-staging, and every later gateway booted without yaml, aiohttp,
anthropic and the overlay (vfy_01M603AFMR22YB1R1D4E7F8T4Y). With nothing
recorded, activation is a no-op for a venv interpreter
(``_require_own_dependencies``: ``sys.prefix != sys.base_prefix``), which is the
state every v2026.9.14 seat booted in.

``<root>/tools`` is the package manager's store (``pm.environments.store_root``
falls back to ``get_default_hermes_root() / "tools"``): the Python and uv it
downloaded to build that generation. Its Python is also what cron runs a
pre_run ``.py`` script on once it exists (``cron/scheduler_script.py``
``_posix_cron_script_argv``: ``resolve_store_python`` non-None -> the selected
generation's interpreter; None -> the gateway's own ``sys.executable``).
Nothing in the seat image writes there.

``purge`` removes exactly the state a failed v0.22 boot left: each
``installs/<key>`` whose facts.json records a venv (or cannot be read, which
Hermes also refuses to boot past: ``_recorded_venv`` raises), and ``tools`` only
when such a selection was removed alongside it. It prints every path it
removes. ``check`` is the fail-closed assertion bootstrap.sh runs before the
gateway exec and seat-checks.sh runs after: no recorded venv in any facts.json,
and no ``tools`` store.

Usage::

    python3 -m bootstrap.pm_state purge <hermes-root>
    python3 -m bootstrap.pm_state check <hermes-root>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def _selection(facts: Path) -> str | None:
    """Why *facts* selects a dependency environment, or None when it selects none."""
    try:
        data = json.loads(facts.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return f"unreadable ({exc.__class__.__name__})"
    if not isinstance(data, dict):
        return "not a JSON object"
    packages = data.get("packages")
    venv = packages.get("venv") if isinstance(packages, dict) else None
    if venv is None:
        return None
    if not isinstance(venv, dict):
        return f"packages.venv is {type(venv).__name__}"
    environment = venv.get("environment")
    if environment is None:
        return None
    return f"packages.venv.environment={environment!r}"


def recorded_selections(root: Path) -> list[tuple[Path, str]]:
    """``(installs/<key> dir, reason)`` for every install whose facts.json selects a venv."""
    installs = root / "installs"
    if not installs.is_dir():
        return []
    found = []
    for facts in sorted(installs.glob("*/facts.json")):
        reason = _selection(facts)
        if reason is not None:
            found.append((facts.parent, reason))
    return found


def purge(root: Path) -> list[str]:
    """Remove a recorded selection and the store built for it; return what was removed."""
    removed: list[str] = []
    selections = recorded_selections(root)
    for key_dir, reason in selections:
        shutil.rmtree(key_dir)
        removed.append(f"{key_dir} ({reason})")
    tools = root / "tools"
    if selections and (tools.exists() or tools.is_symlink()):
        if tools.is_dir() and not tools.is_symlink():
            shutil.rmtree(tools)
        else:
            tools.unlink()
        removed.append(f"{tools} (the package manager store that built it)")
    return removed


def problems(root: Path) -> list[str]:
    """Empty when no hermes process on this volume would swap off the image venv."""
    found = [f"{key_dir}/facts.json selects a dependency environment: {reason}"
             for key_dir, reason in recorded_selections(root)]
    tools = root / "tools"
    if tools.exists() or tools.is_symlink():
        found.append(f"{tools} exists: a package-manager store the seat image never writes")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("purge", "check"):
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[2])
    if argv[1] == "purge":
        try:
            removed = purge(root)
        except OSError as exc:
            print(f"pm state: purge failed: {exc}", file=sys.stderr)
            return 1
        for line in removed:
            print(f"pm state: removed {line}")
        if not removed:
            print("pm state: no recorded dependency selection to remove")
        return 0
    found = problems(root)
    for line in found:
        print(f"pm state: {line}", file=sys.stderr)
    if not found:
        print("pm state: clean (no recorded dependency selection, no package-manager store)")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
