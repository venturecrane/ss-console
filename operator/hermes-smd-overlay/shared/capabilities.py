"""What a seat is for: the capabilities a seat authors, and the one predicate that says so.

WHY THIS EXISTS (ADR 0100, 2026-10-31). Every seat ran the same image and the image
started everything in it: the job runner, every lane's boot fetches and checks, every
lane's gateway tools, the Smokeball webhook routes. On the 1 GB seats that left 15-60
MB free, and captain-mark stopped heartbeating for 24 minutes. Nothing asked what the
seat was for, because nothing could: there was no single answer to ask.

THE MODEL. There is one Operator. A seat is the core every Operator has plus the
capabilities its engagement authored. Authored is DERIVED from config the seat
already carries, never a separate flag that can disagree with it:

* a job lane is authored when a persona enables the lane's skill;
* a connector capability is authored when the seat binds that connector backend.

Nothing here reads ``vertical``, ``seat.product`` or a customer slug. A seat that
needs a lane says so by enabling its skill, whatever business it is in.

ONE SOURCE. Boot (``entrypoint.sh``), the gateway plugins, translate, the self-test
and the release controller all ask this module, so what runs, what is offered to
the model and what is checked cannot drift apart. Shell callers use the CLI::

    python -I capabilities.py lanes <customer.yaml>        # authored lanes, one per line
    python -I capabilities.py connector <name> <customer.yaml>  # exit 0 iff authored

The module imports only the standard library plus PyYAML, so it runs from the
image's Hermes venv before the overlay is on any path.

READ FAILURES. Boot adopts only a customer.yaml the on-box validator accepted
(entrypoint.sh, before any lane work), so an unreadable file here means something
is badly wrong. The readers return "nothing authored" and say why on stderr; callers
that must not lose a capability on a transient read (the skills prune) check
:func:`read` themselves and keep what they have.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: Job lane -> the skill whose being enabled authors it. The runner's own constants
#: (medchron/*_lane.py ``SKILL``) must agree; tests/test_capabilities.py pins them.
JOB_LANES: dict[str, str] = {
    "medchron": "medical-chronology-maintainer",
    "demand": "demand-letter-drafter",
    "drafting": "document-drafter",
    "litigation": "litigation-status",
    "casefile": "case-file-manager",
    "negotiation": "negotiation-watch",
    "note": "smd-note",
}

#: Connector capability -> the ``connectors.<name>.backend`` values that author it.
CONNECTOR_BACKENDS: dict[str, frozenset[str]] = {
    "smokeball": frozenset({"mcp:smokeball"}),
}


def read(path: str | Path) -> dict[str, Any] | None:
    """The parsed customer.yaml, or None when it cannot be read as a mapping."""
    try:
        import yaml

        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - every failure means the same thing here
        print(f"capabilities: cannot read {path}: {exc}", file=sys.stderr)
        return None
    return doc if isinstance(doc, dict) else None


def enabled_skills(doc: dict[str, Any] | None) -> set[str]:
    """Skill names any persona enables (``enabled`` absent counts as enabled)."""
    names: set[str] = set()
    for persona in (doc or {}).get("personas") or []:
        if not isinstance(persona, dict):
            continue
        for skill in persona.get("skills") or []:
            if (
                isinstance(skill, dict)
                and skill.get("enabled") is not False
                and isinstance(skill.get("name"), str)
            ):
                names.add(skill["name"].strip())
    return names


def authored_lanes(doc: dict[str, Any] | None) -> list[str]:
    """The job lanes this seat authors, in :data:`JOB_LANES` order."""
    skills = enabled_skills(doc)
    return [lane for lane, skill in JOB_LANES.items() if skill in skills]


def authors_lane(doc: dict[str, Any] | None, lane: str) -> bool:
    return lane in authored_lanes(doc)


def authors_connector(doc: dict[str, Any] | None, name: str) -> bool:
    """True iff an enabled connector binds one of ``name``'s backends."""
    backends = CONNECTOR_BACKENDS.get(name)
    if not backends:
        raise KeyError(f"unknown connector capability {name!r}")
    connectors = (doc or {}).get("connectors") or {}
    if not isinstance(connectors, dict):
        return False
    return any(
        isinstance(c, dict) and c.get("enabled") is not False and c.get("backend") in backends
        for c in connectors.values()
    )


def _main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "lanes":
        doc = read(argv[1])
        if doc is None:
            # Cannot evaluate is not "no lanes": boot then starts every lane and the
            # self-test runs every lane check (a client must not lose one to a bad read).
            return 3
        for lane in authored_lanes(doc):
            print(lane)
        return 0
    if len(argv) == 3 and argv[0] == "connector":
        return 0 if authors_connector(read(argv[2]), argv[1]) else 1
    print(
        "usage: capabilities.py lanes <customer.yaml> | connector <name> <customer.yaml>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
