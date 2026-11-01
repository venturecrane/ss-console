"""Phase B Cut C2 — every authored customer.yaml block is declared in the
block-materialization registry as implemented or intentionally inert.

The validator proves a block is well-SHAPED. This proves it is CONNECTED (or
consciously not). cron was authored, validated, and silently dropped — nothing
caught that nobody had wired it. This gate would have: a block authored in any
operator/customers/*/customer.yaml that is absent from the registry (or present
without a status) fails CI, forcing a conscious classification.

Run::

    cd operator && python3 -m pytest bin/tests/test_customer_yaml_block_conformance.py -v
"""

from __future__ import annotations

from pathlib import Path

import yaml

_OP = Path(__file__).resolve().parents[2]
_REGISTRY = _OP / "contracts" / "customer-yaml-blocks.yaml"
_CUSTOMERS = _OP / "customers"
_VALID_STATUS = {"implemented", "inert"}


def _registry() -> dict:
    return yaml.safe_load(_REGISTRY.read_text(encoding="utf-8")) or {}


def _customer_files() -> list[Path]:
    # Every seat, plus every preset of the one template (ADR 0100): a preset is
    # what a new seat inherits before anyone thinks about it, so it is checked too.
    files = sorted([*_CUSTOMERS.glob("*/customer.yaml"), *_CUSTOMERS.glob("_template/presets/*/customer.yaml")])
    assert files, "no customer.yaml files found to check"
    return files


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_registry_entries_are_well_formed() -> None:
    reg = _registry()
    for scope in ("top_level", "persona"):
        assert scope in reg, f"registry missing `{scope}` map"
        for name, spec in (reg[scope] or {}).items():
            assert isinstance(spec, dict), f"{scope}.{name}: entry is not a mapping"
            status = spec.get("status")
            assert status in _VALID_STATUS, f"{scope}.{name}: invalid status {status!r}"
            assert spec.get("materializer"), f"{scope}.{name}: no materializer named"
            if status == "inert":
                assert spec.get("note"), f"{scope}.{name}: inert blocks must carry a reason note"


def test_every_authored_top_level_block_is_declared() -> None:
    declared = set((_registry().get("top_level") or {}))
    for path in _customer_files():
        for key in _load(path):
            assert key in declared, (
                f"{path.relative_to(_OP)} authors top-level block `{key}` but it is NOT declared in "
                "customer-yaml-blocks.yaml. Declare it implemented (name the materializer) or inert "
                "(with a reason) — this is the gate that would have caught the cron silent-drop."
            )


def test_every_authored_persona_block_is_declared() -> None:
    declared = set((_registry().get("persona") or {}))
    for path in _customer_files():
        for persona in _load(path).get("personas") or []:
            for key in persona:
                assert key in declared, (
                    f"{path.relative_to(_OP)} persona authors block `{key}` but it is NOT declared in "
                    "customer-yaml-blocks.yaml `persona`. Declare it implemented or inert."
                )


def test_cron_is_implemented() -> None:
    """The block this registry exists for: cron must be marked implemented
    (closed by Cut C1), never silently inert again."""
    cron = (_registry().get("persona") or {}).get("cron") or {}
    assert cron.get("status") == "implemented", (
        "persona.cron must be `implemented` (ADR 0047 / Cut C1). If this regresses to inert, "
        "the silent-drop bug is back."
    )


# ---- ADR 0100: a block that belongs to a capability needs the capability ----

_CAPS = _OP / "hermes-smd-overlay" / "shared" / "capabilities.py"


def _capabilities():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_capabilities_for_conformance", _CAPS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _authors(caps, doc: dict, owner: str) -> bool:
    """The same predicate boot asks (shared/capabilities.py): a lane key is
    authored when a persona enables its skill, a connector key when an
    enabled connector binds one of its backends."""
    if owner in caps.JOB_LANES:
        return caps.authors_lane(doc, owner)
    return caps.authors_connector(doc, owner)


def _owned_blocks() -> dict[str, str]:
    return {
        name: spec["owner"]
        for name, spec in (_registry().get("top_level") or {}).items()
        if spec.get("owner", "core") != "core"
    }


def _refusals(caps, doc: dict) -> list[str]:
    return [
        f"{block} (owner {owner})"
        for block, owner in _owned_blocks().items()
        if doc.get(block) is not None and not _authors(caps, doc, owner)
    ]


def test_every_owner_names_a_capability_or_core() -> None:
    caps = _capabilities()
    known = set(caps.JOB_LANES) | set(caps.CONNECTOR_BACKENDS) | {"core"}
    for name, spec in (_registry().get("top_level") or {}).items():
        owner = spec.get("owner", "core")
        assert owner in known, f"top_level.{name}: owner {owner!r} is not a lane, a connector capability or core"
    for name, spec in (_registry().get("persona") or {}).items():
        assert "owner" not in spec, f"persona.{name}: ownership is declared on top-level blocks only"


def test_every_seat_authors_the_owner_of_each_block_it_authors() -> None:
    caps = _capabilities()
    for path in _customer_files():
        refused = _refusals(caps, _load(path))
        assert not refused, (
            f"{path.relative_to(_OP)} authors blocks whose capability it does not author: "
            f"{', '.join(refused)}. Their only reader is not running on that seat."
        )


def test_a_block_without_its_capability_is_refused() -> None:
    """Falsifier: the check above can fail. A smokeball-owned block on a seat
    that binds no Smokeball connector is refused; binding it clears it."""
    caps = _capabilities()
    block = next(b for b, o in _owned_blocks().items() if o == "smokeball")
    doc: dict = {block: {"x": 1}, "connectors": {}}
    assert _refusals(caps, doc) == [f"{block} (owner smokeball)"]
    doc["connectors"] = {"smokeball": {"backend": "mcp:smokeball"}}
    assert _refusals(caps, doc) == []
    doc["connectors"]["smokeball"]["enabled"] = False
    assert _refusals(caps, doc), "a disabled connector authors nothing"
