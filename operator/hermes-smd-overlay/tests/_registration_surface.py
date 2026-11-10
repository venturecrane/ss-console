"""What the overlay registers on one seat: its tools and its hooks, per sub-plugin.

The collector behind ``test_authored_seat_registration_unchanged.py``. It loads
every sub-plugin ``plugin.yaml`` lists, in that order, through the same
``load_plugin`` the unit suite uses, points the live config at one authored
``customer.yaml``, calls each plugin's real ``register`` with a recording
context, and returns what was registered: each tool by name with its owning
plugin and toolset, and each hook by name with its owning plugin. A hook on
``pre_llm_call`` is how a plugin adds to the prompt, so the hook list is also
the prompt-addition list.

Isolation. Every run gets its own temp state directory for every path-valued
knob the overlay reads (``contracts/consumes.yaml``), so registering for one
seat cannot leave a file another seat's registration reads, and nothing is
written under ``/opt/data``. The collector never starts a turn.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

OVERLAY_ROOT = Path(__file__).resolve().parents[1]
CUSTOMERS = OVERLAY_ROOT.parent / "customers"

#: Path knobs the plugins read at register time, each pointed into the run's
#: own temp directory (the e2e harness keeps the same list, ``_STATE_PATHS``).
_STATE_PATHS: dict[str, str] = {
    "SMD_ADMIN_POSSESSION_DB_PATH": "admin_possession.db",
    "SMD_ATTACHMENT_SPOOL_DIR": "attachment_spool",
    "SMD_CASEWORK_LEDGER_PATH": "casework.db",
    "SMD_CONNECTOR_LEDGER_PATH": "connector_ledger.db",
    "SMD_ESCALATION_LEDGER_PATH": "escalation.db",
    "SMD_EXPOSURE_OVERRIDE_DB_PATH": "exposure_override.db",
    "SMD_GATE_CAP_DB_PATH": "gate_cap.db",
    "SMD_HELD_REPLY_DB_PATH": "held_reply.db",
    "SMD_MCP_STORE_DIR": "mcp_store",
    "SMD_MSGRAPH_STATE_PATH": "msgraph_state.json",
    "SMD_REQUEST_CARDS_DB_PATH": "smd-gate/request_cards.db",
    "SMD_REQUEST_INDEX_DB_PATH": "requests.db",
    "SMD_SENT_LINES_DIR": "sent_lines",
    "SMD_SPEC_DIR": "spec",
    "SMD_STICKY_STOP_DB_PATH": "smd/sticky_stop.db",
    "SMD_D1_AUDIT_BINDING": "audit.db",
    "SMD_D1_AGENT_STATE_BINDING": "agent-state.db",
    "SMD_HELP_STATE_DB_PATH": "smd/help_state.db",
    "SMD_SENDER_ENROLLMENT_ENROLLED": "sender-enrollment/enrolled.json",
    "HERMES_HOME": "home",
}


class RecordingContext:
    """A plugin context that records what each plugin registered, by plugin."""

    def __init__(self) -> None:
        self.owner = ""
        self.tools: dict[str, dict[str, str]] = {}
        self.hooks: list[tuple[str, str]] = []

    def register_hook(self, name: str, callback: Callable[..., Any]) -> None:
        self.hooks.append((name, self.owner))

    def register_tool(self, *, name: str, **kwargs: Any) -> None:
        self.tools[name] = {"plugin": self.owner, "toolset": str(kwargs.get("toolset") or "")}

    # Some plugins register a CLI command or a skill; neither is a tool or a
    # prompt addition, and neither is recorded.
    def __getattr__(self, name: str) -> Callable[..., None]:
        if name.startswith("register_"):
            return lambda *a, **k: None
        raise AttributeError(name)


def sub_plugins() -> list[str]:
    manifest = yaml.safe_load((OVERLAY_ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    return [Path(p).name for p in manifest["plugins"]]


def authored_seats() -> list[Path]:
    """Every authored seat config (``_template`` and its presets are not seats)."""
    return sorted(
        p
        for p in CUSTOMERS.glob("*/customer.yaml")
        if not p.parent.name.startswith("_")
    )


def collect(customer_yaml: Path, load_plugin: Callable[[str], Any]) -> dict[str, Any]:
    """``{"tools": {name: {plugin, toolset}}, "hooks": [[hook, plugin], ...]}``
    for one seat, with the environment restored afterwards."""
    saved = dict(os.environ)
    saved_modules = set(sys.modules)
    ctx = RecordingContext()
    with tempfile.TemporaryDirectory(prefix="regsurf") as tmp:
        state = Path(tmp)
        try:
            for name, leaf in _STATE_PATHS.items():
                target = state / leaf
                target.parent.mkdir(parents=True, exist_ok=True)
                os.environ[name] = str(target)
            slug = customer_yaml.parent.name
            os.environ["SMD_CUSTOMER_YAML_PATH"] = str(customer_yaml)
            os.environ["SMD_CUSTOMER_SLUG"] = slug
            os.environ["CUSTOMER_SLUG"] = slug
            logging.disable(logging.CRITICAL)
            for plugin in sub_plugins():
                ctx.owner = plugin
                module = load_plugin(plugin)
                register = getattr(module, "register", None)
                if callable(register):
                    register(ctx)
        finally:
            logging.disable(logging.NOTSET)
            os.environ.clear()
            os.environ.update(saved)
            # Plugins are reloaded per seat; drop what this run imported so a
            # module-level decision made for one seat cannot carry to the next.
            for name in set(sys.modules) - saved_modules:
                if name.startswith("plugin_hermes_smd_"):
                    sys.modules.pop(name, None)
    return {
        "tools": dict(sorted(ctx.tools.items())),
        "hooks": sorted([list(h) for h in set(ctx.hooks)]),
    }


def main(argv: list[str]) -> int:
    """Regenerate the snapshot: ``python -m tests._registration_surface <out.json>``."""
    import json

    from tests.conftest import load_plugin

    out = {seat.parent.name: collect(seat, load_plugin) for seat in authored_seats()}
    Path(argv[0]).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
