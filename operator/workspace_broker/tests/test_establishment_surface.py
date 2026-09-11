"""Public-surface contract for ``workspace_broker.establishment``.

WHY THIS EXISTS. `establishment.py` was split into four modules
(`establishment_constants`, `establishment_validation`, `pending_rule_store`,
`establishment_store`) with `establishment.py` kept as the import surface, so
that all five import sites in the repo — and anything on a live seat — keep
working untouched. This test is what makes "kept working" a fact rather than a
hope.

WHY NOT ``dir()`` EQUALITY. Comparing name sets is a check that cannot fail on
the two failures a module split actually produces: a constant whose literal was
mistyped while being moved, and a method dropped from a relocated class. Both
leave the name list byte-identical. So the fixture records, per name, the type;
for classes the full member list; for regexes the pattern source; and for plain
constants the value.

WHAT IS NOT COVERED. Names this module merely imported (``Path``, ``logging``,
``sha256``, ``_iso_utc`` ...) are excluded via ``__module__``: they are import
plumbing, not contract, and which of them stay visible is exactly what a
legitimate split changes. Compiled regexes are force-included despite reporting
``__module__ == 're'``, because ``re.compile(...)`` at module scope IS this
module's own state.

REGENERATE (only when the surface change is intended, and say why in the PR):

    UPDATE_ESTABLISHMENT_SURFACE=1 python -m pytest \
        workspace_broker/tests/test_establishment_surface.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from workspace_broker import establishment

FIXTURE = Path(__file__).parent / "fixtures" / "establishment_surface.json"

OWN_MODULES = {
    "workspace_broker.establishment",
    "workspace_broker.establishment_constants",
    "workspace_broker.establishment_validation",
    "workspace_broker.pending_rule_store",
    "workspace_broker.establishment_store",
}

VALUE_TYPES = (str, int, float, bool)


def _is_own(obj: object) -> bool:
    if isinstance(obj, re.Pattern):
        return True
    mod = getattr(obj, "__module__", None)
    if mod is None:
        return True
    return mod in OWN_MODULES


def _describe(obj: object) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": type(obj).__name__}
    if isinstance(obj, type):
        entry["members"] = sorted(m for m in dir(obj) if not m.startswith("__"))
    elif isinstance(obj, re.Pattern):
        entry["pattern"] = obj.pattern
    elif isinstance(obj, (frozenset, set)):
        entry["value"] = sorted(str(v) for v in obj)
    elif isinstance(obj, tuple):
        entry["value"] = [str(v) for v in obj]
    elif isinstance(obj, dict):
        entry["keys"] = sorted(str(k) for k in obj)
    elif isinstance(obj, VALUE_TYPES):
        entry["value"] = obj
    return entry


def _current_surface() -> dict[str, dict[str, Any]]:
    surface: dict[str, dict[str, Any]] = {}
    for name in dir(establishment):
        if name.startswith("__"):
            continue
        obj = getattr(establishment, name)
        if type(obj).__name__ == "module":
            continue
        if not _is_own(obj):
            continue
        surface[name] = _describe(obj)
    return surface


def test_public_surface_is_unchanged() -> None:
    current = _current_surface()

    if os.environ.get("UPDATE_ESTABLISHMENT_SURFACE") == "1":
        FIXTURE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return

    expected = json.loads(FIXTURE.read_text())

    missing = sorted(set(expected) - set(current))
    added = sorted(set(current) - set(expected))
    assert not missing, (
        f"establishment no longer exposes {missing}. A caller importing one of these "
        f"breaks at runtime, not at import of this test — re-export it from "
        f"establishment.py."
    )
    assert not added, (
        f"establishment now exposes {added}, which the recorded surface does not. "
        f"If intended, regenerate with UPDATE_ESTABLISHMENT_SURFACE=1 and say why."
    )

    differences = [
        f"{name}: expected {expected[name]!r}, got {current[name]!r}"
        for name in sorted(expected)
        if expected[name] != current[name]
    ]
    assert not differences, "public surface changed:\n" + "\n".join(differences)


def test_the_fixture_is_not_vacuous() -> None:
    """Law 12: a fixture that had silently emptied would make the test above
    pass while checking nothing. Pin the shape it must have."""
    expected = json.loads(FIXTURE.read_text())
    assert len(expected) > 60, f"surface fixture has only {len(expected)} names"

    classes = {n for n, v in expected.items() if v["type"] == "type"}
    assert {"EstablishmentStore", "PendingRuleStore", "EstablishmentValidationError"} <= classes

    store = expected["EstablishmentStore"]
    assert len(store["members"]) > 30, "EstablishmentStore member list looks truncated"
