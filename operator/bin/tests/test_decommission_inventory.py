"""The seat inventory stays the inverse of provisioning and of the schema.

Two drift guards and the residue report:

* every console D1 table with a seat-slug column is classified exactly once
  (removed, retained with a reason, or the cascaded obligation register), so
  a migration cannot add a seat row decommission does not know about;
* every table that cascades from ``customer_configs`` is one decommission
  handles on purpose;
* the names decommission removes are the names ``provision-customer.sh`` and
  ``render-fly-toml.sh`` create, read from those files;
* the residue report names the exact repo edits still owed and lists vault
  keys by NAME only.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from bin.lib.decommission_inventory import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    OBLIGATIONS_TABLE,
    RETAINED_SEAT_TABLES,
    SEAT_PROJECTION_TABLES,
    infisical_names,
    repo_edits,
    residue_report,
    seat_skills_buckets,
    secret_suffix,
)

REPO_ROOT = _HERE.parents[3]
SEAT_COLUMNS = ("customer_slug", "instance_slug")


def _schema() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for mig in sorted((REPO_ROOT / "migrations").glob("*.sql")):
        conn.executescript(mig.read_text(encoding="utf-8"))
    return conn


def _seat_keyed_tables(conn: sqlite3.Connection) -> dict[str, str]:
    out = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")}
        hit = [c for c in SEAT_COLUMNS if c in cols]
        if hit:
            out[table] = hit[0]
    return out


def test_every_seat_keyed_table_is_classified_exactly_once():
    discovered = _seat_keyed_tables(_schema())
    removed = set(SEAT_PROJECTION_TABLES)
    retained = set(RETAINED_SEAT_TABLES)
    assert not removed & retained, removed & retained
    assert OBLIGATIONS_TABLE not in removed | retained
    classified = removed | retained | {OBLIGATIONS_TABLE}
    unclassified = sorted(set(discovered) - classified)
    assert not unclassified, (
        f"seat-keyed console tables decommission does not know about: {unclassified}. Add each to "
        "SEAT_PROJECTION_TABLES (the seat's live state: removed) or RETAINED_SEAT_TABLES (history: kept, "
        "with the reason) in operator/bin/lib/decommission_inventory.py"
    )
    stale = sorted(classified - set(discovered))
    assert not stale, f"inventory names tables no migration creates: {stale}"


def test_retained_tables_name_the_real_slug_column():
    discovered = _seat_keyed_tables(_schema())
    for table, (column, reason) in RETAINED_SEAT_TABLES.items():
        assert discovered[table] == column, table
        assert reason.strip(), table


def test_projection_tables_are_keyed_by_customer_slug():
    discovered = _seat_keyed_tables(_schema())
    assert all(discovered[t] == "customer_slug" for t in SEAT_PROJECTION_TABLES)


def test_everything_that_cascades_from_customer_configs_is_handled_on_purpose():
    conn = _schema()
    cascading = set()
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        for fk in conn.execute(f"PRAGMA foreign_key_list('{table}')"):
            if fk[2] == "customer_configs":
                cascading.add(table)
    assert cascading <= set(SEAT_PROJECTION_TABLES) | {OBLIGATIONS_TABLE}, cascading
    # customer_configs is the FK parent, so it is emptied LAST.
    assert SEAT_PROJECTION_TABLES[-1] == "customer_configs"


def test_removed_bucket_is_the_one_provisioning_creates():
    provision = (REPO_ROOT / "operator/bin/provision-customer.sh").read_text(encoding="utf-8")
    render = (REPO_ROOT / "operator/bin/lib/render-fly-toml.sh").read_text(encoding="utf-8")
    assert 'R2_SKILL_BODIES_BUCKET="ss-operator-${SLUG}-skills"' in provision
    assert 'skill_bodies="ss-operator-${SLUG}-skills"' in render
    assert seat_skills_buckets("acme")[0] == "ss-operator-acme-skills"
    # The shared, pre-rename smd bucket is never a target for another seat.
    assert "ss-ai-employee-smd-skills" not in seat_skills_buckets("acme")


def test_secret_suffix_matches_provisioning():
    assert secret_suffix("ashton-price") == "ASHTON_PRICE"
    assert secret_suffix("captain-mark") == "CAPTAIN_MARK"


def test_infisical_names_lists_names_and_never_values():
    env = {
        "AGENTMAIL_SEND_API_KEY__ACME": "s3cret-value-1",
        "ANTHROPIC_API_KEY__ACME": "s3cret-value-2",
        "ANTHROPIC_API_KEY__ACME_TWO": "s3cret-value-3",
        "R2_ACCESS_KEY_ID": "s3cret-value-4",
    }
    found = infisical_names("acme", env)
    assert found["names"] == ["AGENTMAIL_SEND_API_KEY__ACME", "ANTHROPIC_API_KEY__ACME"]
    assert found["enumerated"] is True
    assert "s3cret" not in repr(found)
    unloaded = infisical_names("acme", {})
    assert unloaded["enumerated"] is False and "infisical run" in unloaded["note"]


def _fake_repo(tmp_path: Path) -> Path:
    (tmp_path / "operator/fleet").mkdir(parents=True)
    (tmp_path / "operator/fleet/rings.yaml").write_text(
        "rings:\n  - ring: 0\n    seats: [staging]\n  - ring: 2\n    seats: [other, acme]\n"
    )
    (tmp_path / "src/lib/operator").mkdir(parents=True)
    (tmp_path / "src/lib/operator/fly-app-registry.ts").write_text(
        "const CUSTOMER_FLY_APPS = Object.freeze({\n"
        "  'acme-two': 'hermes-acme-two',\n"
        "  'acme': 'hermes-acme',\n"
        "  other: 'hermes-other',\n"
        "})\n"
    )
    (tmp_path / "operator/ops-machine").mkdir(parents=True)
    (tmp_path / "operator/ops-machine/workspaces.json").write_text('{"workspaces": {"acme": {"app": "ops-acme"}}}')
    (tmp_path / "operator/customers/acme.decommissioned.2026-10-22").mkdir(parents=True)
    return tmp_path


def test_repo_edits_name_the_exact_change_in_each_allow_list(tmp_path):
    edits = repo_edits("acme", _fake_repo(tmp_path))
    by_file = {}
    for e in edits:
        by_file.setdefault(e["file"], []).append(e["edit"])
    assert by_file["operator/fleet/rings.yaml"] == ["remove acme from ring 2 seats"]
    assert by_file["src/lib/operator/fly-app-registry.ts"] == ["delete line 3: 'acme': 'hermes-acme',"]
    assert by_file["operator/ops-machine/workspaces.json"] == ['remove .workspaces["acme"]']
    tomb = by_file["operator/customers/acme.decommissioned.2026-10-22/"][0]
    assert "commit the rename" in tomb and "seat_gate_binding_snapshot.json" in tomb


def test_repo_edits_are_empty_for_a_seat_no_file_names(tmp_path):
    assert repo_edits("ghost", _fake_repo(tmp_path)) == []


def test_residue_report_always_states_what_is_kept_and_why(tmp_path):
    report = residue_report("acme", _fake_repo(tmp_path), env={})
    layers = {item["layer"] for item in report["retained_by_design"]}
    assert {"r2", "fly", "local", "d1", "engagements", "sentry"} <= layers
    assert all(item["reason"] for item in report["retained_by_design"])
    assert set(report["retained_d1_tables"]) == set(RETAINED_SEAT_TABLES)


def test_the_real_repo_names_no_unknown_seat():
    """Against this checkout: a slug that was never a seat has no residue."""
    assert repo_edits("never-a-real-seat", REPO_ROOT) == []
