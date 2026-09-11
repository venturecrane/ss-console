"""Regression: bootstrap.sh must PRODUCE the active-persona env the overlay reads.

The overlay's ADR 0056 trust gate (hermes-smd-trust/enforce.py), the audit
emitter, and peer-memory all resolve the active persona from the process env
``HERMES_ACTIVE_PROFILE`` (``SMD_ACTIVE_PERSONA`` is only a fallback), then look
up that persona's authored ``entitlements.exposure`` in customer.yaml. The
overlay's contracts/consumes.yaml declares ``HERMES_ACTIVE_PROFILE`` as
"(Hermes-native); first in the persona-resolution chain" — i.e. it ASSUMED
Hermes core sets it.

Core does NOT. ``hermes -p <slug>`` (hermes_cli/main.py:_apply_profile_override)
rewrites only ``HERMES_HOME``; it never exports ``HERMES_ACTIVE_PROFILE``. So
bootstrap.sh — the boundary that selects the active profile for the ``-p`` flag —
is the ONLY production code path that can publish it. Without the export the
plugins resolve the active persona to "" -> exposure {} -> every governed action
class fail-closes on every channel, leaving the agent unable to perform any
authored work. The overlay's unit tests masked this because they
``monkeypatch.setenv`` the var; only the live boot exposed it (first real
Smokeball matter.updated: every agent write refused "no authored exposure").

This test is the producer-side forcing function: if the export is ever removed
or decoupled from the profile that ``-p`` targets, CI goes red here instead of a
silently ungoverned-then-fail-closed gateway shipping to a customer Machine.

Run::

    cd operator && python3 -m pytest bin/tests/test_active_persona_env_export.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

_OP = Path(__file__).resolve().parents[2]
_BOOTSTRAP = _OP / "templates" / "bootstrap.sh"


def _text() -> str:
    return _BOOTSTRAP.read_text(encoding="utf-8")


def test_bootstrap_exports_active_persona_env() -> None:
    """bootstrap.sh exports HERMES_ACTIVE_PROFILE bound to the same shell var the
    gateway `-p` flag targets, so the overlay resolves the real persona slug."""
    text = _text()
    m = re.search(
        r'^\s*export\s+HERMES_ACTIVE_PROFILE="\$\{ACTIVE_PROFILE\}"\s*$',
        text,
        re.MULTILINE,
    )
    assert m, (
        'bootstrap.sh must `export HERMES_ACTIVE_PROFILE="${ACTIVE_PROFILE}"` so '
        "the overlay's ADR 0056 trust gate can resolve the active persona's "
        "exposure. Hermes core's `-p` flag does NOT set this env."
    )


def test_export_precedes_the_gateway_exec() -> None:
    """The export must come BEFORE the gateway `exec` so the gateway process and
    its in-process plugin hooks inherit it."""
    text = _text()
    export_at = text.find('export HERMES_ACTIVE_PROFILE="${ACTIVE_PROFILE}"')
    exec_at = text.find('exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run')
    assert export_at != -1, "active-persona export missing"
    assert exec_at != -1, "gateway exec line missing or changed shape"
    assert export_at < exec_at, (
        "HERMES_ACTIVE_PROFILE must be exported before the gateway exec so the gateway inherits it"
    )


def test_export_follows_active_profile_derivation() -> None:
    """The export must come AFTER ACTIVE_PROFILE is derived (it interpolates it),
    guarding against a reorder that would export an empty value."""
    text = _text()
    derive_at = text.find('ACTIVE_PROFILE="$(/opt/hermes/.venv/bin/python3')
    export_at = text.find('export HERMES_ACTIVE_PROFILE="${ACTIVE_PROFILE}"')
    assert derive_at != -1, "ACTIVE_PROFILE derivation block missing or changed shape"
    assert export_at != -1, "active-persona export missing"
    assert derive_at < export_at, "HERMES_ACTIVE_PROFILE must be exported AFTER ACTIVE_PROFILE is computed"
