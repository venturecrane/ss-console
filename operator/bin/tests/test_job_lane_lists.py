"""Every job-lane list the fleet tools hold equals operator/contracts/job-lanes.yaml.

The declaration is generated into ``bin/lib/job_lanes.py``. Each list below was
a hand copy; this compares it with the generated constant (by the column that
says how it differs), so a lane added to the yaml and missed here, or a hand
list edited alone, fails.
"""

from __future__ import annotations

from bin.lib import job_lanes as jl
from bin.lib import work_health
from bin.lib.fleet.build import LANE_SCHEMA_MODULES
from bin.lib.fleet.lane_validate import LANE_VALIDATORS
from bin.lib.fleet.preflight import LANE_VAULT_MARKERS


def _marker(lane: str) -> str:
    """The vault object that pins a lane's inputs (its manifest)."""
    return f"{lane}-firm.yaml" if lane in jl.FIRM_INPUT_FILE_LANES else f"{lane}/{lane}-firm.yaml"


def test_work_health_reads_each_lanes_audit_prefix() -> None:
    assert work_health.LANE_PREFIXES == jl.AUDIT_PREFIX_LANE


def test_preflight_knows_each_firm_input_lanes_vault_marker() -> None:
    expected = {lane: f"{lane}-firm.yaml" if lane in jl.FIRM_INPUT_FILE_LANES else f"{lane}/" for lane in jl.FIRM_INPUT_LANES}
    assert LANE_VAULT_MARKERS == expected


def test_lane_validate_judges_each_firm_input_lane_with_its_module() -> None:
    assert LANE_VALIDATORS == {lane: (_marker(lane), jl.LANE_FIRM_MODULE[lane]) for lane in jl.FIRM_INPUT_LANES}


def test_build_hashes_each_firm_input_lanes_schema_module() -> None:
    expected = {
        lane: "operator/runners/jobs/" + jl.LANE_FIRM_MODULE[lane].replace(".", "/") + ".py" for lane in jl.FIRM_INPUT_LANES
    }
    assert LANE_SCHEMA_MODULES == expected
