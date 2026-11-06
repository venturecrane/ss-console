"""The one reader of an installed ``layout.json``: the letter and the lane ask here.

WHY (2026-11-05). The drafting lane checked the file against the digest in the
manifest and the install letter did not, so a torn store told the firm a rule
was "in effect" while the lane applied nothing. They now share this code. The
seat runs them in different venvs, so the file exists twice, byte for byte, at
``operator/hermes-smd-overlay/shared/layout_store.py`` and
``operator/runners/jobs/jobs/drafting/layout_store.py``, and
``tests/test_drafting_firm_rules.py`` fails if the two ever differ.

Three answers, because "nothing here" and "something here we cannot trust" need
different responses: ``NONE`` is an ordinary seat with no layout rules, ``OK`` is
a file its manifest vouches for, and ``UNUSABLE`` is a present-but-broken store,
which applies nothing, is recorded and pages SMD.

STDLIB ONLY: the lane's tests and both venvs load it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The schema of ``layout.json``. Bumped with the compiler; a file of another
#: schema is unusable here, and the applier recompiles on its next poll.
SCHEMA_VERSION = 2
LAYOUT_REL = "classes/work_product/layout.json"
MANIFEST = "manifest.json"

NONE = "none"
OK = "ok"
UNUSABLE = "unusable"


@dataclass(frozen=True)
class Store:
    status: str
    rules: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def _unusable(reason: str) -> Store:
    return Store(UNUSABLE, [], reason)


def _rule_problems(rule: Any, problems: Callable[[str, Any], list[str]] | None) -> list[str]:
    if not isinstance(rule, dict) or not isinstance(rule.get("rule_id"), str):
        return ["a rule has no id"]
    if not isinstance(rule.get("doc_classes"), list):
        return [f"rule {rule['rule_id']} has no document classes"]
    compiled, prose = rule.get("compiled"), rule.get("prose")
    if not isinstance(compiled, list) or not isinstance(prose, list):
        return [f"rule {rule['rule_id']} has no clause lists"]
    out: list[str] = []
    for clause in compiled:
        if not isinstance(clause, dict):
            return [f"rule {rule['rule_id']} has a malformed clause"]
        layout, relative = clause.get("layout"), clause.get("relative")
        if not isinstance(layout, dict) or not isinstance(relative, dict):
            return [f"rule {rule['rule_id']} has a malformed clause"]
        if problems is not None:
            out += [p for k, v in layout.items() for p in problems(k, v)]
            for k, step in relative.items():
                if not isinstance(step, int) or isinstance(step, bool):
                    out.append(f"rule {rule['rule_id']}: the step for {k} is not a whole number")
    return out


def read(spec_dir: str | Path, problems: Callable[[str, Any], list[str]] | None = None) -> Store:
    """What the spec store holds for the drafting lane. Never raises.

    ``problems(key, value)`` is the caller's own validator of a value, so a key
    its venv does not know reads as unusable rather than being applied.
    """
    root = Path(spec_dir)
    path = root / LAYOUT_REL
    manifest_path = root / MANIFEST
    try:
        manifest_raw = manifest_path.read_text("utf-8")
    except FileNotFoundError:
        if path.exists():
            return _unusable("layout.json has no manifest beside it")
        return Store(NONE)
    except (OSError, ValueError) as exc:
        return _unusable(f"the manifest cannot be read: {type(exc).__name__}")
    try:
        manifest = json.loads(manifest_raw)
    except ValueError:
        return _unusable("the manifest is not valid JSON")
    if not isinstance(manifest, dict):
        return _unusable("the manifest is not an object")
    layouts = manifest.get("layouts") or {}
    entry = layouts.get(LAYOUT_REL) if isinstance(layouts, dict) else None
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return _unusable("the manifest names layout.json and it is gone") if entry else Store(NONE)
    except OSError as exc:
        return _unusable(f"layout.json cannot be read: {type(exc).__name__}")
    if not isinstance(entry, dict):
        return _unusable("layout.json is present and the manifest does not name it")
    if entry.get("sha256") != hashlib.sha256(blob).hexdigest():
        return _unusable("layout.json does not match the digest in the manifest")
    try:
        doc = json.loads(blob.decode("utf-8"))
    except ValueError:
        return _unusable("layout.json is not valid JSON")
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        return _unusable("layout.json is not the schema this code reads")
    rules = doc.get("rules")
    if not isinstance(rules, list):
        return _unusable("layout.json has no rule list")
    found = [p for rule in rules for p in _rule_problems(rule, problems)]
    if found:
        return _unusable("; ".join(found)[:200])
    return Store(OK, rules)


__all__ = ["LAYOUT_REL", "MANIFEST", "NONE", "OK", "SCHEMA_VERSION", "UNUSABLE", "Store", "read"]
