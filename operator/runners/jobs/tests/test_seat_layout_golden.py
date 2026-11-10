"""The seat-layout migration changed no path the jobs runner resolves (W2-W4).

Each default below was a string spelled in its module until it was pointed at
the generated ``jobs.seat_layout``. ``fixtures/seat_layout_golden.json`` holds the
value each one had BEFORE the change, captured by importing the modules at the
pre-migration commit; every one must still be exactly that.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

import jobs.casefile.book
import jobs.casefile.firm
import jobs.demand.firm
import jobs.drafting.firm
import jobs.drafting.firm_rules
import jobs.firm_wording
import jobs.litigation.firm
import jobs.medchron.config
import jobs.note_by_smd
import jobs.records.firm
import jobs.records_lane
import jobs.rewake

#: Imported by name (never from a string), one per golden module.
MODULES: dict[str, ModuleType] = {
    m.__name__: m
    for m in (
        jobs.casefile.book,
        jobs.casefile.firm,
        jobs.demand.firm,
        jobs.drafting.firm,
        jobs.drafting.firm_rules,
        jobs.firm_wording,
        jobs.litigation.firm,
        jobs.medchron.config,
        jobs.note_by_smd,
        jobs.records.firm,
        jobs.records_lane,
        jobs.rewake,
    )
}

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "seat_layout_golden.json").read_text("utf-8"))


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_a_module_default_is_the_literal_it_replaced(key: str) -> None:
    module, attr = key.split(":")
    assert getattr(MODULES[module], attr) == GOLDEN[key]


def test_the_golden_covers_every_migrated_default() -> None:
    # jobs/attorneys/roster.py keeps its literal: it is the connector SDK's
    # library/roster.py byte for byte (tests/test_attorney_roster.py).
    assert len(GOLDEN) == 12
    assert sorted(MODULES) == sorted({k.split(":")[0] for k in GOLDEN})
