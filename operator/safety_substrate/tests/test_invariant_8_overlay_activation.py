"""Invariant 8 — overlay ACTIVATION (the governance layer is actually ON).

Invariants 1-7 verify the safety LOGIC is correct. Every one of them passed
while the SMD overlay was completely inert on the live gateway — because they
test the library in isolation, not whether the plugins are wired into the
running agent. That gap shipped an operator with no audit emission and, worse,
no trust/ceiling enforcement (ss-console#1285). This invariant closes it.

It asserts the overlay actually activates: the umbrella `register(ctx)` fans
out, all five functional plugins (audit, trust, voice, webhook-router,
memory-mirror) register their hooks (including the trust `pre_tool_call`
ceiling gate), the audit schema is created, AND trust actually refuses an
action that must be gated (a permanently-banned send tool).

HARD BOOT GATE: run via run_invariants.py --strict, a False here halts boot —
an ungoverned operator must never start. A future Hermes re-pin that breaks the
plugin-load contract (the original failure mode) fails loudly here instead of
silently shipping an inert overlay.

PROCESS-BOUNDARY LIMITATION (read this — it is why #1285 hid for so long). This
invariant runs in the PRE-GATEWAY ``run_invariants`` process. It loads the overlay
and asserts the registration LOGIC is sound in THAT process's plugin manager. It
CANNOT assert the property that actually matters at runtime — that the overlay's
hooks fire through the *gateway* process's live singleton on a real turn — because
it is a different process. #1285 was exactly this gap: every registration/logic
invariant stayed green while the gateway's own singleton was cached without the
overlay and every hook was inert on live turns. So this check is necessary but
PROVABLY INSUFFICIENT on its own.

The AUTHORITATIVE LIVE GATE is the overlay's ``gateway:startup`` HookRegistry
handler (``hooks/smd-overlay-activation/handler.py`` in hermes-smd-overlay). It
runs IN the gateway process: it force-loads the overlay into the live singleton,
then drives a REAL ``pre_tool_call`` dispatch self-check (trust must block a banned
send tool) and fails closed (``os._exit``) if the operator is not governed — the
only place the live-turn property can be asserted. bootstrap.sh fail-closes if that
handler is not wired onto the volume. This invariant and the live handler are
COMPLEMENTARY tiers, not redundant: this one catches a broken registration/logic
contract early and cheaply; the handler catches an inert live singleton.

Environment handling: the activation assertions run whenever the overlay is
installed (the Machine boot context, and any env where it's present). When the
overlay is NOT installed (CI / bare checkout), there is nothing to activate, so
the check is N/A and passes — "overlay present but inert" is the failure mode
this guards; "overlay absent entirely" is already caught by the image build and
the `hermes-plugins-installed` boot-smoke check.

Boot-gated: exposes run() -> (ok, message); imports no pytest.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import types
from pathlib import Path

# The five functional plugins whose activation IS the governance guarantee.
_FUNCTIONAL = [
    "hermes-smd-audit",
    "hermes-smd-trust",
    "hermes-smd-voice",
    "hermes-smd-memory-mirror",
    "hermes-smd-webhook-router",
]

# Hooks that must land for the layer to be governing (union across the five).
_REQUIRED_HOOKS = {
    "pre_tool_call",  # trust ceiling gate — the safety-critical one
    "post_tool_call",  # audit + trust
    "post_llm_call",  # audit + voice
    "pre_llm_call",  # voice
    "subagent_stop",  # audit
    "on_session_end",  # memory-mirror
    "pre_gateway_dispatch",  # webhook-router
}

# A permanently-banned tool (BANNED_TOOLS, ADR 0035 external-send draft floor): the
# trust gate must refuse it regardless of the customer's authored ceiling, so it
# is a deterministic "this must be gated" probe.
_BANNED_PROBE_TOOL = "email_send"


def _overlay_dirs() -> list[Path]:
    dirs = []
    home = os.environ.get("HERMES_HOME")
    if home:
        dirs.append(Path(home) / "plugins" / "hermes-smd-overlay")
    dirs.append(Path("/opt/data/plugins/hermes-smd-overlay"))
    dirs.append(Path.home() / ".hermes" / "plugins" / "hermes-smd-overlay")
    return dirs


def _find_overlay() -> Path | None:
    """The overlay dir that carries the fan-out register (__init__.py)."""
    for c in _overlay_dirs():
        if (c / "__init__.py").exists():
            return c
    return None


def _stale_overlay_dir() -> Path | None:
    """An overlay dir that EXISTS but lacks the fan-out __init__.py — a
    pre-fan-out / stale install that would boot ungoverned. Distinguished from
    'no overlay at all' so the former FAILs the gate and only the latter is N/A."""
    for c in _overlay_dirs():
        if c.exists() and not (c / "__init__.py").exists():
            return c
    return None


class _RecordingCtx:
    """Records hook registrations; tolerant of any other ctx call."""

    def __init__(self):
        self.hooks: dict[str, list] = {}

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _load_umbrella(overlay_dir: Path):
    mod_name = "hermes_plugins.hermes_smd_overlay"
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []  # type: ignore[attr-defined]
        ns.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = ns
    spec = importlib.util.spec_from_file_location(
        mod_name,
        overlay_dir / "__init__.py",
        submodule_search_locations=[str(overlay_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = mod_name
    module.__path__ = [str(overlay_dir)]  # type: ignore[attr-defined]
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _trust_gates_banned_action(ctx: _RecordingCtx) -> bool:
    """Invoke every registered pre_tool_call hook with a banned send tool;
    return True if any returns a block directive (trust enforcing)."""
    for fn in ctx.hooks.get("pre_tool_call", []):
        try:
            res = fn(
                tool_name=_BANNED_PROBE_TOOL,
                args={},
                session_id="invariant-8",
                tool_call_id="invariant-8",
                customer_slug=os.environ.get("SMD_CUSTOMER_SLUG", ""),
            )
        except Exception:  # noqa: BLE001 — a raising hook is not the gate contract
            res = None
        if isinstance(res, dict) and res.get("action") == "block":
            return True
    return False


def run() -> tuple[bool, str]:
    overlay = _find_overlay()
    if overlay is None:
        stale = _stale_overlay_dir()
        if stale is not None:
            return (
                False,
                f"FAIL: overlay dir present at {stale} but missing the fan-out register "
                "__init__.py — stale/pre-fan-out overlay; the operator would boot ungoverned",
            )
        return (True, "N/A: overlay not installed in this environment — activation check skipped")

    try:
        umbrella = _load_umbrella(overlay)
        ctx = _RecordingCtx()
        registered = umbrella.load_and_register_subplugins(ctx)
    except Exception as e:  # noqa: BLE001 - run()-style invariant: an overlay register() raise IS the finding, reported as FAIL rather than raised
        return (False, f"FAIL: overlay register() raised: {type(e).__name__}: {e}")

    missing = [p for p in _FUNCTIONAL if p not in registered]
    if missing:
        return (False, f"FAIL: functional plugins did not register: {missing} (got {registered})")

    hooks_missing = _REQUIRED_HOOKS - set(ctx.hooks)
    if hooks_missing:
        return (False, f"FAIL: required hooks not attached: {sorted(hooks_missing)}")

    binding = os.environ.get("SMD_D1_AUDIT_BINDING", "")
    if binding.startswith("/"):
        try:
            conn = sqlite3.connect(f"file:{binding}?mode=ro", uri=True)
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            conn.close()
        except Exception as e:  # noqa: BLE001 - run()-style invariant: an unreadable audit db IS the finding, reported as FAIL rather than raised
            return (False, f"FAIL: audit db unreadable at {binding}: {type(e).__name__}: {e}")
        if "audit_log" not in tables:
            return (False, f"FAIL: audit_log not created at {binding} — ensure_schema did not run")

    if not _trust_gates_banned_action(ctx):
        return (
            False,
            f"FAIL: TRUST NOT ENFORCING — banned tool {_BANNED_PROBE_TOOL!r} was not gated by any "
            "registered pre_tool_call hook; the operator is ungoverned",
        )

    return (
        True,
        f"PASS: invariant 8 holds — overlay active ({len(registered)} plugins registered, hooks "
        f"attached, audit schema present, trust gated banned {_BANNED_PROBE_TOOL!r})",
    )


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
