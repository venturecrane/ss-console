"""What a seat IS, layer by layer: the inverse of ``provision-customer.sh``.

Decommission used to carry its own idea of where a seat lives, and that idea
drifted from provisioning's. It deleted the prefix ``<slug>/`` in
``ss-ai-employee-smd-skills`` (the smd seat's skills bucket under its
pre-rename name, PR #1189, so every other slug reached into another seat's
bucket) and never touched the bucket provisioning actually creates,
``ss-operator-<slug>-skills`` (provision-customer.sh step 5b; safety invariant
7 requires exactly that name). It cleared three console D1 tables and left the
``customer_configs`` row, the fleet alert and hold rows and the MCP grants.

This module is the one place the seat's footprint is named, so the deleters,
the dry-run survey and the residue report all read the same list. Every
resource is in exactly one of three states here:

* **removed** by a pipeline step (the names below the deleters iterate);
* **retained by design**, with the reason (history and audit tables, the
  object-locked audit archive, Fly volume snapshots, the vault);
* **a repo edit** the script cannot make (it does not commit), printed as the
  exact change and pinned by ``tests/decommissioned-seat-residue.test.ts``.
  Since #3400 the release rings and the Fly app registry are generated from
  the seat's own ``seat.yaml``, so for those two the edit is the tombstone
  rename plus ``npm run gen:seat-registries``; ``workspaces.json`` is still
  hand-kept.

``operator/bin/tests/test_decommission_inventory.py`` builds the console D1
schema from ``migrations/`` and fails when a slug-keyed table is in none of
the classes below, so a new migration cannot add a seat row decommission does
not know about.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping, Optional

# ---------------------------------------------------------------------------
# Cloud resources, named the way provisioning names them
# ---------------------------------------------------------------------------

#: Shared bucket holding every seat's customer.yaml and staged inputs, isolated
#: by key path (provision-customer.sh steps 2 to 2f write ``vaults/<slug>/``).
SHARED_CONFIG_BUCKET = "smd-customer-config"
#: Object-locked nightly audit export (``audit/<slug>/``). The retention copy:
#: never deleted here (docs/specs/operator/audit-retention.md).
AUDIT_ARCHIVE_BUCKET = "smd-audit-archive"


def seat_skills_buckets(slug: str) -> list[str]:
    """The per-seat skill-bodies bucket provisioning creates (step 5b), and the
    same bucket under its name before the AI Employee -> Operator rename
    (PR #1189), which a seat provisioned before the rename still owns."""
    return [f"ss-operator-{slug}-skills", f"ss-ai-employee-{slug}-skills"]


def shared_config_prefixes(slug: str) -> list[tuple[str, str]]:
    """The seat's key prefixes in the shared config bucket."""
    return [(SHARED_CONFIG_BUCKET, f"vaults/{slug}/"), (SHARED_CONFIG_BUCKET, f"customers/{slug}/")]


def r2_targets(slug: str) -> dict:
    """Step 03's targets as the dry run and the step manifest print them."""
    return {
        "prefixes": [f"{b}/{p}" for b, p in shared_config_prefixes(slug)],
        "seat_buckets": seat_skills_buckets(slug),
    }


def seat_fly_apps(slug: str) -> list[str]:
    """The seat app (step 4) and the client's ops workspace
    (operator/ops-machine/workspaces.json), whose volume is the client's
    documents."""
    return [f"hermes-{slug}", f"ops-{slug}"]


def seat_vectorize_indexes(slug: str) -> list[str]:
    return [f"hermes-{slug}-vault", f"hermes-{slug}-corrections"]


def secret_suffix(slug: str) -> str:
    """``NAME__<SUFFIX>`` as provisioning and ops-workspace derive it."""
    return re.sub(r"[^A-Z0-9_]", "", slug.upper().replace("-", "_"))


def ops_sweep_github_names(slug: str) -> tuple[list[str], list[str]]:
    """GitHub Actions secrets and variables ``ops-workspace create`` sets for
    the daily sweep (operator/ops-machine/bin/ops-workspace)."""
    s = secret_suffix(slug)
    return [f"OPS_FLY_TOKEN__{s}", f"OPS_HC_URL__{s}"], [f"OPS_SWEEP_TOKEN_EXPIRES__{s}"]


def seat_webhook_host(slug: str) -> str:
    """The host the seat's AgentMail webhook delivers to (stage-agentmail.sh)."""
    return f"hermes-{slug}.fly.dev"


# ---------------------------------------------------------------------------
# Console D1 (ss-console-db): every table with a seat-slug column, classified
# ---------------------------------------------------------------------------

#: The seat's live projection: rows that say the seat exists, is configured,
#: is reachable or may be granted access. Emptied for the slug, in this order.
#: ``customer_configs`` is last because it is the FK parent of
#: ``machine_credentials`` and ``client_obligations`` (both ON DELETE CASCADE).
SEAT_PROJECTION_TABLES: tuple[str, ...] = (
    "operator_runtime_summary",
    "fleet_status",
    "fleet_alert_state",
    "fleet_holds",
    "mcp_issued_grants",
    "mcp_clerk_bindings",
    "machine_credentials",
    "customer_configs",
)

#: Deleted by the cascade from ``customer_configs``. Exported to the archive
#: dir first, and a live run is refused while any of them is still owed.
OBLIGATIONS_TABLE = "client_obligations"
#: States in which nothing is owed any more (migrations/0117_client_obligations.sql).
TERMINAL_OBLIGATION_STATES: tuple[str, ...] = ("verified", "closed", "cancelled")

_HISTORY = "history of what happened on the seat; the record outlives the seat"

#: Tables kept by design: ``table -> (slug column, reason)``. Nothing here is
#: the seat's live state; every row is history, audit or billing evidence.
RETAINED_SEAT_TABLES: dict[str, tuple[str, str]] = {
    "audit_head_history": ("customer_slug", "audit chain pins: the evidence packet's truncation proof"),
    "captain_time_events": ("customer_slug", "Captain time ledger (billing evidence)"),
    "config_change_audit": ("customer_slug", "audit of config changes"),
    "connector_secret_audit": ("customer_slug", "audit of connector secret writes"),
    "cost_anomaly_alerts": ("customer_slug", "cost history (billing evidence)"),
    "cost_telemetry": ("customer_slug", "cost history (billing evidence)"),
    "customer_config_history": ("customer_slug", "config history"),
    "fleet_rollouts": ("customer_slug", "release history"),
    "hosted_agent_intake": ("customer_slug", _HISTORY),
    "operator_agreement_documents": ("instance_slug", "signed agreements (contract record)"),
    "operator_authority_audit": ("customer_slug", "audit of authority changes"),
    "operator_change_requests": ("customer_slug", _HISTORY),
    "operator_entitlement_changes": ("customer_slug", "audit of entitlement changes"),
    "operator_mcp_audit": ("customer_slug", "audit of MCP access"),
    "operator_mcp_grant_audit": ("customer_slug", "audit of MCP grants"),
    "operator_pause_events": ("customer_slug", _HISTORY),
    "operator_request_cards": ("customer_slug", "send ledger of request cards (no client text)"),
    "operator_runtime_read_audit": ("customer_slug", "audit of runtime reads"),
    "operator_shortfalls": ("customer_slug", _HISTORY),
    "operator_stop_clears": ("customer_slug", _HISTORY),
    "operator_voice_corrections": ("customer_slug", _HISTORY),
    "portal_action_events": ("customer_slug", "audit of portal actions"),
    "subscriptions": ("instance_slug", "billing record; cancellation is the Stripe lifecycle (ADR 0065)"),
}

# ---------------------------------------------------------------------------
# Remaining by design, outside D1
# ---------------------------------------------------------------------------


def remaining_by_design(slug: str) -> list[dict]:
    """What a complete decommission still leaves, and why. Printed on every
    run so "complete" is never read as "nothing remains anywhere"."""
    return [
        {
            "layer": "r2",
            "target": f"{AUDIT_ARCHIVE_BUCKET}/audit/{slug}/",
            "reason": "object-locked audit export; kept for the retention window (audit-retention.md)",
        },
        {
            "layer": "local",
            "target": f"operator/.decommission-archive/{slug}/",
            "reason": "the preserved ledger, memory snapshot, obligations export and signed evidence packet",
        },
        {
            "layer": "fly",
            "target": "volume snapshots of hermes_state",
            "reason": "Fly keeps them until their retention period ends (1 to 60 days, default 5) and "
            "documents no delete; they expire on their own (docs.fly.io/volumes/snapshots)",
        },
        {
            "layer": "d1",
            "target": "entities / organizations",
            "reason": "the client as a business, not the seat; other seats and invoices may hang off it",
        },
        {
            "layer": "sentry",
            "target": "issues tagged with the seat in project smd-operator",
            "reason": "history; they age out under the project's retention",
        },
        {
            "layer": "engagements",
            "target": f"venturecrane/engagements operator/customers/{slug}/",
            "reason": "client material is the engagement record, governed by the client terms, not this script",
        },
    ]


def infisical_names(slug: str, env: Mapping[str, str]) -> dict:
    """The ``NAME__<SUFFIX>`` vault keys for the seat, by NAME only.

    Deleting vault entries is out of scope for this script (a credential the
    Captain may still need to revoke at the vendor first). The run lists them
    so the residue is visible. Read from the process environment, which under
    ``infisical run --env prod --path /ss`` is the vault; values are never
    read into anything but a membership test.
    """
    suffix = "__" + secret_suffix(slug)
    names = sorted(k for k in env if k.endswith(suffix))
    loaded = any(k in env for k in ("R2_ACCESS_KEY_ID", "CF_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID"))
    return {
        "layer": "infisical",
        "target": f"/ss prod keys ending {suffix}",
        "names": names,
        "reason": "kept by design: revoke each at its vendor, then delete it from the vault",
        "enumerated": loaded,
        **({} if loaded else {"note": "environment not loaded from /ss; run under infisical run to list"}),
    }


# ---------------------------------------------------------------------------
# Repo layer: edits the script cannot commit
# ---------------------------------------------------------------------------

RINGS_FILE = "operator/fleet/rings.yaml"
FLY_REGISTRY_FILE = "src/lib/operator/fly-app-registry.generated.ts"
OPS_WORKSPACES_FILE = "operator/ops-machine/workspaces.json"
#: rings.yaml and the Fly app registry are GENERATED from each seat's
#: operator/customers/<slug>/seat.yaml (scripts/lib/seat-inventory.ts, PR
#: #3400). The tombstone rename takes the seat out of the inventory; this
#: command takes it out of both files. tests/seat-inventory.test.ts fails
#: while either is stale.
REGEN_COMMAND = "npm run gen:seat-registries"


def _rings_edits(slug: str, path: Path) -> list[str]:
    if not path.is_file():
        return []
    import yaml  # the CLI runs under `uv run --with pyyaml`

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        f"still lists {slug} in ring {entry.get('ring')}: run `{REGEN_COMMAND}` after the tombstone rename"
        for entry in raw.get("rings") or []
        if slug in (entry.get("seats") or [])
    ]


