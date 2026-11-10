"""What each seat is for (ADR 0107), for the Python tooling.

Every seat's ``operator/customers/<slug>/seat.yaml`` authors ``purpose``
(``client | release-twin | demo | internal``) and, for a release twin,
``twin_of`` (the client seat it is an exact copy of). ``npm run
gen:seat-registries`` renders them into ``operator/fleet/seats.yaml``; this
module reads that file, so no Python tool keeps a slug of its own. Renaming a
seat or adding one is an edit to its seat.yaml, never to this code.

Run as a script for shell callers (reprovision-staging.sh)::

    python3 operator/bin/lib/seat_purpose.py purpose <slug>    # prints the purpose
    python3 operator/bin/lib/seat_purpose.py twin-of <slug>    # prints the twin's client
    python3 operator/bin/lib/seat_purpose.py release-twins     # one slug per line

Stdlib plus PyYAML, no package-relative imports, so every script under
``operator/bin`` can use it whatever its import style.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

PURPOSES = ("client", "release-twin", "demo", "internal")
SEATS_FILE = Path(__file__).resolve().parents[2] / "fleet" / "seats.yaml"


class SeatPurposeError(ValueError):
    """The generated seats file is missing, malformed, or does not name the seat."""


def load(path: Path = SEATS_FILE) -> dict[str, dict[str, Any]]:
    """Seat slug -> ``{"purpose", "twin_of", "provisioned"}``. Raises on a malformed file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise SeatPurposeError(f"{path}: cannot read ({exc}); run `npm run gen:seat-registries`") from exc
    seats = raw.get("seats") if isinstance(raw, dict) else None
    if not isinstance(seats, dict) or not seats:
        raise SeatPurposeError(f"{path}: no seats mapping")
    out: dict[str, dict[str, Any]] = {}
    for slug, entry in seats.items():
        purpose = (entry or {}).get("purpose")
        if purpose not in PURPOSES:
            raise SeatPurposeError(f"{path}: {slug} has purpose {purpose!r}, not one of {PURPOSES}")
        out[str(slug)] = {
            "purpose": purpose,
            "twin_of": (entry or {}).get("twin_of"),
            "provisioned": bool((entry or {}).get("provisioned")),
        }
    return out


def purpose_of(slug: str, seats: dict[str, dict[str, Any]] | None = None) -> str:
    table = load() if seats is None else seats
    if slug not in table:
        raise SeatPurposeError(f"{slug} is not a seat in {SEATS_FILE}")
    return str(table[slug]["purpose"])


def with_purpose(purpose: str, seats: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Every seat of ``purpose``, sorted."""
    table = load() if seats is None else seats
    return sorted(s for s, e in table.items() if e["purpose"] == purpose)


def release_twins(seats: dict[str, dict[str, Any]] | None = None) -> list[str]:
    return with_purpose("release-twin", seats)


def twin_of(slug: str, seats: dict[str, dict[str, Any]] | None = None) -> str:
    """The client seat a release twin rehearses. Raises for any other purpose."""
    table = load() if seats is None else seats
    if purpose_of(slug, table) != "release-twin" or not table[slug].get("twin_of"):
        raise SeatPurposeError(f"{slug} is not a release twin (seat.yaml purpose: release-twin, twin_of)")
    return str(table[slug]["twin_of"])


def _main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[1] == "purpose":
            print(purpose_of(argv[2]))
            return 0
        if len(argv) == 3 and argv[1] == "twin-of":
            print(twin_of(argv[2]))
            return 0
        if len(argv) == 2 and argv[1] == "release-twins":
            for slug in release_twins():
                print(slug)
            return 0
    except SeatPurposeError as exc:
        print(f"seat_purpose: {exc}", file=sys.stderr)
        return 1
    print("usage: seat_purpose.py {purpose <slug>|twin-of <slug>|release-twins}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
