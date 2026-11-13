"""Every golden set the integrity job (``golden-integrity.py``) regenerates from
the merge-base tree, and the instrument it lays over that tree.

A golden set not listed here is a golden a PR can rewrite unchecked, so
``bin/tests/test_golden_integrity_families.py`` fails when an
``allowed-changes`` file exists that no family names.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The PR's instrument, laid over the base tree: the golden test dirs (their
#: kits, testkits, conftest and inputs), the shared golden modules and the
#: seat-config caller capture.
OVERLAY = (
    "runners/jobs/tests",
    "workspace_broker/tests",
    "bin/lib/golden_diff.py",
    "bin/lib/golden_broker.py",
    "bin/lib/seat_callers",
    "bin/capture-seat-config-callers.py",
)
#: Where a dropped case, a newly not-applicable cell or a changed normaliser
#: (``golden_manifest.py``) is named, as ``<cases>`` / ``<normaliser>`` entries.
INSTRUMENT_ALLOWED = "bin/goldens/allowed-instrument-changes.json"


@dataclass(frozen=True)
class Family:
    name: str
    #: The committed goldens, under ``operator/``.
    root: str
    #: Which files under ``root`` are this set's goldens.
    pattern: str
    #: Its allowed list, under ``operator/``.
    allowed: str
    #: ``out``: the harness writes to ``$JOBS_GOLDENS_OUT/<out>``; ``inplace``:
    #: it rewrites the files under ``root`` (cleared first).
    regen: str
    out: str = ""
    #: File names that are fixtures the harness reads, never regenerated (held
    #: to the base's copy by the "nothing pinned is lost" check only).
    inputs: tuple[str, ...] = ()
    #: File names an in-place set keeps before regenerating: they hold the
    #: stored inputs the harness replays, and it rewrites their answers.
    keep: tuple[str, ...] = ()
    #: The runner test files that write an in-place set.
    tests: tuple[str, ...] = ()


_RG = "runners/jobs/tests/goldens"
FAMILIES: tuple[Family, ...] = (
    Family("host_matrix", f"{_RG}/host_matrix", "*", f"{_RG}/host_matrix/allowed-changes.json", "out", "host_matrix"),
    Family("wire", _RG, "wire.json", f"{_RG}/wire.allowed-changes.json", "out"),
    Family(
        "ledgers",
        "workspace_broker/tests/goldens/ledgers",
        "*/*",
        "workspace_broker/tests/goldens/ledgers/allowed-changes.json",
        "out",
        "ledgers",
        inputs=("aged.json",),
    ),
    Family(
        "lane_endings",
        f"{_RG}/lane_endings",
        "*",
        f"{_RG}/lane_endings/allowed-changes.json",
        "inplace",
        tests=("test_lane_goldens.py",),
    ),
    Family(
        "paid_calls",
        f"{_RG}/paid_calls",
        "*",
        f"{_RG}/paid_calls/allowed-changes.json",
        "inplace",
        tests=("test_paid_call_goldens.py", "test_model_fact_goldens.py"),
    ),
    Family(
        "firm_config",
        f"{_RG}/firm_config",
        "*/*",
        f"{_RG}/firm_config/allowed-changes.json",
        "inplace",
        tests=("test_firm_config_goldens.py",),
    ),
    Family(
        "text_rules",
        f"{_RG}/text_rules",
        "*",
        f"{_RG}/text_rules/allowed-changes.json",
        "inplace",
        keep=("jobs.*.json",),
        tests=("test_text_rule_goldens.py",),
    ),
    Family(
        "seat_callers",
        "contracts",
        "seat-config-callers.json",
        "bin/goldens/allowed-seat-config-callers.json",
        "out",
        "contracts",
    ),
)

__all__ = ["FAMILIES", "INSTRUMENT_ALLOWED", "OVERLAY", "Family"]
