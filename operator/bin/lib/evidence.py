"""CLI entrypoint for operator/bin/generate-evidence-packet.sh (#894).

Wraps :class:`adapter.evidence.EvidencePacketBuilder` with argument
parsing, audit-writer construction, and the customer.yaml lookup.
Mirrors the shape of :mod:`bin.lib.decommission_cli` so Captain has a
consistent CLI experience across the per-customer ops scripts.

Usage
-----

::

    uv run --quiet --with pyyaml python3 -m bin.lib.evidence \\
        --customer <slug> \\
        --matter <id-or-all> \\
        --from <ISO> \\
        --to <ISO> \\
        --output <path> \\
        --actor <name>

Exit codes
----------

* ``0`` -- packet generated successfully.
* ``2`` -- preflight failed (bad arg, missing customer.yaml).
* ``3`` -- build halted with :class:`EvidencePacketError` (e.g. secret
  leak detected, role gate failed, audit row could not be persisted,
  or a matter-scoped export whose empty audit section would misread as
  "nothing happened"; see ``--acknowledge-unattributed-gap``).
* ``4`` -- unexpected non-build exception.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
# operator/ on sys.path so `from adapter.evidence import ...` resolves.
sys.path.insert(0, str(_HERE.parents[2]))

from adapter.evidence import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    EvidencePacketBuilder,
    EvidencePacketError,
    PacketActor,
    PacketRequest,
)
from adapter.evidence.packet import SqliteReadExecutor  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

log = logging.getLogger("aie.bin.evidence")


# ---------------------------------------------------------------------------
# Local-dev audit + read executors
# ---------------------------------------------------------------------------


_AUDIT_LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  id            TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,
  action_type   TEXT NOT NULL,
  actor         TEXT NOT NULL,
  actor_role    TEXT,
  skill_name    TEXT,
  matter_ref    TEXT,
  input_digest  TEXT,
  output_digest TEXT,
  diff_digest   TEXT,
  trust_ceiling TEXT,
  metadata      TEXT
);
"""


def _build_local_audit_writer(audit_db_path: Path):
    """Construct an AuditLogWriter against a sqlite file on disk.

    Mirrors :func:`bin.lib.decommission_cli._build_local_audit_writer`:
    the evidence script writes its chain-of-custody row to a sqlite file
    when no real D1 binding is provided. In production the wrapper
    passes ``--audit-db`` pointing at the per-customer audit-replica
    snapshot the dashboard worker fetched.
    """
    from adapter.audit_log import AuditLogWriter, SqliteExecutor  # type: ignore

    audit_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(audit_db_path))
    conn.executescript(_AUDIT_LOCAL_SCHEMA)
    return AuditLogWriter(SqliteExecutor(conn)), conn


def _build_local_read_executor(read_db_path: Path) -> tuple[SqliteReadExecutor, sqlite3.Connection]:
    """Open an existing sqlite file as the D1 read backend.

    The CLI defaults the read DB to the same path as the audit DB, which
    lets a single sqlite file back both write + read paths during local
    dev. In production the wrapper passes ``--read-db`` pointing at the
    per-customer audit-export snapshot.
    """
    if not read_db_path.exists():
        # Create an empty database with no tables; `_fetch_safe` treats
        # missing tables as empty, so the build still produces a packet.
        read_db_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(str(read_db_path)).close()
    conn = sqlite3.connect(str(read_db_path))
    return SqliteReadExecutor(conn), conn


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="generate-evidence-packet",
        description=(
            "Generate a compliance evidence packet for one customer + "
            "period (issue 894). Output is a digest-verified tar.gz "
            "(per-artifact SHA-256; manifest hash recorded in the append-only "
            "audit log) containing a PDF, JSON manifest, and per-spec evidence "
            "files. The manifest is NOT yet cryptographically signed "
            "(signature=unsigned-stub); detached signing is a tracked follow-on."
        ),
    )
    p.add_argument("--customer", required=True, help="Customer slug")
    p.add_argument(
        "--matter",
        required=True,
        help="Matter ID, or 'all' for the customer-wide export",
    )
    p.add_argument(
        "--from",
        dest="period_start",
        required=True,
        help="Period start (ISO 8601 UTC, e.g. 2026-04-01T00:00:00Z)",
    )
    p.add_argument(
        "--to",
        dest="period_end",
        required=True,
        help="Period end (ISO 8601 UTC, e.g. 2026-05-01T00:00:00Z)",
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output tar.gz path (will be overwritten if it exists)",
    )
    p.add_argument(
        "--actor",
        required=True,
        help="Operator name written to the COMPLIANCE_PACKET_EXPORTED audit row",
    )
    p.add_argument(
        "--actor-role",
        default="captain",
        choices=sorted(role.value for role in PacketActor),
        help="Role of the actor (captain or compliance); default: captain",
    )
    p.add_argument(
        "--acknowledge-unattributed-gap",
        action="store_true",
        help=(
            "Emit a matter-scoped packet even when it matches zero audit rows "
            "while unattributed rows exist in the period. The packet still "
            "states the gap on its face; the acknowledgement is recorded in "
            "manifest.json and in the COMPLIANCE_PACKET_EXPORTED audit row. "
            "Without this flag such a build halts (exit 3) rather than ship an "
            "empty audit section that reads as 'nothing happened'."
        ),
    )
    p.add_argument(
        "--pinned-head",
        default=None,
        help=(
            "A chain head recorded off the Machine before this export -- the "
            "newest audit_head_history row for this seat on the control plane. "
            "The ledger must still contain it; if it does not, rows that existed "
            "when it was recorded are gone and the build HALTS (exit 3). There "
            "is no acknowledge flag for that case. Without this flag the packet "
            "states on its face that its audit section was not checked for "
            "truncation."
        ),
    )
    p.add_argument(
        "--customer-yaml",
        type=Path,
        default=None,
        help=(
            "Path to customer.yaml; default: "
            "operator/customers/<slug>/customer.yaml"
        ),
    )
    p.add_argument(
        "--audit-db",
        type=Path,
        default=None,
        help=(
            "Sqlite path for the COMPLIANCE_PACKET_EXPORTED row (chain of "
            "custody). Defaults to "
            "operator/customers/<slug>/.evidence-audit.sqlite"
        ),
    )
    p.add_argument(
        "--read-db",
        type=Path,
        default=None,
        help=(
            "Sqlite path that backs D1 reads for this run; default: same "
            "as --audit-db. Production wrapper points this at the "
            "per-customer audit-export snapshot."
        ),
    )
    return p.parse_args(argv)


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    repo_root = Path(__file__).resolve().parents[3]
    customer_dir = repo_root / "operator" / "customers" / args.customer
    customer_yaml = args.customer_yaml or (customer_dir / "customer.yaml")
    audit_db = args.audit_db or (customer_dir / ".evidence-audit.sqlite")
    read_db = args.read_db or audit_db
    return customer_yaml, audit_db, read_db


