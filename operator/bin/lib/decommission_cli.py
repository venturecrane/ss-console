"""CLI entrypoint for operator/bin/decommission-customer.sh (issue #820).

Wraps :class:`bin.lib.decommission.DecommissionPipeline` with argument
parsing, audit-writer construction, and the customer.yaml lookup. Called
by the shell wrapper or directly via:

    uv run --quiet --with pyyaml python3 \
        -m bin.lib.decommission_cli <slug> [--dry-run] [--live]

Exit codes
----------

* ``0`` — dry-run completed, or live decommission completed cleanly.
* ``2`` — pre-flight failed (missing slug, no customer.yaml, a ``--live``
  run without ``--confirm-slug <slug>`` matching the positional slug, etc.).
* ``3`` — live decommission halted mid-sequence (a step raised
  :class:`DecommissionStepFailed`). Re-run with the same slug to resume
  from the last completed step (every step is idempotent).
* ``4`` — unexpected non-step exception (audit writer init failure,
  config-parse failure, etc.).
* ``5`` — refused: a ``--live`` run was requested but one or more
  destructive backends are unwired, so the run would report a clean
  decommission while customer data remains (issue #1123). The real
  backends live in ``bin.lib.decommission_backends`` and wire themselves
  from the environment; the refusal names the credential each missing one
  needs. Stage them (``infisical run --env prod --path /ss -- ...``), or
  pass ``--allow-unwired`` for a dev/fixture run that explicitly tolerates
  skipped deletions. ``--allow-unwired`` is itself refused at exit 5 unless
  ``--customers-root`` was passed explicitly and resolves OUTSIDE the repo's
  ``operator/customers``: the flag only skips the UNWIRED backends, every
  wired one still runs, so against the real customers root it would destroy
  a real seat's Fly app while printing that the run "does NOT fully
  decommission the customer". A fixture run lives in a fixture root.

Two confirmations guard a live run, both mechanical:

* ``--confirm-slug <slug>`` must equal the positional slug (exit 2 otherwise).
  The shell wrapper prompts for it on a TTY; a non-interactive caller passes
  it explicitly. Typing the slug twice is the same act ``fly apps destroy``
  asks for before this code passes ``--yes`` on the Captain's behalf.
* Every destructive backend arms itself ONLY from a staged credential. In
  particular ``FLY_API_TOKEN`` must be set; a logged-in ``fly`` CLI no longer
  counts, because the Captain's shell is logged in most of the time and the
  destroyer was arming on every run without anyone staging anything.

Credentials the real backends read (all optional; an absent one leaves that
backend unwired and the ``--live`` gate armed):

* ``CLOUDFLARE_API_TOKEN`` + ``CLOUDFLARE_ACCOUNT_ID`` (or the provisioning
  script's ``CF_API_TOKEN`` / ``CF_ACCOUNT_ID``) — R2 prefix deletion; the
  ``CLOUDFLARE_`` names also drive wrangler for Vectorize and D1.
* ``AGENTMAIL_API_KEY`` — the org key; deletes the seat's inbox.
* ``FLY_API_TOKEN`` — ``fly apps destroy``.
* ``HEALTHCHECKS_API_KEY`` — deletes the seat's healthchecks.io check.
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
# Make the operator package importable when this module is run as a
# script from inside the repo. operator/ becomes the path root so
# `from adapter.audit_log import ...` resolves.
sys.path.insert(0, str(_HERE.parents[2]))

# Imported after sys.path tweak.
from bin.lib.decommission import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    DecommissionPipeline,
    DecommissionStepFailed,
    StepResult,
    _load_customer_yaml,
)
from bin.lib.decommission_backends import BACKEND_REQUIREMENTS, backends_from_env  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from bin.lib.seam_pull import SeamAuditLogPreserver, seam_client_from_env  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

log = logging.getLogger("aie.bin.decommission_cli")


# ---------------------------------------------------------------------------
# Local-dev audit executor
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

    The decommission script runs from Captain's workstation and writes
    its trail to a per-customer sqlite file under
    ``operator/customers/{slug}/.decommission-audit.sqlite`` before
    the customer directory is tombstoned. This is intentionally distinct
    from the per-customer D1 (which is itself being deleted as part of
    the sequence); we want the audit row of decommission to survive
    after the customer's substrate is gone.
    """
    from adapter.audit_log import AuditLogWriter, SqliteExecutor

    audit_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(audit_db_path))
    conn.executescript(_AUDIT_LOCAL_SCHEMA)
    return AuditLogWriter(SqliteExecutor(conn)), conn


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_step(result: StepResult, *, stream=sys.stdout) -> None:
    """One step result, one line. Easy to diff between dry-run and live."""
    detail_json = json.dumps(result.detail, sort_keys=True, separators=(",", ":"))
    print(f"[{result.status.value:>8}] {result.name}: {detail_json}", file=stream)


