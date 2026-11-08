"""The seat volume's layout epoch, recorded per image release (migration 0150).

``operator/fleet/volume-epoch`` holds one integer: the layout of /opt/data the
image's boot expects. A boot on epoch N moves an epoch N-1 volume forward; an
image of a LOWER epoch does not know the newer layout and boots on it as if it
were empty (ADR 0103: an epoch-1 image on an epoch-2 volume makes a fresh
/opt/data/medchron and runs every lane on it). So a seat must never be deployed,
forward or as a rollback, onto an image whose epoch is lower than the one it
runs.

``fleet build`` records the epoch of the tree it built in
``fleet_releases.volume_epoch``. A row without it (built before 0150), a missing
file, and an image no release row names all count as epoch 1, the layout before
the first epoch file existed. Counting the unknown as the LOWEST epoch is what
makes the rule fail closed: an unknown rollback target of an epoch-2 release is
refused, never trusted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .runner import ToolingError

EPOCH_FILE = "operator/fleet/volume-epoch"
BASE_EPOCH = 1


def read(repo: Path) -> int:
    """The epoch of the tree at ``repo``; no file is epoch 1. A file that is not
    one positive integer refuses the build: guessing a layout is the defect."""
    path = repo / EPOCH_FILE
    if not path.is_file():
        return BASE_EPOCH
    text = path.read_text(encoding="utf-8").strip()
    if not text.isdigit() or int(text) < BASE_EPOCH:
        raise ToolingError(f"{EPOCH_FILE} must hold one positive integer, found {text[:40]!r}")
    return int(text)


def of(release: dict[str, Any] | None) -> int:
    """A release row's epoch; no row, or a row without one, is epoch 1."""
    raw = (release or {}).get("volume_epoch")
    try:
        value = int(raw) if raw is not None else BASE_EPOCH
    except (TypeError, ValueError):
        return BASE_EPOCH
    return max(value, BASE_EPOCH)