def _registry_edits(slug: str, path: Path) -> list[str]:
    if not path.is_file():
        return []
    key = re.compile(r"^\s*['\"]?" + re.escape(slug) + r"['\"]?\s*:")
    return [
        f"line {n} still maps the seat ({line.strip()}): run `{REGEN_COMMAND}` after the tombstone rename"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if key.match(line)
    ]


def _workspace_edits(slug: str, path: Path) -> list[str]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [f'remove .workspaces["{slug}"]'] if slug in (data.get("workspaces") or {}) else []


def repo_edits(slug: str, repo_root: Path, customers_root: Optional[Path] = None) -> list[dict]:
    """Every authored allow-list that still names the seat, with the exact
    edit. Empty means the repo layer is clean for these files."""
    out: list[dict] = []
    for rel, finder in (
        (RINGS_FILE, _rings_edits),
        (FLY_REGISTRY_FILE, _registry_edits),
        (OPS_WORKSPACES_FILE, _workspace_edits),
    ):
        for edit in finder(slug, repo_root / rel):
            out.append({"file": rel, "edit": edit})
    root = customers_root or repo_root / "operator" / "customers"
    if (root / slug).is_dir():
        out.append({"file": f"operator/customers/{slug}/", "edit": "still live: the tombstone step has not run"})
    for tomb in sorted(root.glob(f"{slug}.decommissioned.*")):
        out.append(
            {
                "file": f"operator/customers/{tomb.name}/",
                "edit": f"commit the rename (its seat.yaml goes with it) and the `{REGEN_COMMAND}` output, with "
                "the edits above, in one PR; regenerate operator/contracts/"
                "gate-coverage-snapshot*.json and copy the .overlay.json into hermes-smd-overlay "
                "tests/contract/seat_gate_binding_snapshot.json in the same change",
            }
        )
    return out