def _print_header(slug: str, *, mode: str, stream=sys.stdout) -> None:
    print(f"# decommission-customer (issue 820) customer={slug} mode={mode}", file=stream)


def _print_footer(slug: str, *, mode: str, ok: bool, stream=sys.stdout) -> None:
    status = "OK" if ok else "FAILED"
    print(f"# decommission-customer end customer={slug} mode={mode} status={status}", file=stream)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="decommission-customer",
        description=(
            "Decommission an Operator customer end-to-end (issue 820). "
            "Default is dry-run; pass --live to execute deletions."
        ),
    )
    p.add_argument("slug", help="Customer slug (matches operator/customers/<slug>/customer.yaml)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the per-step plan without executing anything (default).",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Execute the deletion sequence. Halts on any failure.",
    )
    p.add_argument(
        "--confirm-slug",
        type=str,
        default=None,
        metavar="SLUG",
        help=("Required with --live: must equal the positional slug. The shell wrapper prompts for it on a TTY."),
    )
    p.add_argument(
        "--allow-unwired",
        action="store_true",
        help=(
            "Permit a --live run even when destructive backends are unwired "
            "stubs. DEV/FIXTURE ONLY, and enforced as such: refused unless "
            "--customers-root is given and lies outside the repo's "
            "operator/customers. The wired backends still run; only the "
            "unwired ones are skipped."
        ),
    )
    p.add_argument(
        "--customers-root",
        type=Path,
        default=None,
        help="Override the customers/ directory (used by tests).",
    )
    p.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="Override the compliance-archive directory (used by tests).",
    )
    p.add_argument(
        "--audit-db",
        type=Path,
        default=None,
        help="Override the local audit-log sqlite path (used by tests).",
    )
    p.add_argument(
        "--actor",
        type=str,
        default=os.environ.get("DECOMMISSION_ACTOR", "captain"),
        help="Actor name written to audit rows (default: captain or $DECOMMISSION_ACTOR).",
    )
    return p.parse_args(argv)


def _default_customers_root() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "operator" / "customers"


