"""Role-snapshot projection + hash for the signer determination (ss #2402 Part 3).

A hold released with a signer determination records WHY it was released
(``determination`` on the hold sentinel's ``resolved`` event), and that
determination is trusted only while the facts it was derived from are
unchanged. The facts live in the matter's roles/layout (the F13 conflict is
the matter-type layout carrying empty structural ``Minor``/``Deceased`` role
slots read as live flags), so the snapshot is a code-computed projection over
the reads that feed signer resolution. CODE computes the hash — the model
never hashes and never retypes payloads; the resolving turn only COPIES the
``current_role_snapshot_sha256`` value off its wake-line plan, so a mis-copy
fails safe in both directions (mismatch -> escalate -> re-derive; it can never
silently validate a wrong determination).

The field projection is PINNED BY A LIVE PROBE, not assumed (pilot-smokeball
staging, 2026-08-31, vfy_01M1CB0NTKCV3ACRY0P6QD6JX7; fixture:
``tests/role_snapshot_probe.json``). What the probe showed, and this code
relies on: ``/matters/{id}/roles`` returns ``{matterId, roles: [...]}`` with
each role EMBEDDING its relationships (id, name, contactId), so no separate
relationships pull is needed (the flat ``/matters/{id}/relationships``
endpoint 403s on this tenant); and the structural F13 slots live under
``matter.items`` as ``type: "role"`` entries that carry NO ``id``.

Volatile fields (href, rel, etag, versionId, title, description, status,
openedDate) are EXCLUDED; lists are sorted by stable id so payload ordering
cannot move the hash. Two probe pulls minutes apart must hash identically —
tested against the committed fixture.

STAGING. This module lives beside ``pre_run.py`` in the skill dir and is
loaded by path exactly like the vendored ``escalation_ledger.py`` — the
scheduler stages ``pre_run.py`` alone, so ``pre_run`` walks the same candidate
dirs (its own dir, then the staged skills roots). A load failure degrades
every hash to None (unknown): the consult treats unknown as no determination
and the turn falls back to fresh derivation with ambiguity-holds, so the
failure direction is toward holding, never toward trusting.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"
_SNAPSHOT_PULL_TIMEOUT_SECONDS = 60

# Runs inside the connector venv (the same seam as pre_run's task pull). The
# matter id arrives as argv[1]; any failure is REPORTED so the caller degrades
# the hash to None (unknown) — a partial projection must never hash, because a
# partial hash reads as a fact change (false stale) rather than a failed read.
_SNAPSHOT_PULL_SNIPPET = """\
import json
import sys

from smokeball_connector.client import build_client_from_env

matter_id = sys.argv[1]
client = build_client_from_env()
out = {}
try:
    out["matter"] = client.get(f"/matters/{matter_id}")
    out["roles"] = client.get(f"/matters/{matter_id}/roles")
except Exception as exc:
    out["error"] = str(exc)[:300]
