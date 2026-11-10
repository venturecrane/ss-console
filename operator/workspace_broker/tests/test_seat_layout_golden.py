"""The seat-layout migration changed no path the broker resolves (W2-W4).

Each default below was a string spelled in its module until it was pointed at
the generated ``workspace_broker.seat_layout``. ``fixtures/seat_layout_golden.json``
holds the value each one had BEFORE the change, captured by importing the
modules at the pre-migration commit; every one must still be exactly that.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from workspace_broker import casefile_scheduled, casefile_verbs, litigation_intake, ops_close, records_verbs

#: Imported by name (never from a string), one per golden module.
MODULES: dict[str, ModuleType] = {
    m.__name__: m for m in (casefile_scheduled, casefile_verbs, litigation_intake, ops_close, records_verbs)
}

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "seat_layout_golden.json").read_text("utf-8"))


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_a_module_default_is_the_literal_it_replaced(key: str) -> None:
    module, attr = key.split(":")
    assert getattr(MODULES[module], attr) == GOLDEN[key]


def test_the_golden_covers_every_migrated_default() -> None:
    assert len(GOLDEN) == 5
    assert sorted(MODULES) == sorted({k.split(":")[0] for k in GOLDEN})
