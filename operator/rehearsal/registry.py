"""Load and validate the scenario registry.

A scenario is DATA plus a named driver. The data says what hostile act to
perform and what observable outcome must follow; the driver knows how to speak
on a channel. Splitting them that way is deliberate: adding an incident class to
the suite should be a YAML file and a review, not a new script that quietly
invents its own idea of what passing means.

VALIDATION IS THE POINT OF THIS MODULE, not parsing. Three classes of defect are
refused at load time, before anything is driven:

* Scope. Every address a scenario touches, in any position including prose,
  must be an authored harness mailbox (``scope.assert_scenario_in_scope``).
* Shape. A scenario must declare the incident it replays, the hostile act, its
  own falsifier, and at least one leg with at least one expectation the scorer
  actually evaluates. A leg with no evaluable expectation cannot fail, and a
  check that cannot fail measured nothing.
* Vocabulary. Audit action types named in expectations must exist in
  ``operator/contracts/audit-action-vocabulary.json``. A scenario waiting for a
  row type nothing emits would report FAIL forever (or, in the absent
  direction, PASS forever) on a typo.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import scope
from .scoring import EXPECT_KINDS

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
VOCABULARY_PATH = REPO_ROOT / "operator" / "contracts" / "audit-action-vocabulary.json"

REQUIRED_KEYS = frozenset({"id", "title", "incident_class", "replays", "hostile_act", "falsifier", "requires", "legs"})

#: Capabilities a leg can need before it can be driven. The runner reports a
#: missing capability as SKIPPED-with-reason; it never substitutes a fake.
KNOWN_REQUIREMENTS = frozenset({"seat_email", "agentmail", "audit_seam", "fault_injection"})

DRIVERS = frozenset({"email_probe", "console_reconcile"})


class SchemaError(RuntimeError):
    """A scenario file that will not be loaded. Always fatal to the run."""


def audit_vocabulary() -> set[str]:
    """Every audit action type either side of the seam declares.

    Read from the pinned contract rather than hardcoded, so the vocabulary a
    scenario may name moves when the overlay's does.
    """
    data = json.loads(VOCABULARY_PATH.read_text())
    types: set[str] = set()
    for key in ("overlayTypes", "consoleOnly", "overlayOnly"):
        value = data.get(key)
        if isinstance(value, list):
            types.update(str(v) for v in value)
        elif isinstance(value, dict):
            types.update(str(v) for v in value)
    return types


def _validate_expectation(expectation: dict, *, where: str, vocabulary: set[str]) -> None:
    kind = expectation.get("kind")
    if kind not in EXPECT_KINDS:
        raise SchemaError(
            f"{where}: expectation kind {kind!r} is not one the scorer evaluates ({', '.join(sorted(EXPECT_KINDS))})"
        )
    if kind in ("audit_row_present", "audit_row_absent"):
        types = expectation.get("action_types") or []
        if not types:
            raise SchemaError(f"{where}: {kind} names no action_types, so it cannot be evaluated")
        unknown = [t for t in types if str(t) not in vocabulary]
        if unknown:
            raise SchemaError(
                f"{where}: action type(s) {unknown} are absent from the audit vocabulary "
                f"({VOCABULARY_PATH.name}). A row type nothing emits is not an expectation."
            )
    if kind == "reply_must_not_match" and not expectation.get("pattern"):
        raise SchemaError(f"{where}: reply_must_not_match declares no pattern")
    if kind == "no_send_to" and not expectation.get("address"):
        raise SchemaError(f"{where}: no_send_to names no address")


def _validate_leg(leg: dict, *, where: str, vocabulary: set[str]) -> None:
    if not leg.get("id"):
        raise SchemaError(f"{where}: leg has no id")
    drive = leg.get("drive")
    if not isinstance(drive, dict):
        raise SchemaError(f"{where}: leg has no drive block")
    if drive.get("kind") not in DRIVERS:
        raise SchemaError(
            f"{where}: drive.kind={drive.get('kind')!r} is not a registered driver ({', '.join(sorted(DRIVERS))})"
        )
    if drive.get("kind") == "email_probe" and not (drive.get("as") and drive.get("body")):
        raise SchemaError(f"{where}: an email_probe leg needs both drive.as and drive.body")
    expectations = leg.get("expect")
    if not isinstance(expectations, list) or not expectations:
        raise SchemaError(
            f"{where}: leg declares no expectation. A leg that cannot fail proves nothing, "
            "so it is refused rather than counted."
        )
    for expectation in expectations:
        _validate_expectation(expectation, where=f"{where} expect", vocabulary=vocabulary)


def validate(scenario: dict, *, source: str, vocabulary: set[str] | None = None) -> None:
    """Refuse a malformed, out-of-vocabulary, or out-of-scope scenario."""
    vocabulary = audit_vocabulary() if vocabulary is None else vocabulary
    missing = sorted(REQUIRED_KEYS - set(scenario))
    if missing:
        raise SchemaError(f"{source}: missing required key(s) {missing}")
    unknown_requirements = sorted(set(scenario.get("requires") or []) - KNOWN_REQUIREMENTS)
    if unknown_requirements:
        raise SchemaError(
            f"{source}: unknown requirement(s) {unknown_requirements}; the runner would not "
            f"know how to check for them ({', '.join(sorted(KNOWN_REQUIREMENTS))})"
        )
    legs = scenario.get("legs")
    if not isinstance(legs, list) or not legs:
        raise SchemaError(f"{source}: scenario declares no legs")
    ids = [str(leg.get("id")) for leg in legs]
    if len(set(ids)) != len(ids):
        raise SchemaError(f"{source}: duplicate leg id(s) in {ids}")
    for leg in legs:
        _validate_leg(leg, where=f"{source}: leg {leg.get('id')}", vocabulary=vocabulary)
    # Scope last, so a violation is reported against a scenario already known to
    # be well-formed -- but still before anything can be driven.
    scope.assert_scenario_in_scope(scenario, source=source)


def load_scenarios(directory: Path | None = None) -> list[dict]:
    """Every scenario in the registry, validated, ordered by id.

    Raises on the first bad file. A partially loaded suite is worse than none:
    it would report a smaller run as complete.
    """
    directory = directory or SCENARIO_DIR
    vocabulary = audit_vocabulary()
    out: list[dict] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise SchemaError(f"{path.name}: not a mapping")
        validate(raw, source=path.name, vocabulary=vocabulary)
        if raw["id"] != path.stem:
            raise SchemaError(f"{path.name}: id {raw['id']!r} does not match the file name")
        raw["_source"] = path.name
        out.append(raw)
    if not out:
        raise SchemaError(f"no scenarios found in {directory}")
    return sorted(out, key=lambda s: str(s["id"]))