def residue_report(
    slug: str,
    repo_root: Path,
    customers_root: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> dict:
    """Everything that is not removed by the pipeline: the repo edits still
    owed, the vault names, and what is kept by design. The CLI prints it on
    every run, dry or live."""
    return {
        "repo_edits": repo_edits(slug, repo_root, customers_root),
        "infisical": infisical_names(slug, os.environ if env is None else env),
        "retained_by_design": remaining_by_design(slug),
        "retained_d1_tables": {t: reason for t, (_, reason) in RETAINED_SEAT_TABLES.items()},
    }


__all__ = [
    "AUDIT_ARCHIVE_BUCKET",
    "FLY_REGISTRY_FILE",
    "OBLIGATIONS_TABLE",
    "OPS_WORKSPACES_FILE",
    "RETAINED_SEAT_TABLES",
    "RINGS_FILE",
    "SEAT_PROJECTION_TABLES",
    "SHARED_CONFIG_BUCKET",
    "TERMINAL_OBLIGATION_STATES",
    "infisical_names",
    "ops_sweep_github_names",
    "r2_targets",
    "remaining_by_design",
    "repo_edits",
    "residue_report",
    "seat_fly_apps",
    "seat_skills_buckets",
    "seat_vectorize_indexes",
    "seat_webhook_host",
    "secret_suffix",
    "shared_config_prefixes",
]
