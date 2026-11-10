"""The overlay's jobs-family path constants resolve to the values they always had.

The overlay reads its jobs-family paths from the generated ``shared.seat_layout``
(operator/contracts/seat-layout.yaml). Each row is a constant that was a
hand-written literal before the migration, with that literal: a regenerated
module or a wrong constant name changes a resolved value and fails here.
"""

from __future__ import annotations

import pytest

from bundle_installer import installer, manifest
from firm_inputs_applier import applier
from shared import fleet_facts, lane_proofs, record_store

# (resolved value, the literal it replaced), captured before the migration.
GOLDEN: dict[str, tuple[object, object]] = {
    "fleet_facts.DEFAULT_LANE_RUN_DIR": (fleet_facts.DEFAULT_LANE_RUN_DIR, "/run/smd-jobs"),
    "lane_proofs.DEFAULT_DIR": (lane_proofs.DEFAULT_DIR, "/run/smd-jobs"),
    "record_store.RESERVED_PREFIXES": (
        record_store.RESERVED_PREFIXES,
        (
            "/opt/data/profiles",
            "/opt/data/attachment-spool",
            "/opt/data/smokeball-extract-cache",
            "/opt/data/.smd",
            "/opt/data/medchron",
            "/opt/data/jobs",
        ),
    ),
    "applier.DEFAULT_VALIDATOR_PYTHON": (
        applier.DEFAULT_VALIDATOR_PYTHON,
        "/opt/jobs/.venv/bin/python",
    ),
    "installer._LINK_ROOTS": (installer._LINK_ROOTS, ("/usr/", "/opt/jobs/", "/opt/connectors/")),
    "manifest.JOBS.mounts": (
        manifest.JOBS.mounts,
        (("jobs", "/opt/jobs"), ("connectors/smokeball", "/opt/connectors/smokeball")),
    ),
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_a_constant_resolves_to_its_old_literal(name: str) -> None:
    value, literal = GOLDEN[name]
    assert value == literal
    assert type(value) is type(literal)
