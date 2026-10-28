"""Select the overlay plugin in a Hermes config.yaml, without ``hermes plugins enable``.

WHY (ss-console#3564, the re-promotion after the #3567 revert). bootstrap.sh
used to run ``hermes plugins enable hermes-smd-overlay`` in the root home and in
the active profile home on every boot. From Hermes v0.22 that command goes
through the package manager's admission transaction
(``hermes_cli/plugins_cmd.py`` ``_set_plugin_enabled`` ->
``_admit_and_save_plugin_sets``), which records a dependency generation under
``/opt/data/installs/<key>/facts.json`` and downloads a Python and uv into
``/opt/data/tools``. From then on every hermes process's
``pm.environments.activate_dependencies`` removes the image venv's
site-packages from ``sys.path`` and runs on that generation instead, so the
smd-staging gateway booted without ``yaml``, ``aiohttp``, ``anthropic`` or the
overlay's own packages (vfy_01M603AFMR22YB1R1D4E7F8T4Y, upstream
NousResearch/hermes-agent#136015). The enable also ran every overlay
``register()`` in a non-gateway CLI process, which the workspace broker refuses.

What ``enable`` wrote that a seat needs is two keys, and this writes exactly
those, directly:

* ``plugins.enabled`` holds ``hermes-smd-overlay``. The loader gates a user
  plugin on this list alone, matching the path-derived key or the manifest name
  (``hermes_cli/plugins_discovery.py`` ``gate_manifest`` @ v0.22.5;
  ``_get_enabled_plugins`` reads ``plugins.enabled``). For a flat plugin under
  ``<home>/plugins/hermes-smd-overlay`` both are ``hermes-smd-overlay``. The
  v2026.9.14 loader reads the same list.
* ``plugins.entries.hermes-smd-overlay.allow_tool_override: false``. v2026.9.14's
  ``cmd_enable`` asked the override question on a non-interactive stdin, got the
  default No and wrote ``false`` (``_resolve_tool_override_grant``); written here
  so the grant on disk is the one every seat has run with.

Plus one pin that has no ``enable`` equivalent:

* ``security.allow_lazy_installs: false``. With it ``pm`` refuses every
  non-explicit dependency sync (``pm/install.py`` ``lazy_installs_allowed``,
  read from the ACTIVE home's config), so nothing at runtime can commit a
  generation either. translate writes all three into every persona profile;
  this module writes them into the root home's config and checks both.

Usage (bootstrap.sh)::

    python3 -m bootstrap.plugin_selection <config.yaml>...          # apply (idempotent)
    python3 -m bootstrap.plugin_selection --check <config.yaml>...  # verify only
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

OVERLAY_KEY = "hermes-smd-overlay"
OVERLAY_SELECTION: dict[str, Any] = {
    "enabled": [OVERLAY_KEY],
    "entries": {OVERLAY_KEY: {"allow_tool_override": False}},
}
SECURITY_PIN: dict[str, Any] = {"allow_lazy_installs": False}


def _load(path: Path, *, create: bool) -> dict[str, Any]:
    if not path.exists():
        if create:
            return {}
        raise FileNotFoundError(f"{path} does not exist")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    return doc


def _child(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        value = parent[key] = {}
    return value


def select(config: dict[str, Any]) -> dict[str, Any]:
    """Return *config* with the overlay selected and lazy installs off (mutates and returns)."""
    plugins = _child(config, "plugins")
    enabled = plugins.get("enabled")
    enabled = [e for e in enabled if isinstance(e, str)] if isinstance(enabled, list) else []
    if OVERLAY_KEY not in enabled:
        enabled.append(OVERLAY_KEY)
    plugins["enabled"] = enabled
    disabled = plugins.get("disabled")
    if isinstance(disabled, list) and OVERLAY_KEY in disabled:
        # "explicit disable wins" in the loader, so a stale entry would veto the selection.
        plugins["disabled"] = [d for d in disabled if d != OVERLAY_KEY]
    entry = _child(_child(plugins, "entries"), OVERLAY_KEY)
    entry.update(OVERLAY_SELECTION["entries"][OVERLAY_KEY])
    _child(config, "security").update(SECURITY_PIN)
    return config


def problems(config: dict[str, Any], where: str) -> list[str]:
    """What keeps *config* from loading the overlay with lazy installs off; empty is pass."""
    found: list[str] = []
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list) or OVERLAY_KEY not in enabled:
        found.append(f"{where}: plugins.enabled does not hold {OVERLAY_KEY!r} (got {enabled!r})")
    disabled = plugins.get("disabled")
    if isinstance(disabled, list) and OVERLAY_KEY in disabled:
        found.append(f"{where}: plugins.disabled holds {OVERLAY_KEY!r}")
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    entry = entries.get(OVERLAY_KEY) if isinstance(entries.get(OVERLAY_KEY), dict) else {}
    if entry.get("allow_tool_override") is not False:
        found.append(f"{where}: plugins.entries.{OVERLAY_KEY}.allow_tool_override is not false")
    security = config.get("security") if isinstance(config.get("security"), dict) else {}
    if security.get("allow_lazy_installs") is not False:
        found.append(
            f"{where}: security.allow_lazy_installs is {security.get('allow_lazy_installs')!r}, want false"
        )
    return found


def _write(path: Path, config: dict[str, Any]) -> None:
    body = yaml.safe_dump(config, default_flow_style=False, sort_keys=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(prefix=".config.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply(path: Path) -> bool:
    """Select the overlay in the config at *path* (created when absent). True when it wrote."""
    before = _load(path, create=True)
    after = select(copy.deepcopy(before))
    if path.exists() and after == before:
        return False
    _write(path, after)
    return True


def main(argv: list[str]) -> int:
    args = argv[1:]
    check = bool(args) and args[0] == "--check"
    paths = [Path(a) for a in (args[1:] if check else args)]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    failed: list[str] = []
    for path in paths:
        try:
            if not check:
                wrote = apply(path)
                print(f"plugin selection: {'wrote' if wrote else 'unchanged'} {path}")
            failed.extend(problems(_load(path, create=False), str(path)))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            failed.append(f"{path}: {exc.__class__.__name__}: {exc}")
    for problem in failed:
        print(f"plugin selection: {problem}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
