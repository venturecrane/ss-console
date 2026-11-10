"""Every authored seat registers exactly the tools and hooks it registered before.

WHY. The overlay reaches every seat on merge, live client seats included. A
capability built for one seat (a partitioned record store, a switch that turns
per-sender memory off) must register only where a seat's config authors it; a
new tool on a client seat's menu, or a new ``pre_llm_call`` hook adding to its
prompt, is a change that client never asked for. This test makes that a fact
checked on every PR rather than a claim in a description: for each authored
``operator/customers/*/customer.yaml``, the real ``register`` of every
sub-plugin ``plugin.yaml`` lists is run against that seat's config, and what it
registered (tools by name, owning plugin and toolset; hooks by name and owning
plugin) must equal the committed snapshot,
``tests/fixtures/authored_seat_registration.json``.

The snapshot was taken on origin/main at b5f4cbb22, before the partitioned
record store and the per-sender memory switch were added, and the code that
adds them leaves it unchanged. A PR that MEANS to change a seat's surface
regenerates it and the diff shows, seat by seat, what changed::

    cd operator/hermes-smd-overlay
    python -m tests._registration_surface tests/fixtures/authored_seat_registration.json

Its falsifier is the second test: a seat whose config switches per-sender memory
off must come out different, so the collector is shown able to see a change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests._registration_surface import authored_seats, collect
from tests.conftest import load_plugin

SNAPSHOT = Path(__file__).parent / "fixtures" / "authored_seat_registration.json"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("seat", authored_seats(), ids=lambda p: p.parent.name)
def test_an_authored_seat_registers_what_it_registered_before(seat: Path) -> None:
    expected = _snapshot().get(seat.parent.name)
    assert expected is not None, (
        f"{seat.parent.name} has no entry in {SNAPSHOT.name}; regenerate it (see the module "
        "docstring) and review the new seat's surface in the diff"
    )
    actual = collect(seat, load_plugin)
    added = sorted(set(actual["tools"]) - set(expected["tools"]))
    removed = sorted(set(expected["tools"]) - set(actual["tools"]))
    assert not added and not removed, f"{seat.parent.name}: tools added {added}, removed {removed}"
    assert actual["tools"] == expected["tools"], f"{seat.parent.name}: a tool moved plugin or toolset"
    assert actual["hooks"] == expected["hooks"], (
        f"{seat.parent.name}: hooks changed: added "
        f"{sorted(map(tuple, actual['hooks']), key=str)} vs {sorted(map(tuple, expected['hooks']), key=str)}"
    )


def test_the_collector_sees_a_change(tmp_path: Path) -> None:
    """The falsifier: the guide seat with per-sender memory switched off does
    not match its snapshot. If this passed with no difference, the snapshot
    test above could not have failed either."""
    guide = next(p for p in authored_seats() if p.parent.name == "smd-guide")
    doc = yaml.safe_load(guide.read_text(encoding="utf-8"))
    doc["memory"]["peer_memory_enabled"] = False
    changed = tmp_path / "smd-guide" / "customer.yaml"
    changed.parent.mkdir()
    changed.write_text(yaml.safe_dump(doc), encoding="utf-8")
    actual = collect(changed, load_plugin)
    expected = _snapshot()["smd-guide"]
    assert "record_peer_preference" in expected["tools"]
    assert "record_peer_preference" not in actual["tools"]
    assert ["pre_llm_call", "hermes-smd-peer-memory"] in expected["hooks"]
    assert ["pre_llm_call", "hermes-smd-peer-memory"] not in actual["hooks"]
