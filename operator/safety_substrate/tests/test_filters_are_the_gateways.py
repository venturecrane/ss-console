"""The safety filters the substrate proves are the ones the gateway runs.

The citation filter, the identifier filter and the sticky-stop machine exist
once, in the overlay's ``shared`` package, which is what the gateway imports.
They used to exist twice: a copy here under ``safety_substrate/`` and a copy in
the overlay. Nothing on a seat imported the substrate copies; they ran only
inside this directory's own tests, including the boot invariant bootstrap.sh
runs before the gateway starts. So the boot gate could pass on a filter the
gateway did not run, and the identifier-filter copy here had already fallen
behind the overlay's (no ordinal dates, no money register, no captions).

These checks fail if a substrate copy comes back or a test here stops
importing the gateway's module. Pytest-mode only (no ``run()``), so the boot
runner skips this file; it gates in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_SUBSTRATE = Path(__file__).resolve().parents[1]
_OPERATOR = _SUBSTRATE.parent
_OVERLAY_SHARED = _OPERATOR / "hermes-smd-overlay" / "shared"
_FILTERS = ("citation_filter", "identifier_filter", "sticky_stop")


@pytest.mark.parametrize("name", _FILTERS)
def test_the_substrate_carries_no_copy(name: str) -> None:
    assert not (_SUBSTRATE / f"{name}.py").exists(), (
        f"safety_substrate/{name}.py is back. The gateway imports shared.{name}; a second copy here "
        "is a copy the boot gate can prove while the seat runs the other one. Change the overlay's."
    )
    assert (_OVERLAY_SHARED / f"{name}.py").is_file()


@pytest.mark.parametrize("name", _FILTERS)
def test_the_imported_module_is_the_overlays(name: str) -> None:
    module = __import__(f"shared.{name}", fromlist=[name])
    assert Path(module.__file__).resolve() == (_OVERLAY_SHARED / f"{name}.py").resolve()


def test_no_test_here_imports_a_substrate_copy() -> None:
    pattern = re.compile(r"safety_substrate\.(?:%s)\b" % "|".join(_FILTERS))
    offenders = [
        p.name
        for p in sorted(Path(__file__).parent.glob("*.py"))
        if p.name != Path(__file__).name and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"these import a substrate copy that no longer exists: {offenders}"


def test_the_boot_invariant_imports_the_gateways_citation_filter() -> None:
    # Load the boot invariant the way the boot runner does and check that the
    # functions it proves are the gateway module's own objects, not copies.
    import importlib.util

    import shared.citation_filter as gateway

    path = Path(__file__).parent / "test_invariant_6_no_citations.py"
    spec = importlib.util.spec_from_file_location("_boot_invariant_6", path)
    assert spec is not None and spec.loader is not None
    invariant = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(invariant)
    for name in ("contains_citation", "refusal_message", "scan"):
        assert getattr(invariant, name) is getattr(gateway, name), name


def test_no_overlay_pair_names_a_retired_substrate_copy() -> None:
    manifest = json.loads((_OPERATOR / "contracts" / "overlay-pairs.json").read_text(encoding="utf-8"))
    retired = {f"operator/safety_substrate/{name}.py" for name in _FILTERS}
    assert not retired & {p["adapterPath"] for p in manifest["pairs"]}