print(json.dumps(out, default=str))
"""

# Only a value shaped like a connector id may reach the subprocess argv: the
# matter ids here are read off Smokeball payloads / ledger rows, and this seam
# must not forward anything else.
_SAFE_MATTER_ID_CHARS = frozenset("0123456789abcdefABCDEF-_")


def _snapshot_role_node(node) -> dict | None:
    """One role entry from the ``/roles`` payload, stable fields only."""
    if not isinstance(node, dict):
        return None
    contact = node.get("contact")
    contact_id = node.get("contactId") or (
        contact.get("id") if isinstance(contact, dict) else None
    )
    relationships = []
    rels = node.get("relationships")
    if isinstance(rels, list):
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            relationships.append(
                {
                    "id": rel.get("id"),
                    "name": rel.get("name"),
                    "contactId": rel.get("contactId"),
                }
            )
    relationships.sort(key=lambda r: (str(r.get("id") or ""), str(r.get("name") or "")))
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "contactId": contact_id,
        "isClient": node.get("isClient"),
        "isOtherSide": node.get("isOtherSide"),
        "relationships": relationships,
    }


def _snapshot_items_tree(items) -> list:
    """The matter's role/layout item tree, ``type: "role"`` entries only.

    This is where the F13 structural slots live: an empty ``Minor`` or
    ``Deceased`` sub-slot is a ``role`` entry with no ``id``, and its presence
    or absence is exactly the class of fact a signer determination was derived
    from. Layout entries (``type: "layout"``) are form definitions, not party
    facts, and are excluded.
    """
    out: list = []
    if not isinstance(items, dict):
        return out
    for group_name in sorted(items, key=str):
        entries = items.get(group_name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "role":
                continue
            contact = entry.get("contact")
            node = {
                "group": str(group_name),
                "id": entry.get("id"),  # None for a structural (F13) slot
                "name": entry.get("name"),
                "role": entry.get("role"),
                "isClient": entry.get("isClient"),
                "isOtherSide": entry.get("isOtherSide"),
                "contactId": contact.get("id") if isinstance(contact, dict) else None,
                "subRoles": _snapshot_items_tree(entry.get("subItems")),
            }
            out.append(node)
    out.sort(key=lambda n: (n["group"], str(n.get("id") or ""), str(n.get("name") or "")))
    return out


def role_snapshot_projection(matter, roles) -> dict:
    """The stable projection the determination hash is computed over. Pure."""
    matter = matter if isinstance(matter, dict) else {}
    matter_type = matter.get("matterType")
    client_ids = matter.get("clientIds")
    other_side_ids = matter.get("otherSideIds")
    role_nodes = []
    roles_list = (roles or {}).get("roles") if isinstance(roles, dict) else roles
    if isinstance(roles_list, list):
        for node in roles_list:
            projected = _snapshot_role_node(node)
            if projected is not None:
                role_nodes.append(projected)
    role_nodes.sort(key=lambda n: (str(n.get("id") or ""), str(n.get("name") or "")))
    return {
        "matter": {
            "id": matter.get("id"),
            "number": matter.get("number"),
            "matterTypeId": matter.get("matterTypeId")
            or (matter_type.get("id") if isinstance(matter_type, dict) else None),
            "clientIds": sorted(str(v) for v in client_ids or [] if v),
            "otherSideIds": sorted(str(v) for v in other_side_ids or [] if v),
            "personResponsibleStaffId": matter.get("personResponsibleStaffId"),
            "roleItems": _snapshot_items_tree(matter.get("items")),
        },
        "roles": role_nodes,
    }


def role_snapshot_hash(projection: dict) -> str:
    """sha256 over the canonical JSON form of the projection."""
    canonical = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pull_role_snapshot_hash(matter_id: str) -> str | None:
    """The CURRENT role-snapshot hash for one matter, or None (unknown).

    A bounded connector-venv subprocess pull, run ONLY for matters that carry a
    hold item (holds are rare; the seat is 1 vCPU, and the caller serializes
    these). Every failure — bad id, subprocess error, pull error, unparseable
    payload — degrades to None, never to a guess.
    """
    if not matter_id or not all(ch in _SAFE_MATTER_ID_CHARS for ch in str(matter_id)):
        return None
    connector_python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    try:
        result = subprocess.run(
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter (SMD_CONNECTOR_VENV_PYTHON is the Machine's own boot env, same trust domain / test seam); the snippet is a module constant; the matter id is a connector-read id constrained above to id-safe characters.
            [connector_python, "-c", _SNAPSHOT_PULL_SNIPPET, str(matter_id)],
            capture_output=True,
            text=True,
            timeout=_SNAPSHOT_PULL_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return None
        raw = json.loads((result.stdout or "").strip().splitlines()[-1])
    except Exception:  # noqa: BLE001 — unknown, never a guess
        return None
    if not isinstance(raw, dict) or raw.get("error") or "matter" not in raw:
        return None
    return role_snapshot_hash(role_snapshot_projection(raw.get("matter"), raw.get("roles")))


def hold_matter_snapshot_hashes(
    items,
    ledger,
    ledger_events,
    snapshot_hash_fn,
    *,
    hold_source_id: str,
    hold_label: str,
) -> dict:
    """The current role-snapshot hash per HOLD-BEARING matter.

    Only matters whose hold sentinel has ledger history get a pull (holds are
    rare, and the pull is a second connector subprocess on a 1 vCPU seat). The
    loop is a plain sequential one on purpose — the pulls are SERIALIZED,
    never concurrent. ``items`` are the pre_run's VerificationItems (anything
    with ``matter_id``); ``hold_source_id``/``hold_label`` are the caller's
    hold-sentinel literals (the cross-side contract lives in pre_run.py, which
    passes them in rather than this module re-declaring them). A failed pull
    records None (unknown); a failure here must never stop the decision.
    """
    try:
        states = ledger.derive_state(ledger_events)
    except Exception:  # noqa: BLE001 — the decision path re-derives; degrade
        return {}
    hashes: dict = {}
    for item in items:
        matter_id = item.matter_id
        if not matter_id or matter_id in hashes:
            continue
        try:
            hold_key = ledger.item_key(matter_id, hold_source_id, hold_label, None)
        except Exception:  # noqa: BLE001 - an item whose key cannot be derived is skipped; the snapshot is a best-effort read, never a raise
            continue
        hold_state = states.get(hold_key)
        if hold_state is None or (
            hold_state.attempts == 0 and getattr(hold_state, "determination", None) is None
        ):
            continue
        try:
            hashes[matter_id] = snapshot_hash_fn(matter_id)
        except Exception:  # noqa: BLE001 — unknown, never a guess
            hashes[matter_id] = None
    return hashes
