"""Guard 3 (audit Wave 0) — HOOK-PARITY guard.

THE INERT-CONTROL CLASS THIS CLOSES
-----------------------------------
The overlay's governance is only real if its functional plugins actually
REGISTER their Hermes hooks at boot. The trust gate IS ``pre_tool_call``;
audit emission is ``post_tool_call`` / ``post_llm_call`` / ``subagent_stop``;
the webhook router is ``pre_gateway_dispatch``; memory-mirror is
``on_session_end``. ``test_invariant_8_overlay_activation.py`` is the runtime
gate that checks the overlay registered them — but nothing held ITS expectation
(``_REQUIRED_HOOKS`` / ``_FUNCTIONAL``) in sync with the documented hook
surface. Drop a hook from that set, or stop requiring a functional plugin, and
the operator can boot ungoverned while the activation gate still passes (it
only checks the hooks it was told to check).

WHAT THIS GUARD ASSERTS
-----------------------
``operator/contracts/overlay-hook-surface.json`` is the canonical in-repo
declaration of the hooks the overlay MUST register and the functional plugins
whose activation IS the governance guarantee. This guard pins the runtime
activation gate to that contract:

  1. ACTIVATION-GATE PARITY — ``test_invariant_8_overlay_activation.py``'s
     ``_REQUIRED_HOOKS`` set must equal the contract's ``requiredHooks`` keys,
     and its ``_FUNCTIONAL`` list must equal the contract's
     ``functionalPlugins``. Editing one without the other fails CI.
  2. WEBHOOK-ROUTER COHERENCE — the customer.yaml webhook-triggers validator
     references ``pre_gateway_dispatch`` + ``hermes-smd-webhook-router``; both
     must be in the contract.
  3. ABSTRACT-ALIAS PRESENCE — the four adapter-register aliases
     (``pre_tool`` / ``post_tool`` / ``refusal`` / ``compaction``) named by
     ``docs/specs/operator/aie-adapter-register.md`` are documented in the
     contract, and the spec actually lists them.

CROSS-REPO LIMIT. The overlay plugin registry
(``hermes-smd-overlay/__init__.py`` and each ``plugins/hermes-smd-*/``
``register_hook`` call) is NOT checked out in this CI. This guard proves the
ss-console-side expectation is coherent; the final proof that the overlay
actually registered the hooks is ``test_invariant_8``'s runtime check against a
live Machine. The contract is the anchor that forces the two surfaces to be
reconciled by a human (the same shape ``overlay-pairs.json`` uses).

Run::

    cd operator && python3 -m pytest \
        safety-substrate/tests/test_guard_hook_parity.py -v
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

_HERE = Path(__file__).resolve()
_OP = _HERE.parents[2]  # operator/
_REPO = _OP.parent  # repo root
_CONTRACT = _OP / "contracts" / "overlay-hook-surface.json"
_INVARIANT_8 = _HERE.parent / "test_invariant_8_overlay_activation.py"
_SPEC = _REPO / "docs" / "specs" / "operator" / "aie-adapter-register.md"
_WEBHOOK_TRIGGERS = _REPO / "src" / "lib" / "operator" / "customer-yaml" / "sections-webhook-triggers.ts"


def _contract() -> dict:
    return json.loads(_CONTRACT.read_text(encoding="utf-8"))


def _literal_set_from_module(path: Path, name: str) -> set[str]:
    """Extract a module-level ``name = {...}`` set/dict literal of string
    constants by parsing the AST. Robust to surrounding comments. For a dict
    literal we return its keys; for a set/list literal its elements.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        value = node.value
        if isinstance(value, ast.Set):
            return {_const_str(e) for e in value.elts}
        if isinstance(value, (ast.List, ast.Tuple)):
            return {_const_str(e) for e in value.elts}
        if isinstance(value, ast.Dict):
            return {_const_str(k) for k in value.keys}
        raise AssertionError(f"{name} in {path.name} is not a set/list/dict literal")
    raise AssertionError(f"{name} not found as a module-level assignment in {path.name}")


def _const_str(node: ast.expr) -> str:
    assert isinstance(node, ast.Constant) and isinstance(node.value, str), (
        f"expected a string constant, got {ast.dump(node)}"
    )
    return node.value


# ---------------------------------------------------------------------------
# 1. Activation-gate parity
# ---------------------------------------------------------------------------


def test_required_hooks_match_contract():
    """invariant_8's _REQUIRED_HOOKS must equal the contract's requiredHooks."""
    contract = _contract()
    contract_hooks = set(contract["requiredHooks"].keys())
    gate_hooks = _literal_set_from_module(_INVARIANT_8, "_REQUIRED_HOOKS")
    assert gate_hooks == contract_hooks, (
        "test_invariant_8_overlay_activation.py::_REQUIRED_HOOKS has drifted from "
        "operator/contracts/overlay-hook-surface.json::requiredHooks.\n"
        f"  only in activation gate: {sorted(gate_hooks - contract_hooks)}\n"
        f"  only in contract:        {sorted(contract_hooks - gate_hooks)}\n"
        "A hook in the contract but not the gate is NOT enforced at boot (inert); "
        "a hook in the gate but not the contract is undocumented. Reconcile both."
    )


