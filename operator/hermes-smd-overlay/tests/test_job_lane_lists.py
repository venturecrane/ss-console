"""Every job-lane list the gateway holds equals operator/contracts/job-lanes.yaml.

The declaration is generated into ``shared/job_lanes.py``. Each list below was a
hand copy; this compares it with the generated constant (by the column that
says how it differs), so a lane added to the yaml and missed here, or a hand
list edited alone, fails. Where the module now reads the generated constant the
check still holds the name it exports to its callers.
"""

from __future__ import annotations

import re

from firm_inputs_applier import applier
from shared import capabilities, fleet_facts, heartbeat, request_cards, turn_sources
from shared import job_lanes as jl
from tests.conftest import load_plugin


def test_capabilities_names_every_lane_with_its_skill_in_declared_order() -> None:
    # The order is the tool-registration order hermes-smd-lanes walks.
    assert list(capabilities.JOB_LANES.items()) == list(jl.LANE_SKILL.items())


def test_every_lane_writes_a_delivery_proof_row() -> None:
    assert heartbeat.DELIVERY_PROOF_LANES == frozenset(jl.JOB_LANES)


def test_request_card_lanes() -> None:
    assert set(request_cards.JOB_LANES) == set(jl.REQUEST_CARD_LANES)
    assert len(request_cards.JOB_LANES) == len(jl.REQUEST_CARD_LANES)


def test_firm_input_lanes_the_heartbeat_reports() -> None:
    assert set(fleet_facts.FIRM_INPUT_LANES) == set(jl.FIRM_INPUT_LANES) | set(jl.FIRM_INPUT_EXTRAS)
    assert len(fleet_facts.FIRM_INPUT_LANES) == len(jl.FIRM_INPUT_LANES) + len(jl.FIRM_INPUT_EXTRAS)


def test_wake_words_and_scheduled_kinds() -> None:
    assert turn_sources.JOB_KIND == jl.WAKE_WORD_JOB_KIND
    assert turn_sources.SCHEDULED_KINDS == frozenset(jl.SCHEDULED_JOB_KINDS)
    words = re.search(r"\(([a-z|]+)\) job", turn_sources.WAKE_JOB.pattern)
    assert words is not None and set(words.group(1).split("|")) == set(jl.WAKE_WORD_JOB_KIND)


def test_the_firm_inputs_applier_lanes() -> None:
    by_name = {lane.name: lane for lane in applier.LANES}
    assert set(by_name) == set(jl.FIRM_INPUT_LANES) | set(jl.FIRM_INPUT_EXTRAS)
    for name in jl.FIRM_INPUT_LANES:
        lane = by_name[name]
        assert lane.marker == f"{name}-firm.yaml"
        assert lane.single_file is (name in jl.FIRM_INPUT_FILE_LANES)
        assert lane.broker_copy == (
            f"{name}-broker-firm.yaml" if name in jl.BROKER_FIRM_COPY_LANES else None
        )


def test_the_lanes_plugin_offers_tools_for_every_lane() -> None:
    plugin = load_plugin("hermes-smd-lanes")
    assert list(plugin.LANE_TOOLS) == list(jl.JOB_LANES)
