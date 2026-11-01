"""Every customer.yaml declares what its seat IS.

WHY THIS GATE EXISTS. Nothing in this repo said what a seat was. `fly status`
reports `started` for a Machine serving nobody; the only `status:` in any
customer.yaml is on the persona and reads `active` on all eight seats including
`_template`; and the single place recording "ashton-price has no live Machine"
was a prose sentence in an upgrade runbook that had since gone stale. A seat
provisioned, connected to nothing, and serving no one was described in this
session as a live client seat on exactly that evidence.

WHY THE VALIDATOR DOES NOT ENFORCE THIS. `seat:` validates as OPTIONAL. The
portal's `reconstructFromProjection` builds a synthetic customer.yaml root out
of lossy D1 columns and hands it to the same validator; a required field the
projection cannot carry would turn the settings editor into a config error for
every customer. That is #1965 exactly, and making this block required would
have rebuilt it. So shape is proven in the validator and COMPLETENESS is proven
here, where the real files are in reach and no projection is involved.

Run::

    cd operator && python3 -m pytest bin/tests/test_seat_descriptor_conformance.py -v
"""

from __future__ import annotations

from pathlib import Path

import yaml

_OP = Path(__file__).resolve().parents[2]
_CUSTOMERS = _OP / "customers"

_KINDS = {"customer", "proving", "sandbox", "internal", "preprod"}
_PRODUCTS = {"operator", "hosted-agent"}


def _customer_files() -> list[Path]:
    # Every seat, plus every preset of the one template (ADR 0100): a preset is
    # what a new seat inherits before anyone thinks about it, so it is checked too.
    files = sorted([*_CUSTOMERS.glob("*/customer.yaml"), *_CUSTOMERS.glob("_template/presets/*/customer.yaml")])
    assert files, "no customer.yaml files found to check"
    return files


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_every_customer_declares_a_seat_descriptor() -> None:
    for path in _customer_files():
        seat = _load(path).get("seat")
        assert isinstance(seat, dict), (
            f"{path.relative_to(_OP)} does not declare `seat:`. Every seat must say what it is — "
            "kind (customer / proving / sandbox / internal / preprod) and product. Templates "
            "included: a template's value is what a new seat inherits before anyone thinks about it."
        )
        assert seat.get("kind") in _KINDS, f"{path.relative_to(_OP)}: seat.kind={seat.get('kind')!r}"
        assert seat.get("product") in _PRODUCTS, f"{path.relative_to(_OP)}: seat.product={seat.get('product')!r}"


def test_seat_descriptor_carries_no_lifecycle_state() -> None:
    """Kind is authored; state is derived. This gate keeps it that way.

    The moment a `status`/`state`/`stage` field appears here, it becomes a claim
    an agent can write and a reviewer can believe, and it starts drifting from
    the Machine the instant it is written. Lifecycle belongs to probes of the
    running system — is the app there, does it boot clean, does it hold a live
    connector token, has it produced anything for a human — never to this file.
    """
    forbidden = {"status", "state", "stage", "lifecycle", "provisioned", "connected", "serving", "live"}
    for path in _customer_files():
        seat = _load(path).get("seat") or {}
        offenders = forbidden & set(seat)
        assert not offenders, (
            f"{path.relative_to(_OP)}: seat.{sorted(offenders)[0]} is lifecycle STATE, which must never "
            "be authored. Derive it from a probe of the running system instead — an authored state "
            "field is the same defect as the persona `status: active` that reads identically on a "
            "seat serving a law firm and on _template."
        )


def test_templates_default_to_the_most_cautious_kind() -> None:
    """A new seat inherits its template's kind before anyone has thought about it.

    Fail-safe direction matters here: an unlabelled seat should be treated as if
    it holds a real client's data, never as a proving rig that can be handled
    casually. So templates carry `customer`.
    """
    presets = sorted(_CUSTOMERS.glob("_template/presets/*/customer.yaml"))
    # A check over an empty set measures nothing: the one template keeps its presets.
    assert {p.parent.name for p in presets} >= {"pi-law-firm", "self-serve", "couples"}, presets
    for path in presets:
        kind = (_load(path).get("seat") or {}).get("kind")
        assert kind == "customer", (
            f"preset {path.parent.name} declares seat.kind={kind!r}. Templates must default to "
            "`customer` so a seat nobody has relabelled inherits the MOST caution, not the least."
        )


def test_one_template_no_per_offering_scaffolds() -> None:
    """ADR 0100: one Operator, one template; an offering is a preset inside it.

    A second scaffold directory beside ``_template`` is how "Hosted Agent" and
    "Operator" drifted into two products. Presets live under _template/presets/.
    """
    scaffolds = sorted(p.name for p in _CUSTOMERS.iterdir() if p.is_dir() and p.name.startswith("_"))
    assert scaffolds == ["_template"], scaffolds
