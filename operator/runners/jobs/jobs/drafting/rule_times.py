"""When each installed layout rule was said: the one input the order of
precedence needs that ``layout.json`` does not carry (design 2026-11-06, piece 5).

The spec applier renders each confirmed rule into the installed spec file as
one bullet, ``- [rule <id>] (instructed by ..., <at>): <text>``
(``spec_applier/applier.render_spec``), under the same root-computed manifest
that vouches for ``layout.json``. This reads the id and the ``at`` of each
bullet and nothing else: no name, no word of the rule. The file is read only
when the manifest's digest for it matches its bytes; anything else answers
``None`` (cannot say when), which the precedence treats as a rule it cannot
place, never as a rule that is older.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

#: The installed spec the layout rules are compiled from (``layout_rules.LAYOUT_PROPERTY``).
SPEC_REL = "classes/work_product/format.md"
_BULLET = re.compile(r"^- \[rule ([^\]\s]+)\] \((?:.*?), ([^,()]+)\): ", re.M)


def when(at: str) -> datetime | None:
    """``at`` as an aware UTC time, or None when it is not ISO 8601."""
    text = str(at or "").strip()
    if not text:
        return None
    try:
        got = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=timezone.utc)


def read(spec_dir: str | Path) -> dict[str, str] | None:
    """``{rule_id: at}`` for every rule bullet in the installed spec, or None
    when the file cannot be trusted (absent from the manifest, torn, unreadable)."""
    root = Path(spec_dir)
    try:
        manifest = json.loads((root / "manifest.json").read_text("utf-8"))
        blob = (root / SPEC_REL).read_bytes()
    except (OSError, ValueError):
        return None
    entry = ((manifest.get("specs") or {}) if isinstance(manifest, dict) else {}).get(SPEC_REL)
    if not isinstance(entry, dict) or entry.get("sha256") != hashlib.sha256(blob).hexdigest():
        return None
    return {m.group(1): m.group(2).strip() for m in _BULLET.finditer(blob.decode("utf-8", "replace"))}


__all__ = ["SPEC_REL", "read", "when"]