def _preflight(customer_yaml: Path) -> tuple[bool, Optional[str]]:
    if not customer_yaml.exists():
        return False, f"customer.yaml not found at {customer_yaml}"
    return True, None


async def _run(args: argparse.Namespace) -> int:
    customer_yaml, audit_db, read_db = _resolve_paths(args)

    ok, reason = _preflight(customer_yaml)
    if not ok:
        print(f"[preflight] FAILED: {reason}", file=sys.stderr)
        return 2

    try:
        audit_writer, audit_conn = _build_local_audit_writer(audit_db)
    except Exception as exc:  # noqa: BLE001 - audit writer init failure is exit 4 with the reason printed; the CLI's contract is exit codes, not traces
        print(f"[preflight] audit writer init failed: {exc}", file=sys.stderr)
        return 4

    try:
        read_executor, read_conn = _build_local_read_executor(read_db)
    except Exception as exc:  # noqa: BLE001 - read executor init failure is exit 4 with the reason printed; the CLI's contract is exit codes, not traces
        print(f"[preflight] read executor init failed: {exc}", file=sys.stderr)
        audit_conn.close()
        return 4

    yaml_loader = None
    yaml_dumper = None
    try:
        import yaml  # type: ignore

        yaml_loader = yaml.safe_load
        yaml_dumper = lambda data: yaml.safe_dump(data, sort_keys=True)  # noqa: E731 - a one-line dumper alias bound beside its loader; a def adds only a name
    except ImportError:
        log.warning(
            "pyyaml not installed; falling back to JSON-shaped yaml. "
            "Install pyyaml for full fidelity."
        )

    builder = EvidencePacketBuilder(
        reader=read_executor,
        audit_writer=audit_writer,
        yaml_loader=yaml_loader,
        yaml_dumper=yaml_dumper,
    )

    request = PacketRequest(
        customer_slug=args.customer,
        matter=args.matter,
        period_start=args.period_start,
        period_end=args.period_end,
        output_path=args.output,
        customer_yaml_path=customer_yaml,
        actor=args.actor,
        actor_role=PacketActor(args.actor_role),
        acknowledge_unattributed_gap=args.acknowledge_unattributed_gap,
        pinned_head=args.pinned_head,
    )

    try:
        result = await builder.build(request)
    except EvidencePacketError as exc:
        print(f"[build] HALTED: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - an unexpected build failure is exit 4 with the type named; the CLI's contract is exit codes, not traces
        print(
            f"[build] UNEXPECTED ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 4
    finally:
        try:
            audit_conn.close()
        except Exception:  # noqa: BLE001 - closing the audit connection in finally must not replace the build's own exit code
            pass
        try:
            read_conn.close()
        except Exception:  # noqa: BLE001 - closing the read connection in finally must not replace the build's own exit code
            pass

    summary = {
        "output_path": str(result.output_path),
        "manifest_sha256": result.manifest_sha256,
        "file_count": result.file_count,
        "bytes_written": result.bytes_written,
        "counts": dict(result.counts),
        "coverage": result.coverage.to_dict(),
        "chain_pin": result.chain_pin.to_dict(),
    }
    print(json.dumps(summary, sort_keys=True, indent=2))

    # Surface the coverage boundary on stderr too. The JSON above is
    # machine-readable; an operator scanning the terminal needs to see
    # that this packet's audit section is partial before they forward it.
    if result.coverage.has_unattributed_rows or not result.coverage.table_present:
        for line in result.coverage.narrative_lines():
            print(f"[coverage] {line}", file=sys.stderr)

    # Same reason, one layer down. A packet built without a pin cannot speak to
    # truncation, and the operator forwarding it should see that before they
    # send it, not discover it in the README afterwards.
    if not result.chain_pin.was_checked:
        for line in result.chain_pin.narrative_lines():
            print(f"[chain] {line}", file=sys.stderr)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("AIE_LOG_LEVEL", "INFO"),
        format="[%(asctime)s] [%(name)s] %(message)s",
    )
    args = parse_args(argv)
    try:
        return asyncio.new_event_loop().run_until_complete(_run(args))
    except KeyboardInterrupt:
        print("[evidence] interrupted by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
