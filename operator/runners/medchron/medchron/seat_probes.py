"""Read-only probes of the seat a runner lane checks before it spends: the
persisted sticky-stop level and which memory controller the guest offers.
Lifted out of ``daemon`` so the lanes share them without growing it."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")


def sticky_level(db_path: str) -> str | None:
    """The seat's worst persisted sticky-stop level, read-only; None when no
    state file exists yet (a fresh Machine); 'unknown' on a read error (never
    a fabricated OK)."""
    if not os.path.exists(db_path):
        return None
    order = ["OK", "WARN", "SOFT_STOP", "HARD_STOP"]
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            levels = [str(r[0]) for r in conn.execute("SELECT level FROM sticky_stop_state").fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a read-only observer fails toward unknown
        return "unknown"
    if not levels:
        return "OK"
    return max(levels, key=lambda lv: order.index(lv) if lv in order else 0)


def memory_cap_mode(cgroup_root: Path = CGROUP_ROOT) -> str:
    """Which memory controller this guest offers: ``cgroup2`` (unified root),
    ``cgroup1`` (the hybrid layout Fly Machines run: v2 mounted bare at
    /sys/fs/cgroup/unified with no controllers, memory on the v1 mount), or
    ``none``. Probed live on hermes-ashton-price 2026-08-31: no
    ``cgroup.controllers`` at the root, ``cgroup ... memory`` in /proc/mounts."""
    try:
        if "memory" in (cgroup_root / "cgroup.controllers").read_text().split():
            return "cgroup2"
    except OSError:
        pass
    if (cgroup_root / "memory" / "cgroup.procs").exists():
        return "cgroup1"
    return "none"
