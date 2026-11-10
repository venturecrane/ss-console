"""The trust plugin the gateway runs, loaded for the boot invariants.

Invariants 1 to 5 (destructive, external send, commitment, the stop, ceiling in
code) are claims about what ``hermes-smd-trust`` decides on a live tool call. So
they load that plugin, not a look-alike: its ``enforce`` module holds the
ceiling resolution the gateway's ``pre_tool_call`` hook calls, and its
``on_pre_tool_call`` is that hook.

WHERE IT IS LOADED FROM, in order, the first that exists:

1. ``$HERMES_HOME/plugins/hermes-smd-overlay`` and
   ``/opt/data/plugins/hermes-smd-overlay``: the volume copy the gateway loads
   on a seat (bootstrap.sh refreshes it from the image's pack before the
   invariants run; invariant 8 looks in the same two places).
2. Beside the installed ``shared`` package: the overlay as pip-installed into
   the Hermes venv on a seat, or the source tree in CI and on a laptop.

The plugin imports ``shared`` (and ``records_contract``) by absolute name, so it
resolves them exactly as the gateway does. Nothing here falls back to another
implementation: a plugin that cannot be found or loaded raises, and the runner
records that invariant as failed.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

PLUGIN = "hermes-smd-trust"
_MODULE_NAME = "smd_substrate_trust_plugin"


def _candidates() -> list[Path]:
    overlays: list[Path] = []
    home = os.environ.get("HERMES_HOME")
    if home:
        overlays.append(Path(home) / "plugins" / "hermes-smd-overlay")
    overlays.append(Path("/opt/data/plugins/hermes-smd-overlay"))
    out = [o / "plugins" / PLUGIN for o in overlays]
    import shared  # the package the gateway imports; required on every seat

    if shared.__file__:
        out.append(Path(shared.__file__).resolve().parent.parent / "plugins" / PLUGIN)
    return out


def plugin_dir() -> Path:
    """The trust plugin directory these invariants exercise."""
    for candidate in _candidates():
        if (candidate / "__init__.py").is_file() and (candidate / "enforce.py").is_file():
            return candidate
    raise FileNotFoundError(f"{PLUGIN} not found; looked in {[str(c) for c in _candidates()]}")


@functools.cache
def load() -> ModuleType:
    """The trust plugin package, loaded once per process. Its ``enforce``
    submodule and ``on_pre_tool_call`` are the gateway's own."""
    directory = plugin_dir()
    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME, directory / "__init__.py", submodule_search_locations=[str(directory)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {PLUGIN} from {directory}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_MODULE_NAME, None)
        raise
    return module


def enforce_module() -> ModuleType:
    """``hermes-smd-trust/enforce.py``: ``enforce``, ``Ceiling``, ``ActionClass``."""
    return sys.modules[f"{load().__name__}.enforce"]


def source() -> str:
    """The enforce.py these invariants ran, for the PASS line."""
    return str(enforce_module().__file__)
