"""CLI surface for the audit-coverage gate (#2122).

The refusal in :mod:`adapter.evidence.packet` is only useful if the
operator-facing script exposes it honestly: the override has to reach
the builder, its default has to be "refuse", and the help text has to
tell an operator what the halt means and what their options are. These
tests pin that surface.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))  # operator/ on sys.path

from bin.lib import evidence  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


_BASE_ARGV = [
    "--customer",
    "acme",
    "--matter",
    "m-1",
    "--from",
    "2026-04-01T00:00:00Z",
    "--to",
    "2026-04-30T23:59:59Z",
    "--output",
    "/tmp/out.tar.gz",
    "--actor",
    "captain@example.com",
]


def _help_text() -> str:
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        evidence.parse_args(["--help"])
    return buf.getvalue()


def test_acknowledge_flag_defaults_to_refusing():
    """Absent the flag, a matter-scoped export that cannot answer its own
    question halts. The safe posture is the default, not the opt-in."""
    args = evidence.parse_args(_BASE_ARGV)
    assert args.acknowledge_unattributed_gap is False


def test_acknowledge_flag_parses_when_passed():
    args = evidence.parse_args([*_BASE_ARGV, "--acknowledge-unattributed-gap"])
    assert args.acknowledge_unattributed_gap is True


def test_help_explains_what_the_override_does_and_does_not_do():
    """An operator reaching for the override must be able to see from the
    help alone that it does not suppress the disclosure."""
    text = _help_text().lower()
    assert "--acknowledge-unattributed-gap" in text
    assert "states the gap" in text
    assert "audit row" in text
    # The help must not imply the flag makes the packet clean.
    assert "suppress" not in text


def test_help_names_the_halt_so_exit_3_is_diagnosable():
    text = _help_text().lower()
    assert "halts" in text or "halt" in text