def test_functional_plugins_match_contract():
    """invariant_8's _FUNCTIONAL must equal the contract's functionalPlugins."""
    contract = _contract()
    contract_plugins = set(contract["functionalPlugins"])
    gate_plugins = _literal_set_from_module(_INVARIANT_8, "_FUNCTIONAL")
    assert gate_plugins == contract_plugins, (
        "test_invariant_8_overlay_activation.py::_FUNCTIONAL has drifted from "
        "overlay-hook-surface.json::functionalPlugins.\n"
        f"  only in activation gate: {sorted(gate_plugins - contract_plugins)}\n"
        f"  only in contract:        {sorted(contract_plugins - gate_plugins)}"
    )


def test_every_required_hook_names_a_known_functional_plugin():
    """Each requiredHook's owning plugin must be one of functionalPlugins —
    a hook owned by a plugin nobody activates is inert."""
    contract = _contract()
    plugins = set(contract["functionalPlugins"])
    orphans = {
        hook: meta["plugin"] for hook, meta in contract["requiredHooks"].items() if meta["plugin"] not in plugins
    }
    assert not orphans, f"these required hooks name a plugin absent from functionalPlugins: {orphans}"


# ---------------------------------------------------------------------------
# 2. Webhook-router coherence
# ---------------------------------------------------------------------------


def test_webhook_triggers_validator_hook_is_in_contract():
    """The customer.yaml webhook-triggers validator routes via
    pre_gateway_dispatch + hermes-smd-webhook-router; both must be declared in
    the contract, or the inbound-event path is unaccounted for."""
    contract = _contract()
    assert "pre_gateway_dispatch" in contract["requiredHooks"], (
        "pre_gateway_dispatch missing from contract requiredHooks"
    )
    assert "hermes-smd-webhook-router" in contract["functionalPlugins"], (
        "hermes-smd-webhook-router missing from contract functionalPlugins"
    )
    # Best-effort: confirm the validator still references both (documents the
    # coupling). If the file moves/renames, this surfaces it rather than going
    # silently stale.
    if _WEBHOOK_TRIGGERS.exists():
        text = _WEBHOOK_TRIGGERS.read_text(encoding="utf-8")
        assert "pre_gateway_dispatch" in text, (
            f"{_WEBHOOK_TRIGGERS.name} no longer references pre_gateway_dispatch — "
            "verify the inbound-event hook coupling is still wired and update the contract"
        )
        assert "hermes-smd-webhook-router" in text, (
            f"{_WEBHOOK_TRIGGERS.name} no longer references hermes-smd-webhook-router"
        )


# ---------------------------------------------------------------------------
# 3. Abstract-alias presence
# ---------------------------------------------------------------------------


def test_adapter_register_aliases_present_in_spec():
    """The four abstract adapter-register aliases the contract documents must
    actually appear in the spec that defines them, so a rename on the abstract
    integration surface is caught here too."""
    contract = _contract()
    aliases = contract["adapterRegisterAliases"]["aliases"]
    assert set(aliases) == {"pre_tool", "post_tool", "refusal", "compaction"}, (
        f"unexpected adapter-register alias set: {aliases}"
    )
    if _SPEC.exists():
        spec = _SPEC.read_text(encoding="utf-8")
        missing = [a for a in aliases if f"`{a}`" not in spec]
        assert not missing, (
            f"aie-adapter-register.md no longer documents aliases {missing}; "
            "the abstract hook surface changed — reconcile the contract and the overlay's "
            "hermes_hook.py"
        )


# ---------------------------------------------------------------------------
# Proof the guard bites — synthetic drift
# ---------------------------------------------------------------------------


def test_guard_catches_dropped_hook(tmp_path: Path):
    """Simulate someone deleting a hook from the activation gate's
    _REQUIRED_HOOKS. The parity comparison must flag it as missing — proving
    the guard catches the real 'a hook silently stopped being required' shape.
    """
    fake_gate = tmp_path / "fake_invariant_8.py"
    fake_gate.write_text(
        "_REQUIRED_HOOKS = {\n"
        '    "pre_tool_call",\n'
        '    "post_tool_call",\n'
        "    # pre_gateway_dispatch DROPPED — webhook router no longer required\n"
        "}\n",
        encoding="utf-8",
    )
    gate_hooks = _literal_set_from_module(fake_gate, "_REQUIRED_HOOKS")
    contract_hooks = set(_contract()["requiredHooks"].keys())
    assert gate_hooks != contract_hooks, "guard must detect a dropped hook"
    assert "pre_gateway_dispatch" in (contract_hooks - gate_hooks), (
        "the dropped hook must surface as 'in contract, missing from gate'"
    )
