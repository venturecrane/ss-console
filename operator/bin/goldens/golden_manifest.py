#!/usr/bin/env python3
"""What a tree's golden harness declares, as one JSON document: the cases each
golden set runs and the normalisers it applies.

``golden-integrity.py`` runs this against the merge-base tree as committed and
against the PR's tree, and fails on a case the PR drops, a cell it declares
not applicable, or a normaliser it changes, unless
``bin/goldens/allowed-instrument-changes.json`` names it. A golden that pins
less than it did is otherwise invisible: the harness that would show the loss
is the one that changed.

    python3 bin/goldens/golden_manifest.py <operator dir>

Run with the jobs runner's interpreter (the harness imports the runner). A
module the tree does not have yet is left out (nothing to lose).
"""

from __future__ import annotations

import contextlib
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

#: Harness modules whose normalisers are pinned: every module-level compiled
#: pattern, and the source of every function or class named here.
NORMALISERS: dict[str, tuple[str, ...]] = {
    "host_matrix_normal": ("Normal", "fixed_root", "placeholder"),
    "host_matrix_kit": ("freeze_broker_clock", "seed_random"),
    "goldens_kit": ("plain", "scrub_tmp", "canon"),
    "test_lane_goldens": ("_normal",),
    "wire_kit": ("shape", "traffic"),
    "test_wire_snapshot": ("direct",),
    "ledger_golden_kit": ("normal", "fix_clock", "Clock"),
    "filing_kit": ("normal", "_row"),
}


def _paths(operator: Path) -> None:
    for p in (
        operator,
        operator / "runners" / "jobs" / "tests",
        operator / "workspace_broker" / "tests",
        operator / "runners" / "jobs",
        operator / "connectors" / "_sdk",
        operator / "connectors" / "_contract",
        operator / "connectors" / "smokeball",
    ):
        sys.path.insert(0, str(p))


def _load() -> dict[str, Any]:
    """The harness modules the tree has, each imported by its literal name."""
    out: dict[str, Any] = {}
    with contextlib.suppress(ImportError):
        import host_matrix_cells

        out["host_matrix_cells"] = host_matrix_cells
    with contextlib.suppress(ImportError):
        import host_matrix_normal

        out["host_matrix_normal"] = host_matrix_normal
    with contextlib.suppress(ImportError):
        import host_matrix_kit

        out["host_matrix_kit"] = host_matrix_kit
    with contextlib.suppress(ImportError):
        import goldens_kit

        out["goldens_kit"] = goldens_kit
    with contextlib.suppress(ImportError):
        import test_lane_goldens

        out["test_lane_goldens"] = test_lane_goldens
    with contextlib.suppress(ImportError):
        import test_paid_call_goldens

        out["test_paid_call_goldens"] = test_paid_call_goldens
    with contextlib.suppress(ImportError):
        import test_firm_config_goldens

        out["test_firm_config_goldens"] = test_firm_config_goldens
    with contextlib.suppress(ImportError):
        import text_rule_kit

        out["text_rule_kit"] = text_rule_kit
    with contextlib.suppress(ImportError):
        import wire_kit

        out["wire_kit"] = wire_kit
    with contextlib.suppress(ImportError):
        import test_wire_snapshot

        out["test_wire_snapshot"] = test_wire_snapshot
    with contextlib.suppress(ImportError):
        import ledger_golden_kit

        out["ledger_golden_kit"] = ledger_golden_kit
    with contextlib.suppress(ImportError):
        import filing_kit

        out["filing_kit"] = filing_kit
    with contextlib.suppress(ImportError):
        import test_filing_goldens

        out["test_filing_goldens"] = test_filing_goldens
    return out


_MODULES: dict[str, Any] = {}


def _module(name: str) -> Any:
    return _MODULES.get(name)


def cases() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cells = _module("host_matrix_cells")
    if cells is not None:
        out["host_matrix.cells"] = sorted(cells.CELLS)
        out["host_matrix.not_applicable"] = sorted(cells.NOT_APPLICABLE)
    for key, mod, attr in (
        ("lane_endings.cases", "test_lane_goldens", "CASES"),
        ("paid_calls.cases", "test_paid_call_goldens", "CASES"),
        ("firm_config.cases", "test_firm_config_goldens", "CASES"),
        ("text_rules.targets", "text_rule_kit", "TARGETS"),
        ("wire.direct", "test_wire_snapshot", "DIRECT"),
        ("filing.cells", "test_filing_goldens", "CELLS"),
        ("filing.frozen", "test_filing_goldens", "FROZEN"),
        ("filing.not_applicable", "test_filing_goldens", "NOT_APPLICABLE"),
    ):
        m = _module(mod)
        if m is not None and hasattr(m, attr):
            out[key] = sorted("/".join(c) if isinstance(c, tuple) else str(c) for c in getattr(m, attr))
    ledgers = _module("ledger_golden_kit")
    if ledgers is not None:
        out["ledgers.lanes"] = sorted(ledgers.LANES)
    return out


def normalisers() -> dict[str, str]:
    out: dict[str, str] = {}
    for mod_name, names in NORMALISERS.items():
        m = _module(mod_name)
        if m is None:
            continue
        for attr, value in sorted(vars(m).items()):
            if isinstance(value, re.Pattern):
                out[f"{mod_name}.{attr}"] = f"{value.pattern} /{int(value.flags)}"
        for name in names:
            obj = getattr(m, name, None)
            if obj is not None:
                out[f"{mod_name}.{name}"] = inspect.getsource(obj)
    return out


def manifest(operator: Path) -> dict[str, Any]:
    _paths(operator)
    _MODULES.update(_load())
    return {"cases": cases(), "normalisers": normalisers()}


if __name__ == "__main__":
    print(json.dumps(manifest(Path(sys.argv[1]).resolve()), sort_keys=True))