def _allow_unwired_refusal(args: argparse.Namespace) -> Optional[str]:
    """Why ``--allow-unwired`` is refused for this invocation, or None.

    The flag skips the UNWIRED backends only; every wired one still executes.
    So it is safe exactly when the customers root is a fixture root, and the
    only way to know that is that the caller named one, outside the repo's
    real ``operator/customers``.
    """
    if not args.allow_unwired:
        return None
    if args.customers_root is None:
        return "--allow-unwired requires an explicit --customers-root outside operator/customers"
    real_root = _default_customers_root().resolve()
    given = Path(args.customers_root).resolve()
    if given == real_root or real_root in given.parents:
        return (
            f"--allow-unwired refused: --customers-root {given} is the repo's real "
            "operator/customers; a fixture run needs a fixture root"
        )
    return None


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Resolve customers_root / archive_root / audit_db with sensible defaults."""
    repo_root = Path(__file__).resolve().parents[3]
    customers_root = args.customers_root or _default_customers_root()
    archive_root = args.archive_root or (repo_root / "operator" / ".decommission-archive")
    audit_db = args.audit_db or (customers_root / args.slug / ".decommission-audit.sqlite")
    return customers_root, archive_root, audit_db


def _preflight(customers_root: Path, slug: str) -> tuple[bool, Optional[str]]:
    """Returns (ok, reason). Idempotency-friendly: missing dir is OK
    iff a tombstoned copy already exists (treat as completed)."""
    if not slug or not slug.replace("-", "").isalnum():
        return False, f"invalid slug {slug!r} (must match ^[a-z0-9-]+$)"
    live = customers_root / slug
    tomb_glob = list(customers_root.glob(f"{slug}.decommissioned.*"))
    if not live.exists() and not tomb_glob:
        return False, (
            f"customer dir not found: {live} (and no tombstone present at "
            f"{customers_root}/{slug}.decommissioned.*); nothing to decommission"
        )
    return True, None


async def _run(args: argparse.Namespace) -> int:
    customers_root, archive_root, audit_db = _resolve_paths(args)

    ok, reason = _preflight(customers_root, args.slug)
    if not ok:
        print(f"[preflight] FAILED: {reason}", file=sys.stderr)
        return 2

    if args.live and args.confirm_slug != args.slug:
        print(
            f"[preflight] FAILED: --live requires --confirm-slug {args.slug!r} "
            f"(got {args.confirm_slug!r}); nothing was touched",
            file=sys.stderr,
        )
        return 2

    refusal = _allow_unwired_refusal(args)
    if refusal is not None:
        print(f"[live] REFUSING: {refusal}; nothing was touched", file=sys.stderr)
        return 5

    mode = "live" if args.live else "dry-run"
    _print_header(args.slug, mode=mode)

    try:
        audit_writer, audit_conn = _build_local_audit_writer(audit_db)
    except Exception as exc:  # noqa: BLE001 - audit writer init failure is exit 4 with the reason printed; the CLI's contract is exit codes, not traces
        print(f"[preflight] audit writer init failed: {exc}", file=sys.stderr)
        return 4

    # Load customer.yaml so the audit-log retention carve-out resolves the
    # right per-vertical default + override (audit-retention.md #893). Missing
    # YAML is tolerated: the pipeline falls back to the conservative 2555-day
    # window. We do this before the pipeline is constructed so a YAML parse
    # error surfaces here, not mid-step.
    customer_yaml = _load_customer_yaml(customers_root, args.slug)

    # Pull-before-destroy (#1355): wire the seam-based preserver when the
    # runtime-read env is staged (OPERATOR_RUNTIME_READ_SECRET +
    # OPERATOR_RUNTIME_READ_URL). When absent, the pipeline keeps its
    # InMemory stub and unwired_destructive_backends() blocks a --live run —
    # destroying the Machine without a real preservation pull would burn the
    # only copy of the audit ledger.
    seam_client = seam_client_from_env(args.slug)
    pipeline_kwargs: dict = {}
    if seam_client is not None:
        pipeline_kwargs["audit_log_preserver"] = SeamAuditLogPreserver(seam_client)

    # The destructive backends (R2, Vectorize, AgentMail, Fly, observability)
    # wire themselves from whatever credentials are staged. An absent
    # credential leaves that backend as its stub, and the #1123 gate below
    # refuses the --live run naming exactly what is missing.
    backend_kwargs, wired = backends_from_env(args.slug, customers_root)
    pipeline_kwargs.update(backend_kwargs)
    for name, ok in wired.items():
        print(
            f"[backends] {name}: {'wired' if ok else 'UNWIRED (needs ' + BACKEND_REQUIREMENTS[name] + ')'}",
            file=sys.stderr,
        )

    pipeline = DecommissionPipeline(
        customer_slug=args.slug,
        customers_root=customers_root,
        archive_root=archive_root,
        audit_writer=audit_writer,
        actor=args.actor,
        customer_yaml=customer_yaml,
        **pipeline_kwargs,
    )

    # Fail closed (#1123): a --live run that cannot actually delete must
    # not report success. Refuse BEFORE writing any audit row or
    # tombstoning the customer directory.
    if args.live:
        unwired = pipeline.unwired_destructive_backends()
        if unwired and not args.allow_unwired:
            needs = "; ".join(f"{name} needs {BACKEND_REQUIREMENTS.get(name, 'its client')}" for name in unwired)
            print(
                "[live] REFUSING: destructive backend(s) not wired — "
                f"{', '.join(unwired)}. A --live run would report a clean "
                "decommission while that customer data, the Fly Machine, and "
                f"its secrets remain. Stage the credentials ({needs}), or pass "
                "--allow-unwired with a fixture --customers-root for a "
                "dev/fixture run that explicitly tolerates skipped deletions.",
                file=sys.stderr,
            )
            try:
                audit_conn.close()
            except Exception:  # noqa: BLE001 - closing the audit connection on the refusal path must not mask the refusal being reported
                pass
            _print_footer(args.slug, mode=mode, ok=False)
            return 5
        if unwired and args.allow_unwired:
            print(
                "[live] WARNING: --allow-unwired set; the following "
                "destructive backend(s) will be SKIPPED, not deleted: "
                f"{', '.join(unwired)}. This run does NOT fully decommission "
                "the customer.",
                file=sys.stderr,
            )

    try:
        if args.live:
            results = await pipeline.run()
        else:
            results = await pipeline.plan()
    except DecommissionStepFailed as exc:
        print(
            f"[live] HALTED at step {exc.step_name}: {exc.cause}",
            file=sys.stderr,
        )
        _print_footer(args.slug, mode=mode, ok=False)
        return 3
    except Exception as exc:  # noqa: BLE001 - the CLI's contract is exit 4 with the error type named; an unhandled trace would lose the footer
        print(f"[live] UNEXPECTED ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        _print_footer(args.slug, mode=mode, ok=False)
        return 4
    finally:
        try:
            audit_conn.close()
        except Exception:  # noqa: BLE001 - closing the audit connection in finally must not replace the step's own exit code
            pass

    for r in results:
        _print_step(r)

    _print_footer(args.slug, mode=mode, ok=True)
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
        print("[decommission] interrupted by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
