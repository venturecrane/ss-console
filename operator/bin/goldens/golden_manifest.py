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
import hashlib
import importlib.util
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
    "reader_kit": (
        "scrub",
        "_tmp_out",
        "tree",
        "paged",
        "shape",
        "page_of",
        "reply_for",
        "_colour_page",
        "Seat",
        "Model",
    ),
}

#: The decision corpus's instrument (wave 4), under ``operator/``: frozen for the
#: PRs that split the two decision functions, so each file is pinned whole, as a
#: hash under ``decisions_frozen.<name>``. A changed recorder, census, case
#: table or replay is a changed normaliser.
DECISIONS = "hermes-smd-overlay/tests/decisions"
FROZEN = ("recorder.py", "census.py", "tables.py", "cases.py", "drive.py", "replay.py")
#: The reader family's instrument (wave 5), under ``operator/``: frozen the same
#: way for the PRs that move every lane onto one reader, one downloader and one
#: page store (``reader_frozen.<name>``). A changed fixture, recorder, entry
#: point or census is a changed normaliser, named in
#: ``allowed-instrument-changes.json``.
READER = "runners/jobs/tests"
READER_FROZEN = (
    "reader_fixtures.py",
    "reader_kit.py",
    "reader_lanes.py",
    "reader_pulls.py",
    "reader_names.py",
    "reader_connector.py",
    "test_reader_goldens.py",
    "test_reader_census.py",
)


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
    with contextlib.suppress(ImportError):
        import reader_kit

        out["reader_kit"] = reader_kit
    with contextlib.suppress(ImportError):
        import test_reader_goldens

        out["test_reader_goldens"] = test_reader_goldens
    return out


def _decisions(operator: Path) -> None:
    """The corpus tables, loaded by file (stdlib only, no ``tests`` package)."""
    for name in ("tables", "cases"):
        path = operator / DECISIONS / f"{name}.py"
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(f"decisions_{name}", path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        with contextlib.suppress(ImportError):
            spec.loader.exec_module(mod)
            _MODULES[spec.name] = mod


def decision_cases() -> dict[str, list[str]]:
    """Every name the corpus tables list: a dropped one is a dropped case, and
    a newly unrecorded call (``not_applicable``) is named the same way."""
    out: dict[str, list[str]] = {}
    t = _module("decisions_tables")
    if t is not None:
        for d in t.DECISIONS:
            out[f"decisions.{d.key}.wrap"] = sorted(d.wrap)
            out[f"decisions.{d.key}.local"] = sorted(f"{n}={h}" for n, h in d.local)
            out[f"decisions.{d.key}.edges"] = sorted(d.edges)
            out[f"decisions.{d.key}.registers"] = sorted(d.registers)
            out[f"decisions.{d.key}.resets"] = sorted(d.resets)
            out[f"decisions.{d.key}.not_applicable"] = sorted(n for n, _ in d.unwrapped)
        out["decisions.shared_edges"] = sorted(t.SHARED_EDGES)
        out["decisions.hooks"] = sorted("/".join(h) for h in t.HOOKS)
        out["decisions.selection"] = sorted(t.SELECTION)
        out["decisions.unstable.not_applicable"] = sorted(n for n, _ in t.UNSTABLE)
    c = _module("decisions_cases")
    if c is not None:
        out["decisions.cases"] = sorted(c.case_names())
    return out


def frozen(operator: Path) -> dict[str, str]:
    out = {}
    for name in FROZEN:
        path = operator / DECISIONS / name
        if path.is_file():
            out[f"decisions_frozen.{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in READER_FROZEN:
        path = operator / READER / name
        if path.is_file():
            out[f"reader_frozen.{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
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
        ("reader.cells", "test_reader_goldens", "CELLS"),
        ("reader.not_applicable", "test_reader_goldens", "NOT_APPLICABLE"),
    ):
        m = _module(mod)
        if m is not None and hasattr(m, attr):
            out[key] = sorted("/".join(c) if isinstance(c, tuple) else str(c) for c in getattr(m, attr))
    ledgers = _module("ledger_golden_kit")
    if ledgers is not None:
        out["ledgers.lanes"] = sorted(ledgers.LANES)
    out.update(decision_cases())
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
    _decisions(operator)
    return {"cases": cases(), "normalisers": {**normalisers(), **frozen(operator)}}


if __name__ == "__main__":
    print(json.dumps(manifest(Path(sys.argv[1]).resolve()), sort_keys=True))
