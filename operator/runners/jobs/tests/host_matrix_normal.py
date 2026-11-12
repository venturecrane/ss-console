"""How the host matrix makes a step's record stable (``Normal``) and what the
run dir's own readers say of it (``readers``): the fleet busy probe judged by
``busy.decide``, and the overlay's heartbeat reader."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from host_matrix_kit import CLOCK, OPERATOR


# ---------------------------------------------------------------------------
# Normalising
# ---------------------------------------------------------------------------

_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_HEX24 = re.compile(r"\b[0-9a-f]{24,}\b")
#: A process id in a sentence ("child pid 4242 alive"), and seconds a lane measured.
_PID = re.compile(r"\b(pid) \d+\b")
_SECONDS = re.compile(r"(\b)\d+s after")
#: The current month (the broker's month window reads the wall clock); a full
#: date is left alone.
_MONTH = re.compile(r"\b20\d\d-[01]\d\b(?!-\d)")


class Normal:
    """Volatile values to stable tokens, ULIDs numbered by first appearance."""

    def __init__(self, roots: list[Path], secrets: frozenset[str]) -> None:
        self.roots = [str(r) for r in roots] + [os.path.realpath(r) for r in roots]
        self.ids: dict[str, str] = {}
        self.secrets = secrets

    def text(self, s: str) -> str:
        for r in sorted(set(self.roots), key=len, reverse=True):
            s = s.replace(r, "<TMP>")
        s = s.replace(sys.executable, "<PYTHON>")
        s = _ULID.sub(lambda m: self.ids.setdefault(m.group(0), f"<ULID{len(self.ids) + 1}>"), s)
        s = _ISO.sub("<TS>", s)
        s = _HEX24.sub("<HEX>", s)
        s = _PID.sub(r"\1 <PID>", s)
        s = _SECONDS.sub(r"\1<N>s after", s)
        return _MONTH.sub("<MONTH>", s)

    def number(self, key: str, v: float) -> Any:
        if key in ("pid", "started_at") or key.endswith("_pid"):
            return "<PID>" if key != "started_at" else "<EPOCH>"
        if abs(v - CLOCK) < 1e6:
            return f"<CLOCK{v - CLOCK:+.1f}>"
        if 1e9 < v < 4e9:
            return "<EPOCH>"
        return v

    def __call__(self, doc: Any, key: str = "") -> Any:
        if (key == "pid" or key.endswith("_pid")) and doc not in (None, ""):
            return "<PID>"
        if isinstance(doc, dict):
            return {self.text(str(k)): self(v, str(k)) for k, v in sorted(doc.items(), key=lambda kv: str(kv[0]))}
        if isinstance(doc, (list, tuple)):
            return [self(v, key) for v in doc]
        if isinstance(doc, bool) or doc is None:
            return doc
        if isinstance(doc, (int, float)):
            return self.number(key, float(doc)) if isinstance(doc, float) or doc > 10**8 else doc
        return self.text(str(doc))

    def env(self, env: dict[str, str]) -> dict[str, str]:
        """A child's env: a registry secret is named with its presence only."""
        return {k: ("<SECRET:present>" if k in self.secrets else self.text(v)) for k, v in sorted(env.items())}


# ---------------------------------------------------------------------------
# The readers of the run dir
# ---------------------------------------------------------------------------


def _probe_module(run_dir: Path) -> Any:
    from host_matrix_kit import _load_path

    mod = _load_path("host_matrix_busy_probe", OPERATOR / "bin" / "lib" / "fleet" / "remote_busy_probe.py")
    mod.RUN = str(run_dir)
    # The process start time comes from /proc (Linux only); a golden must not
    # differ by platform, so the matrix reads it as unknown everywhere.
    mod.started_at = lambda pid: None
    return mod


def readers(run_dir: Path) -> dict[str, Any]:
    from bin.lib.fleet import busy

    probe = _probe_module(run_dir)
    errors: list[str] = []
    facts = {
        "lanes": probe.lanes(CLOCK, errors),
        "children": probe.children(errors),
        "turns": probe.turns(CLOCK, errors),
        "errors": errors,
    }
    verdict = busy.decide(facts)
    overlay = str(OPERATOR / "hermes-smd-overlay")
    if overlay not in sys.path:
        sys.path.insert(0, overlay)
    from shared import fleet_facts

    counts = fleet_facts.read_lane_counts(run_dir, clock=lambda: CLOCK)
    return {
        "busy_probe": {"busy": verdict.busy, "reasons": verdict.reasons, "facts": facts},
        "fleet_facts_lane_counts": counts._asdict(),
    }


__all__ = ["Normal", "readers"]
