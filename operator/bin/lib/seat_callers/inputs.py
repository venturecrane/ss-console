"""The fixed inputs every site is run over.

customer.yaml: a synthetic, client-free "good" seat document, then the failure
shapes (missing, empty, not a mapping, YAML syntax error, unreadable), then the
three block mutations built from the keys the site was OBSERVED to read
(yamltrack.py), then every real operator/customers/*/customer.yaml, named by a
hash of its folder so no client name enters the table.

Broker socket: a fixed payload set (one ASCII, one non-ASCII) and the server
behaviours a client must survive.

CUSTOMER_SLUG: the five environment states, plus a padded value.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml

GOOD_SLUG = "alpha-seat"
ALT_SLUG = "beta-seat"
SLUG_ALTERNATES = ("SMD_CUSTOMER_SLUG", "SMD_SLUG")

# The shape of operator/templates/customer-no-pm-system.yaml, with synthetic
# values, held here so a template edit does not move the table.
GOOD: dict[str, Any] = {
    "schema_version": 1,
    "customer_id": GOOD_SLUG,
    "customer_name": "Alpha Seat (synthetic)",
    "vertical": "law-firm",
    "practice_areas": ["personal-injury-plaintiff"],
    "fly_region": "iad",
    "model": "synthetic-model",
    "machine": {"size": "shared-cpu-2x", "memory_mb": 2048},
    "seat": {"kind": "client", "product": "operator", "timezone": "America/Phoenix"},
    "users": [{"email": "principal@example.com", "role": "principal", "full_name": "Pat Example"}],
    "personas": [
        {
            "slug": "operator",
            "status": "active",
            "name": "Operator",
            "title": "AI Associate",
            "entitlements": {"exposure": {"internal_write": "draft_for_review", "external_send": "draft_for_review"}},
            "skills": [
                {
                    "name": "inbox-triage",
                    "version": "pending",
                    "initiation": {"manual": True, "scheduled": False, "webhook": False},
                    "enabled": True,
                }
            ],
        }
    ],
    "connectors": {
        "Email": {"adapter": "agentmail", "backend": "mcp:agentmail", "enabled": True, "inbox": "seat@example.com"},
        "PracticeManagement": {"adapter": "no_pm", "backend": "synthetic:no_pm", "enabled": True},
    },
    "scope": {
        "email_folders_visible": ["Inbox", "Sent"],
        "email_folders_blind": [],
        "email_keyword_blocks": [],
        "domain_blocks": [],
    },
    "escalation": {"red_flag_recipients": ["principal@example.com"], "failure_recipients": ["principal@example.com"]},
    "memory": {
        "d1_namespace": GOOD_SLUG,
        "r2_vault_path": f"vaults/{GOOD_SLUG}/",
        "vectorize_index": f"hermes-{GOOD_SLUG}-vault",
    },
    "send_policy": {"reply": {"internal_exempt": True, "per_sender_max": 5}},
    "webhook_triggers": [],
}

# (id, kind): kind "file" writes text, "missing" leaves no file, "dir" puts a
# directory where the file should be.
FAILURE_INPUTS: list[tuple[str, str, str]] = [
    ("missing", "missing", ""),
    ("empty", "file", ""),
    ("not-mapping", "file", "- one\n- two\n"),
    ("yaml-syntax-error", "file", "customer_id: [unclosed\n  - : :\n"),
    ("unreadable", "dir", ""),
]
BLOCK_INPUTS = ("block-missing", "block-malformed", "block-malformed-deep")
BAD_INPUTS = ("empty", "not-mapping", "yaml-syntax-error", "unreadable", "block-malformed", "block-malformed-deep")

PAYLOADS: list[tuple[str, dict[str, Any]]] = [
    ("ascii", {"verb": "ping", "n": 1}),
    ("non-ascii", {"verb": "note", "text": "café naïve 日本 ±"}),
]
# What the recording server does after it has read the request.
SOCKET_SCENARIOS = ("ok", "not-ok", "empty-reply", "non-json", "fault-after-send", "no-socket", "refused")

SLUG_STATES: list[tuple[str, dict[str, str]]] = [
    ("unset", {}),
    ("empty", {"CUSTOMER_SLUG": ""}),
    ("valid", {"CUSTOMER_SLUG": GOOD_SLUG}),
    ("whitespace", {"CUSTOMER_SLUG": "   "}),
    ("padded", {"CUSTOMER_SLUG": f" {GOOD_SLUG}\n"}),
    ("both", {"CUSTOMER_SLUG": GOOD_SLUG, **dict.fromkeys(SLUG_ALTERNATES, ALT_SLUG)}),
]


def dump(doc: Any) -> str:
    return yaml.safe_dump(doc, sort_keys=True, allow_unicode=True)


def good_text() -> str:
    return dump(GOOD)


def real_inputs(repo: Path) -> list[tuple[str, Path]]:
    """Every real customer.yaml, id'd by a hash of its folder name (no client names)."""
    out = []
    for path in sorted((repo / "operator" / "customers").glob("*/customer.yaml")):
        tag = hashlib.sha256(path.parent.name.encode()).hexdigest()[:10]
        out.append((f"real-{tag}", path))
    return out


def _wrong(type_name: str) -> Any:
    if type_name == "dict":
        return ["malformed"]
    if type_name in ("list", "tuple"):
        return "malformed"
    if type_name == "str":
        return ["malformed"]
    if type_name in ("int", "float", "bool"):
        return "malformed"
    return ["malformed"]


def _set_leaf(node: Any, path: tuple, value: Any) -> None:
    head, rest = path[0], path[1:]
    if head == "[]":
        if isinstance(node, list):
            for item in node:
                if rest:
                    _set_leaf(item, rest, value)
        return
    if head == "*" or not isinstance(node, dict):
        return
    if not rest:
        node[head] = value
        return
    if head not in node or not isinstance(node[head], dict | list):
        if rest[0] == "[]":
            return
        node[head] = {}
    _set_leaf(node[head], rest, value)


def _deepest(paths: dict[tuple, str]) -> list[tuple]:
    usable = [p for p in paths if p and p[-1] not in ("*", "[]")]
    return sorted(p for p in usable if not any(len(q) > len(p) and q[: len(p)] == p for q in usable))


def block_inputs(accessed: dict[tuple, str]) -> dict[str, str | None]:
    """The three block mutations of GOOD, or None where nothing was observed read."""
    tops = {p[0]: accessed.get((p[0],), "NoneType") for p in accessed if p and p[0] not in ("*", "[]")}
    if not tops:
        return dict.fromkeys(BLOCK_INPUTS)
    missing = copy.deepcopy(GOOD)
    malformed = copy.deepcopy(GOOD)
    for key, type_name in sorted(tops.items()):
        missing.pop(key, None)
        malformed[key] = _wrong(type(GOOD[key]).__name__ if key in GOOD else type_name)
    deep = copy.deepcopy(GOOD)
    for path in _deepest(accessed):
        _set_leaf(deep, path, _wrong(accessed[path]))
    return {"block-missing": dump(missing), "block-malformed": dump(malformed), "block-malformed-deep": dump(deep)}
