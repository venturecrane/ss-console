"""Make the jobs runner importable from a laptop checkout.

The proof tools read the lane's own parsers (``howell.money``,
``howell.parse_date``, the page markers, ``evidence.lint``) so a score means
what the lane means. In the jobs venv the runner is installed; from a bare
checkout it is the tree's own ``operator/runners/jobs``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

RUNNER = Path(__file__).resolve().parents[3] / "runners" / "jobs"


def ensure() -> None:
    try:
        import jobs  # noqa: F401 -- probing that the runner is installed
    except ModuleNotFoundError:
        if str(RUNNER) not in sys.path:
            sys.path.insert(0, str(RUNNER))


def howell() -> ModuleType:
    ensure()
    from jobs.drafting import howell as m

    return m


def page_text() -> ModuleType:
    ensure()
    from jobs.medchron.audit import page_text as m

    return m


def lint() -> ModuleType | None:
    """``evidence.lint``, or None before it merged."""
    ensure()
    try:
        from jobs.drafting.evidence import lint as m
    except ImportError:
        return None
    return m


def render() -> ModuleType | None:
    ensure()
    try:
        from jobs.drafting.evidence import render as m
    except ImportError:
        return None
    return m


def shapes() -> ModuleType:
    ensure()
    from jobs.drafting.evidence import shapes as m

    return m
