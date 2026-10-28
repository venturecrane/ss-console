"""Guard the authored ``platform_toolsets.webhook`` in a persona profile's config.yaml.

WHY (Hermes v0.22.5 promotion, ss-console#3564). translate writes an exact
webhook tool surface into the persona profile's config.yaml: Hermes'
webhook-safe toolsets plus the overlay's one-tool read-only toolset, and
nothing that writes (``shared/webhook_read_surface.py``, ss-console#2145).
Since v0.22, ``hermes plugins enable`` APPENDS the enabled plugin's toolset to
every saved ``platform_toolsets`` list (``hermes_cli/plugins_cmd.py``
``_toggle_plugin_toolset``). The first promotion ran ``enable`` on every boot
and so widened that list.

bootstrap.sh no longer runs ``enable``: the overlay is selected by writing
``plugins.enabled`` (``bootstrap/plugin_selection.py``). On a normal boot this
module therefore finds the list already equal to the authored one and writes
nothing. It stays as a defensive guard, run right before the gateway exec,
whatever else touched the file. It puts the authored list back and re-reads
the file. The list on disk must EQUAL the authored one or the boot fails
(exit 1). A wider webhook surface is the failure the safe default exists to
prevent, and a narrower one breaks the email channel silently. The authored
list is recomputed from customer.yaml with translate's own functions, so the
two cannot disagree. A seat whose translate writes no webhook list must still
have none.

Usage (bootstrap.sh)::

    python3 -m bootstrap.webhook_toolsets <customer.yaml> <profile config.yaml>
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from bootstrap.translate import _materialize_webhook_platform, _sealed_reader_authored, _yaml_bytes
from shared.webhook_read_surface import webhook_platform_toolsets


def authored_webhook_toolsets(customer: dict[str, Any]) -> list[str] | None:
    """The list translate writes for this customer, or None when it writes none."""
    if not _materialize_webhook_platform(customer):
        return None
    return webhook_platform_toolsets(
        sealed_reader=_sealed_reader_authored(customer.get("connectors") or {})
    )


def _current(config: dict[str, Any]) -> Any:
    toolsets = config.get("platform_toolsets")
    return toolsets.get("webhook") if isinstance(toolsets, dict) else None


def reassert(customer_yaml: Path, profile_config: Path) -> list[str]:
    """Rewrite the profile's webhook list to the authored one; return the problems (empty is pass)."""
    customer = yaml.safe_load(customer_yaml.read_text(encoding="utf-8")) or {}
    authored = authored_webhook_toolsets(customer)
    config = yaml.safe_load(profile_config.read_text(encoding="utf-8")) or {}
    current = _current(config)
    if authored is None:
        if current is not None:
            return [
                f"translate authors no webhook toolsets, but {profile_config} carries {current!r}"
            ]
        return []
    if current != authored:
        extra = sorted(set(current or []) - set(authored)) if isinstance(current, list) else current
        print(
            f"webhook toolsets: restoring the authored {authored!r} (on disk: {current!r}; extra: {extra!r})"
        )
        toolsets = config.get("platform_toolsets")
        if not isinstance(toolsets, dict):
            toolsets = config["platform_toolsets"] = {}
        toolsets["webhook"] = list(authored)
        profile_config.write_bytes(_yaml_bytes(config))
    reread = _current(yaml.safe_load(profile_config.read_text(encoding="utf-8")) or {})
    if reread != authored:
        return [
            f"{profile_config}: platform_toolsets.webhook is {reread!r} after the rewrite, authored {authored!r}"
        ]
    return []


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    problems = reassert(Path(argv[1]), Path(argv[2]))
    for problem in problems:
        print(f"webhook toolsets: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
