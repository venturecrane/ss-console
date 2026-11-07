"""The voice gate is NEVER WEAKER since the sample-transform fallback was removed
(ss ADR 0083 decision 6, 2026-11-07), proven over every real seat's config.

BEFORE: a class declared ``output_classes.<class>.voice_spec: expected`` was
governed by the authored-spec regime; every other class fell to the
sample-transform fallback, which BOUND on every seat (all seven authored a
sample library, the snapshot this file read until the removal said so) and
PASSED an autonomous outside send only on a turn the transform demonstrably
reshaped; otherwise it downgraded to draft.

AFTER: a class declared ``expected`` keeps the spec regime, unchanged; every
other class is UNDECLARED and downgrades, always (reason
``no_voice_spec_declared``).

THE PROPERTY, per (seat x class), asserted below for every real seat and every
output class the recipient map can resolve, plus the unresolved class:

* every case the old gate bound is bound now (no class went unbound);
* every state in which the old gate downgraded downgrades now (the transform
  absent, which is every turn once it is removed);
* the one state the old gate let through, a turn the transform reshaped, now
  downgrades too: stricter, never weaker.

FALSIFIER (Law 12): rewriting ``resolve_binding_regime`` to answer
``REGIME_SPEC`` only for a declared class and anything that is not a downgrade
otherwise (say a string ``"unbound"``) fails
``test_no_seat_class_is_ever_unbound`` on every undeclared class of every seat,
ashton-price x outbound_client included.

The snapshot (``operator/contracts/gate-coverage-snapshot.overlay.json``) is
generated from ``operator/customers/*/customer.yaml`` by
``tests/gate-coverage-snapshot.test.ts`` with underscore-prefixed template dirs
excluded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import load_plugin

_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[3]
    / "operator"
    / "contracts"
    / "gate-coverage-snapshot.overlay.json"
)

#: Every output class the recipient-class map can resolve to, plus the two
#: internal-artifact classes and the unresolved case (``None``).
_CLASSES: tuple[str | None, ...] = (
    "staff",
    "outbound_client",
    "outbound_vendor",
    "outbound_external",
    "work_product",
    "record",
    None,
)


def _voice_gate():
    return load_plugin("hermes-smd-trust").enforce.voice_gate


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return json.loads(_SNAPSHOT_PATH.read_text())


def _declared(seat: dict, cls: str | None):
    return seat["output_classes"].get(cls) if cls else None


def _expected(decl) -> bool:
    return isinstance(decl, dict) and str(decl.get("voice_spec", "")).strip().lower() == "expected"


def _old(decl, transform_ran: bool) -> str:
    """The removed gate's outcome: every seat authored a sample library."""
    if _expected(decl):
        return "spec"
    return "pass" if transform_ran else "downgrade"


def _new(vg, decl) -> str:
    regime = vg.resolve_binding_regime(class_declaration=decl)
    return (
        "spec"
        if regime == vg.REGIME_SPEC
        else "downgrade"
        if regime == vg.REGIME_UNDECLARED
        else regime
    )


def test_snapshot_has_real_seats_and_no_templates(snapshot):
    seats = snapshot["seats"]
    assert len(seats) >= 7
    assert all(not slug.startswith("_") for slug in seats)
    assert all(set(seat) == {"output_classes"} for seat in seats.values())


def test_snapshot_excluded_exactly_the_template_dirs(snapshot):
    excluded = snapshot["excluded_template_dirs"]
    assert excluded, "template dirs exist console-side; an empty list means the glob broke"
    assert all(slug.startswith("_") for slug in excluded)


def test_no_seat_class_is_ever_unbound(snapshot):
    vg = _voice_gate()
    for slug, seat in snapshot["seats"].items():
        for cls in _CLASSES:
            got = _new(vg, _declared(seat, cls))
            assert got in ("spec", "downgrade"), f"{slug} x {cls}: the gate answered {got!r}"


def test_no_seat_class_loses_its_downgrade(snapshot):
    """Never weaker: wherever the old gate downgraded, the new one does; where it
    deferred to the spec regime, the new one still does."""
    vg = _voice_gate()
    for slug, seat in snapshot["seats"].items():
        for cls in _CLASSES:
            decl = _declared(seat, cls)
            new = _new(vg, decl)
            for transform_ran in (False, True):
                old = _old(decl, transform_ran)
                if old == "spec":
                    assert new == "spec", f"{slug} x {cls}: the spec regime must still govern"
                else:
                    assert new == "downgrade", (
                        f"{slug} x {cls} (transform_ran={transform_ran}): {old} -> {new}"
                    )


def test_the_client_seats_outside_classes_downgrade(snapshot):
    """The case the decision was made for: the client seat declares a spec only for
    staff and work_product, and its client and vendor sends can be raised to
    autonomous by the portal dial."""
    vg = _voice_gate()
    seat = snapshot["seats"]["ashton-price"]
    for cls in ("outbound_client", "outbound_vendor", "outbound_external"):
        assert _new(vg, _declared(seat, cls)) == "downgrade"
    for cls in ("staff", "work_product"):
        assert _new(vg, _declared(seat, cls)) == "spec"


@pytest.mark.parametrize(
    "decl, want",
    [
        ({"voice_spec": "expected"}, "spec"),
        ({"voice_spec": " Expected "}, "spec"),
        ({"voice_spec": "none"}, "downgrade"),
        ({}, "downgrade"),
        (None, "downgrade"),
        ("expected", "downgrade"),  # a malformed declaration is not a declaration
    ],
)
def test_the_pure_rule(decl, want):
    assert _new(_voice_gate(), decl) == want


def per_seat_table(snapshot: dict) -> list[tuple[str, str, str, str]]:
    """(seat, class, before, after) for the PR body; before is the removed gate's
    answer as (transform absent / transform ran)."""
    vg = _voice_gate()
    rows = []
    for slug, seat in sorted(snapshot["seats"].items()):
        for cls in _CLASSES:
            decl = _declared(seat, cls)
            rows.append((slug, str(cls), f"{_old(decl, False)}/{_old(decl, True)}", _new(vg, decl)))
    return rows
