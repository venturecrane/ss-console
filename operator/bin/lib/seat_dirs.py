"""Which directories under ``operator/customers/`` are seats. The one rule.

A directory is a SEAT when all of these hold:

* its name does not start with ``_`` (``_template``, ``_hosted-template``,
  ``_couples-template`` are provisioning scaffolds, not seats);
* its name does not start with ``.``;
* its name does not contain ``.decommissioned.`` (a retired seat is kept as a
  ``<slug>.decommissioned.<date>`` tombstone by ``bin/lib/decommission.py``
  step 08; it is a historical record, never a seat);
* it holds a ``customer.yaml``;
* its name is a valid slug (:data:`SLUG_RE`).

This module is the Python home of that rule. The TypeScript home is
``scripts/lib/seat-inventory.ts`` and the bash home is
``scripts/lib/seat-dir.sh``; ``tests/seat-inventory.test.ts`` runs all three
against one fixture tree (scaffolds, a tombstone, a dir with no customer.yaml)
and fails if they disagree. Every Python enumeration of seats goes through
:func:`seat_slugs` rather than re-deriving a filter, because the hand-written
filters drifted: one excluded only ``_template`` and counted the other two
scaffolds as seats, several counted tombstones.

Stdlib only, no package-relative imports, so every script under ``operator/bin``
can use it whatever its import style.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union

# Canonical seat slug shape (#2285). bin/lib/fleet/ids.py re-exports this.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")

TOMBSTONE_MARKER = ".decommissioned."


def skip_reason(name: str) -> Optional[str]:
    """Why a directory NAME is not a seat, or None when the name could be one.

    ``scaffold`` / ``tombstone`` / ``hidden``. A name that passes still needs a
    customer.yaml and a valid slug to be a seat (:func:`is_seat_dir`).
    """
    if name.startswith("_"):
        return "scaffold"
    if name.startswith("."):
        return "hidden"
    if TOMBSTONE_MARKER in name:
        return "tombstone"
    return None


def is_seat_dir(path: Union[str, Path]) -> bool:
    p = Path(path)
    return (
        p.is_dir()
        and skip_reason(p.name) is None
        and (p / "customer.yaml").is_file()
        and SLUG_RE.match(p.name) is not None
    )


def seat_slugs(customers_dir: Union[str, Path]) -> list[str]:
    """Every seat slug under ``customers_dir``, sorted. Empty when it is absent."""
    root = Path(customers_dir)
    if not root.is_dir():
        return []
    return sorted(name for name in os.listdir(root) if is_seat_dir(root / name))


# ``fly_app:`` with a YAML null value on its own line. seat.yaml is a flat file;
# scripts/lib/seat-inventory.ts is the strict parser, this is read as text so the
# stdlib-only callers need no PyYAML.
_NULL_APP_RE = re.compile(r"^fly_app:[ \t]*(?:null|Null|NULL|~)?[ \t]*(?:#.*)?$", re.MULTILINE)


def unprovisioned_slugs(customers_dir: Union[str, Path]) -> set[str]:
    """Seats whose ``seat.yaml`` authors ``fly_app: null``: authored, no app yet.

    Such a seat has no Machine, no mailbox credential and no release to take, so
    tooling that ACTS on a seat (``fleet release``'s rings, the daily send
    reconciler's mailbox reads) leaves it out rather than holding or failing on
    it every run. A seat.yaml that cannot be read counts as PROVISIONED: the
    seat stays in, and whatever acts on it fails loudly, never skips silently.
    """
    root = Path(customers_dir)
    out: set[str] = set()
    for slug in seat_slugs(root):
        try:
            text = (root / slug / "seat.yaml").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _NULL_APP_RE.search(text):
            out.add(slug)
    return out


if __name__ == "__main__":  # parity probe for tests/seat-inventory.test.ts
    import json
    import sys

    print(json.dumps(seat_slugs(sys.argv[1])))
