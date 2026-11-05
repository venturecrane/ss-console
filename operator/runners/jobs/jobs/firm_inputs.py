"""A lane's firm inputs, pinned to one version for the life of a job.

The seat's live inputs path (``/var/lib/smd-config/<lane>``, and the
chronology's ``medchron-firm.yaml``) is a SYMLINK to a versioned copy that the
root firm-inputs applier swaps live when the firm's configuration is
republished (overlay firm_inputs_applier). Demand and drafting read their
pinned files lazily, stage by stage, so a job that kept the symlink path could
read its voice from one version and its skeleton from the next. Every reader
therefore resolves the path ONCE, here, and keeps the real directory: each
lane's ``resolve_dir`` calls :func:`pin`, so every ``firm.load`` (the lane gate
and each child's constructor) holds the version that was live when it loaded,
and each lane hands its child the resolved path rather than the symlink.
"""

from __future__ import annotations

import os
from pathlib import Path


def pin(path: str | Path) -> Path:
    """The real path behind ``path`` right now (a non-link path is returned unchanged)."""
    return Path(os.path.realpath(path))


def pin_env(path: str | Path | None) -> str | None:
    """``pin`` for a value headed into a child's env; None and "" pass through."""
    return str(pin(path)) if path else None


__all__ = ["pin", "pin_env"]
