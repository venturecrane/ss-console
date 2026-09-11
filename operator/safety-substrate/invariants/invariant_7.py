"""Invariant #7 - cross-Machine query prohibition (Phase 1 storage model).

Per platform PRD §7.5 and `docs/adr/0009-cross-machine-query-prohibition.md`:

    Cross-Machine query prohibition. No agent reads storage bound to
    another customer's Machine. At Machine boot, the runtime verifies
    its storage bindings include only its own customer's namespaces
    and refuses to start if it detects bindings outside its namespace.

This module implements the boot-time enforcement of that promise. Cross-
Machine isolation is the platform's load-bearing trust commitment: any
customer who can convince themselves that another customer's data could
ever surface inside their agent will not sign. The mechanism is
deliberately mechanical - no heuristics, no machine-learning, no fuzzy
matching. The Machine's storage either resolves to *this* customer's
namespace or the runtime refuses to serve.

What "storage binding" actually means in Phase 1
------------------------------------------------

The original draft of this invariant validated a fictional set of
Cloudflare bindings (``hermes-{slug}-{d1,r2,vault,corrections}``). That
storage model was never built. Phase 1 storage is concrete and lives in
two places, both derivable from ``CUSTOMER_SLUG``:

* **Per-Machine SQLite files on the mounted Fly volume** (``/opt/data``).
  Per ADR 0007 the volume *is* the customer boundary - it is attached to
  exactly one Machine, which serves exactly one customer. The audit
  ledger (``SMD_D1_AUDIT_BINDING`` = ``/opt/data/audit/audit.db``) and
  the mutable agent-state DB (``SMD_D1_AGENT_STATE_BINDING`` =
  ``/opt/data/agent-state.db``) are paths *under that volume*. The
  isolation guarantee for these is: the configured path resolves inside
  ``/opt/data`` and embeds no foreign customer slug. A path that escapes
  the volume root, or that names a *different* customer's slug, is the
  leak this invariant exists to catch.

* **R2 buckets named in the Fly env.** Two buckets:

    - ``R2_SKILL_BODIES_BUCKET`` is **per-customer**: it MUST equal
      ``ss-operator-{slug}-skills``. This is the cross-tenant leak
      surface - a Machine whose skill-bodies bucket names *another*
      customer's slug would write/read that customer's agent-authored
      skills. The check derives the expected name from ``CUSTOMER_SLUG``
      and refuses on any mismatch.

    - ``R2_BUCKET_CONFIG`` is **shared** across customers (default
      ``smd-customer-config``). Per-customer isolation here lives in the
      key *path* (``vaults/{slug}/customer.yaml``), not the bucket name,
      so this invariant does NOT require it to embed the slug. What it
      DOES enforce is that the config bucket is not accidentally pointed
      at some *other* customer's per-customer skill-bodies bucket
      (``ss-operator-{otherslug}-skills``) - that would be a
      misprovisioning that leaks another tenant's namespace.

* **Slug agreement.** The SMD overlay plugins resolve the per-customer
  namespace via ``SMD_CUSTOMER_SLUG``; the Hermes runtime and the
  provisioner use ``CUSTOMER_SLUG``. Both are set by ``fly.toml`` to the
  same value. If they disagree, the audit/voice/memory plugins would
  namespace to a different customer than the runtime - so the check
  treats a slug disagreement as a violation.

Source of the naming contract: ``operator/bin/provision-customer.sh``
(``R2_SKILL_BODIES_BUCKET="ss-operator-${SLUG}-skills"``,
``R2_BUCKET_CONFIG`` default ``smd-customer-config``) and
``operator/templates/fly.toml.template`` ([env] paths). This module is the
single in-runtime source of truth for what each binding MUST resolve to,
derived from the slug.

Design notes
------------

* **Boot-time only.** This invariant is not a runtime filter. It runs
  once at Machine startup before the gateway serves any request and
  refuses to start the runtime on any mismatch. Once the Machine is
  running, the bindings are immutable (Fly env + the mounted volume don't
  change under a live Machine), so a single boot-time check is sufficient.

* **Pure core, side-effecting shell.** ``verify_storage_bindings`` is a
  pure function over a ``BindingSnapshot`` - no env reads, no I/O - so it
  is exhaustively unit-testable. ``collect_snapshot_from_env`` reads the
  real env into a snapshot. ``verify_at_boot`` is the thin boot entry that
  wires env -> verify -> audit-emit -> exit code.

* **Refusal shape.** ``verify_storage_bindings`` returns a result object
  describing any mismatches. Production callers MUST treat
  ``passed=False`` as a fatal boot failure and exit ``3`` (per
  ``r2-vectorize-naming.md`` / ADR 0009). The pure function never
  ``sys.exit``s; ``verify_at_boot`` returns the exit code for the caller
  (entrypoint/bootstrap) to ``sys.exit`` on.

* **Audit emission, pytest-free.** A failure writes one
  ``INVARIANT_BOOT_CHECK_FAILED`` row through the existing Workspace
  broker audit socket (``SMD_AUDIT_BROKER_SOCKET`` -> ``audit_append``,
  the OP-P1-4 append-only ledger path). The emit uses only the stdlib
  (``socket``/``json``) so the boot path imports cleanly in the customer
  Machine venv, which has no pytest. If the broker socket is unavailable
  the failure is still surfaced on stderr and the nonzero exit code still
  refuses the boot - audit-emit best-effort must never *weaken* the
  refusal.

* **No PII in the failure row.** The metadata names the EXPECTED and
  OBSERVED binding values. These embed customer slugs (business slug, not
  a natural-person identifier) and volume paths. No customer data is
  exfiltrated by the failure row.

Module shape
------------

::

    from invariants.invariant_7 import (
        BindingSnapshot,
        collect_snapshot_from_env,
        verify_storage_bindings,
        verify_at_boot,
    )

    # boot entry (entrypoint/bootstrap):
    rc = verify_at_boot()
    if rc != 0:
        sys.exit(3)
"""

from __future__ import annotations

import enum
import json
import logging
import os
import posixpath
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

log = logging.getLogger("aie.invariants.invariant_7")


# ---------------------------------------------------------------------------
# The volume root that is the per-Machine (== per-customer) boundary.
# Mirrors fly.toml.template [mounts].destination / HERMES_HOME.
# ---------------------------------------------------------------------------

_VOLUME_ROOT = "/opt/data"

# Default shared config bucket name (provision-customer.sh:
# R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}").
_DEFAULT_CONFIG_BUCKET = "smd-customer-config"

# The per-customer skill-bodies bucket prefix/suffix
# (provision-customer.sh: ss-operator-${SLUG}-skills).
_SKILL_BUCKET_PREFIX = "ss-operator-"
_SKILL_BUCKET_SUFFIX = "-skills"


# ---------------------------------------------------------------------------
# Binding kinds (closed set, mirroring the real Phase-1 surface)
# ---------------------------------------------------------------------------


class BindingKind(str, enum.Enum):
    """The safety-relevant storage bindings per the Phase-1 model.

    Adding a new kind requires (a) an ADR, (b) updates here, (c) updates
    in ``fly.toml.template`` / ``provision-customer.sh``, and (d) updates
    in the env-consumption contract.
    """

    SKILL_BODIES_BUCKET = "r2_skill_bodies_bucket"  # per-slug R2 bucket
    CONFIG_BUCKET = "r2_bucket_config"              # shared R2 bucket
    AUDIT_DB = "smd_d1_audit_binding"               # SQLite path on volume
    AGENT_STATE_DB = "smd_d1_agent_state_binding"   # SQLite path on volume


# Env-var name each kind is read from (used by collect_snapshot_from_env
# and for human-facing diagnostics).
_ENV_VAR_FOR_KIND: dict[BindingKind, str] = {
    BindingKind.SKILL_BODIES_BUCKET: "R2_SKILL_BODIES_BUCKET",
    BindingKind.CONFIG_BUCKET: "R2_BUCKET_CONFIG",
    BindingKind.AUDIT_DB: "SMD_D1_AUDIT_BINDING",
    BindingKind.AGENT_STATE_DB: "SMD_D1_AGENT_STATE_BINDING",
}


def _expected_skill_bucket(customer_slug: str) -> str:
    """The per-customer skill-bodies bucket name derived from the slug."""
    return f"{_SKILL_BUCKET_PREFIX}{customer_slug}{_SKILL_BUCKET_SUFFIX}"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BindingSnapshot:
    """Observed storage bindings at Machine boot.

    Production callers populate this from the Fly env + fly.toml [env]
    (see :func:`collect_snapshot_from_env`). Tests populate it directly.

    ``customer_slug`` is the runtime's ``CUSTOMER_SLUG``; ``overlay_slug``
    is the overlay's ``SMD_CUSTOMER_SLUG``. They must agree.

    Every binding field is a string. An empty string means "unbound" -
    itself a violation, because the runtime cannot serve without all four
    bindings present.
    """

    customer_slug: str
    overlay_slug: str
    skill_bodies_bucket: str
    config_bucket: str
    audit_db_path: str
    agent_state_db_path: str

    def __post_init__(self) -> None:
        for field_name in (
            "customer_slug",
            "overlay_slug",
            "skill_bodies_bucket",
            "config_bucket",
            "audit_db_path",
            "agent_state_db_path",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string, got {type(value).__name__}"
                )

    def to_kind_map(self) -> dict[BindingKind, str]:
        """Render the four bindings as a kind-keyed map for comparison."""
        return {
            BindingKind.SKILL_BODIES_BUCKET: self.skill_bodies_bucket,
            BindingKind.CONFIG_BUCKET: self.config_bucket,
            BindingKind.AUDIT_DB: self.audit_db_path,
            BindingKind.AGENT_STATE_DB: self.agent_state_db_path,
        }


@dataclass(frozen=True)
class BindingMismatch:
    """One binding that does not resolve to this customer's namespace."""

    kind: BindingKind
    expected: str
    observed: str
    reason: str


@dataclass(frozen=True)
class Invariant7Violation:
    """Aggregated result of :func:`verify_storage_bindings`.

    Empty ``mismatches`` tuple = the snapshot resolves entirely to this
    customer's namespace; ``passed`` is True. Any mismatch flips
    ``passed`` to False and the runtime MUST refuse to start.
    """

    customer_slug: str
    mismatches: tuple[BindingMismatch, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return len(self.mismatches) == 0

    def __bool__(self) -> bool:
        # Truthy iff there is a violation. Lets callers write
        # ``if violation := verify_storage_bindings(...): exit(3)``.
        return not self.passed

    def to_audit_metadata(self) -> dict:
        """Stable shape for ``INVARIANT_BOOT_CHECK_FAILED`` rows."""
        return {
            "invariant": 7,
            "customer_slug": self.customer_slug,
            "mismatches": [
                {
                    "kind": m.kind.value,
                    "expected": m.expected,
                    "observed": m.observed,
                    "reason": m.reason,
                }
                for m in self.mismatches
            ],
        }

    def refusal_message(self) -> str:
        """One-line operator-facing summary for stdout."""
        if self.passed:
            return ""
        parts = [
            f"{m.kind.value}={m.observed!r} (expected {m.expected!r})"
            for m in self.mismatches
        ]
        return (
            f"INVARIANT_7_VIOLATION: customer_slug={self.customer_slug!r} "
            f"storage bindings escape customer namespace: {'; '.join(parts)}"
        )


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------


_VALID_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _is_valid_slug(slug: str) -> bool:
    """Mirror the ``customer.yaml`` validator's slug rule. Lowercase
    letters, digits, hyphens. No leading/trailing hyphen. 2-32 chars.

    A malformed slug at boot is itself an invariant failure - the
    expected-name derivation would otherwise produce nonsense.
    """
    if not slug or len(slug) < 2 or len(slug) > 32:
        return False
    if slug.startswith("-") or slug.endswith("-"):
        return False
    return all(c in _VALID_SLUG_CHARS for c in slug)


def _foreign_slug_in_skill_bucket(bucket: str, own_slug: str) -> Optional[str]:
    """If ``bucket`` is a per-customer skill bucket for a DIFFERENT slug,
    return that foreign slug; else ``None``.

    A bucket named ``ss-operator-<other>-skills`` points at another
    tenant's skill bodies - the explicit cross-Machine leak.
    """
    if not bucket.startswith(_SKILL_BUCKET_PREFIX):
        return None
    if not bucket.endswith(_SKILL_BUCKET_SUFFIX):
        return None
    inner = bucket[len(_SKILL_BUCKET_PREFIX) : -len(_SKILL_BUCKET_SUFFIX)]
    if inner and inner != own_slug:
        return inner
    return None


def _path_under_volume(observed_path: str) -> bool:
    """True iff ``observed_path`` resolves inside the per-Machine volume
    root (``/opt/data``), after normalizing ``.``/``..`` segments.

    A path that escapes the volume (absolute elsewhere, or a ``..``
    traversal back out) is outside the customer boundary.
    """
    if not observed_path:
        return False
    # Normalize without touching the filesystem; reject relative paths
    # (the env always carries absolute volume paths).
    if not observed_path.startswith("/"):
        return False
    normalized = posixpath.normpath(observed_path)
    root = posixpath.normpath(_VOLUME_ROOT)
    return normalized == root or normalized.startswith(root + "/")


def _path_names_foreign_slug(observed_path: str, own_slug: str) -> Optional[str]:
    """If a path segment is a *different* valid slug, return it; else None.

    Catches ``/opt/data/<other-slug>/audit.db`` style misprovisioning
    where the path stayed on the volume but embedded another tenant's
    slug. Conservative: only flags a segment that is itself a well-formed
    slug AND differs from ours AND is not a known generic dir name.
    """
    _GENERIC_SEGMENTS = {
        "opt",
        "data",
        "audit",
        "agent-state.db",
        "audit.db",
        "honcho",
        "voice",
        "oauth",
        "profiles",
        "observations.db",
        "customer.yaml",
        "run",
        "samples-cache",
        "pg",
        "redis",
    }
    for seg in observed_path.strip("/").split("/"):
        if seg in _GENERIC_SEGMENTS:
            continue
        if seg == own_slug:
            continue
        if _is_valid_slug(seg) and seg != own_slug:
            return seg
    return None


# ---------------------------------------------------------------------------
# Core check (pure)
# ---------------------------------------------------------------------------


def verify_storage_bindings(snapshot: BindingSnapshot) -> Invariant7Violation:
    """Compare observed storage bindings against the per-slug expectation.

    Pure function: no env reads, no I/O. The Machine's ``CUSTOMER_SLUG``
    is carried inside ``snapshot.customer_slug`` so the result is fully
    determined by its argument.

    Returns
    -------
    Invariant7Violation
        ``violation.passed`` is True iff every binding resolves to this
        customer's namespace. A False result means the runtime MUST exit
        non-zero before serving any request.
    """
    slug = snapshot.customer_slug
    mismatches: list[BindingMismatch] = []

    # (0) Slug validity. A malformed slug poisons every derivation; report
    #     it against the skill bucket (the one binding we derive a name for)
    #     and stop - downstream checks would only produce nonsense.
    if not _is_valid_slug(slug):
        return Invariant7Violation(
            customer_slug=slug,
            mismatches=(
                BindingMismatch(
                    kind=BindingKind.SKILL_BODIES_BUCKET,
                    expected=f"{_SKILL_BUCKET_PREFIX}<valid-slug>{_SKILL_BUCKET_SUFFIX}",
                    observed=snapshot.skill_bodies_bucket,
                    reason=(
                        f"customer_slug={slug!r} is not a valid slug; expected "
                        "lowercase letters, digits, hyphens (2-32 chars, no "
                        "leading or trailing hyphen) - cannot derive per-customer "
                        "binding names"
                    ),
                ),
            ),
        )

    # (1) Overlay slug must agree with the runtime slug. Disagreement means
    #     the SMD plugins namespace to a different customer than Hermes.
    if snapshot.overlay_slug != slug:
        mismatches.append(
            BindingMismatch(
                kind=BindingKind.SKILL_BODIES_BUCKET,
                expected=slug,
                observed=snapshot.overlay_slug,
                reason=(
                    "SMD_CUSTOMER_SLUG (overlay namespace) disagrees with "
                    "CUSTOMER_SLUG (runtime namespace); the overlay plugins "
                    "would read/write another customer's namespace - "
                    "cross-Machine isolation failure mode"
                ),
            )
        )

    # (2) Per-customer skill-bodies bucket MUST equal ss-operator-<slug>-skills.
    expected_skill = _expected_skill_bucket(slug)
    observed_skill = snapshot.skill_bodies_bucket
    if not observed_skill:
        mismatches.append(
            BindingMismatch(
                kind=BindingKind.SKILL_BODIES_BUCKET,
                expected=expected_skill,
                observed="",
                reason="R2_SKILL_BODIES_BUCKET is empty / unbound",
            )
        )
    elif observed_skill != expected_skill:
        foreign = _foreign_slug_in_skill_bucket(observed_skill, slug)
        if foreign is not None:
            reason = (
                f"R2_SKILL_BODIES_BUCKET points at another customer's "
                f"skill-bodies bucket (foreign slug {foreign!r}) - this is "
                "the cross-Machine isolation failure mode"
            )
        else:
            reason = (
                "R2_SKILL_BODIES_BUCKET does not match the per-customer name "
                f"{expected_skill!r} (malformed or wrong bucket)"
            )
        mismatches.append(
            BindingMismatch(
                kind=BindingKind.SKILL_BODIES_BUCKET,
                expected=expected_skill,
                observed=observed_skill,
                reason=reason,
            )
        )

    # (3) Shared config bucket. Isolation here is in the key path, not the
    #     bucket name, so we do NOT require the slug. We DO refuse if it was
    #     accidentally pointed at some customer's per-customer skill bucket.
    observed_config = snapshot.config_bucket
    if not observed_config:
        mismatches.append(
            BindingMismatch(
                kind=BindingKind.CONFIG_BUCKET,
                expected=_DEFAULT_CONFIG_BUCKET,
                observed="",
                reason="R2_BUCKET_CONFIG is empty / unbound",
            )
        )
    else:
        foreign_cfg = _foreign_slug_in_skill_bucket(observed_config, slug)
        if foreign_cfg is not None or observed_config == expected_skill:
            mismatches.append(
                BindingMismatch(
                    kind=BindingKind.CONFIG_BUCKET,
                    expected=f"shared config bucket (e.g. {_DEFAULT_CONFIG_BUCKET!r})",
                    observed=observed_config,
                    reason=(
                        "R2_BUCKET_CONFIG is pointed at a per-customer "
                        "skill-bodies bucket; the shared config bucket must "
                        "not name a per-customer namespace - cross-Machine "
                        "isolation failure mode"
                    ),
                )
            )

    # (4) SQLite paths MUST resolve under the per-Machine volume and must
    #     not embed a foreign customer slug.
    for kind, observed_path in (
        (BindingKind.AUDIT_DB, snapshot.audit_db_path),
        (BindingKind.AGENT_STATE_DB, snapshot.agent_state_db_path),
    ):
        env_name = _ENV_VAR_FOR_KIND[kind]
        if not observed_path:
            mismatches.append(
                BindingMismatch(
                    kind=kind,
                    expected=f"a path under {_VOLUME_ROOT}/",
                    observed="",
                    reason=f"{env_name} is empty / unbound",
                )
            )
            continue
        if not _path_under_volume(observed_path):
            mismatches.append(
                BindingMismatch(
                    kind=kind,
                    expected=f"a path under {_VOLUME_ROOT}/",
                    observed=observed_path,
                    reason=(
                        f"{env_name} resolves outside the per-Machine volume "
                        f"root {_VOLUME_ROOT!r}; the mounted volume is the "
                        "customer boundary (ADR 0007) - a binding outside it "
                        "is the cross-Machine isolation failure mode"
                    ),
                )
            )
            continue
        foreign_seg = _path_names_foreign_slug(observed_path, slug)
        if foreign_seg is not None:
            mismatches.append(
                BindingMismatch(
                    kind=kind,
                    expected=f"a path under {_VOLUME_ROOT}/ for slug {slug!r}",
                    observed=observed_path,
                    reason=(
                        f"{env_name} embeds another customer's slug "
                        f"{foreign_seg!r} - cross-Machine isolation failure mode"
                    ),
                )
            )

    return Invariant7Violation(customer_slug=slug, mismatches=tuple(mismatches))


# ---------------------------------------------------------------------------
# Env collection (boot)
# ---------------------------------------------------------------------------


def collect_snapshot_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> BindingSnapshot:
    """Read the real Phase-1 storage binding env into a snapshot.

    ``env`` defaults to ``os.environ``; injectable for tests. Missing
    vars become empty strings, which :func:`verify_storage_bindings`
    treats as unbound violations.
    """
    e = os.environ if env is None else env
    return BindingSnapshot(
        customer_slug=e.get("CUSTOMER_SLUG", "") or "",
        overlay_slug=e.get("SMD_CUSTOMER_SLUG", "") or e.get("CUSTOMER_SLUG", "") or "",
        skill_bodies_bucket=e.get("R2_SKILL_BODIES_BUCKET", "") or "",
        config_bucket=e.get("R2_BUCKET_CONFIG", "") or "",
        audit_db_path=e.get("SMD_D1_AUDIT_BINDING", "") or "",
        agent_state_db_path=e.get("SMD_D1_AGENT_STATE_BINDING", "") or "",
    )


# ---------------------------------------------------------------------------
# Audit emission (pytest-free; stdlib only)
# ---------------------------------------------------------------------------


def _emit_boot_failure_audit(
    violation: Invariant7Violation,
    *,
    broker_socket: Optional[str],
) -> bool:
    """Best-effort: write one INVARIANT_BOOT_CHECK_FAILED row through the
    Workspace broker's append-only audit ledger (OP-P1-4).

    Returns True on a confirmed append, False otherwise. A False return
    NEVER weakens the boot refusal - the caller still exits non-zero.
    Uses only the stdlib (``socket``/``json``) so this path imports and
    runs in the customer Machine venv, which has no pytest.
    """
    if not broker_socket:
        log.warning(
            "invariant_7: no SMD_AUDIT_BROKER_SOCKET; cannot emit "
            "INVARIANT_BOOT_CHECK_FAILED row (boot still refused)"
        )
        return False

    row = {
        "action_type": "INVARIANT_BOOT_CHECK_FAILED",
        "actor": "agent",
        "actor_role": "agent",
        "metadata": json.dumps(violation.to_audit_metadata(), sort_keys=True),
    }
    request = json.dumps({"action": "audit_append", "row": row}).encode("utf-8")

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(broker_socket)
            client.sendall(request + b"\n")
            raw = client.makefile("rb").readline()
        response = json.loads(raw)
    except (OSError, ValueError) as exc:
        log.warning(
            "invariant_7: failed to emit INVARIANT_BOOT_CHECK_FAILED via "
            "broker %s: %s (boot still refused)",
            broker_socket,
            exc,
        )
        return False

    if response.get("ok") is True:
        return True
    log.warning(
        "invariant_7: broker rejected INVARIANT_BOOT_CHECK_FAILED append: %s "
        "(boot still refused)",
        response,
    )
    return False


# ---------------------------------------------------------------------------
# Boot entry (pytest-free)
# ---------------------------------------------------------------------------


def verify_at_boot(env: Optional[Mapping[str, str]] = None) -> int:
    """Boot-time entry. Read the real binding env, verify, and on a
    violation emit ``INVARIANT_BOOT_CHECK_FAILED`` and return non-zero.

    Returns
    -------
    int
        ``0`` when every storage binding resolves to this customer's
        namespace. ``3`` on any violation (the caller, entrypoint.sh /
        bootstrap.sh, ``sys.exit(3)``s on a non-zero return). The exit
        code is the load-bearing refusal; audit emission is best-effort
        and never changes the returned code.

    Importable without pytest - this is the substrate boot path.
    """
    e = os.environ if env is None else env
    snapshot = collect_snapshot_from_env(e)
    violation = verify_storage_bindings(snapshot)

    if violation.passed:
        log.info(
            "invariant_7: storage bindings verified for customer_slug=%r",
            snapshot.customer_slug,
        )
        return 0

    # Surface on stderr regardless of audit-emit outcome.
    print(violation.refusal_message(), file=sys.stderr)
    broker_socket = e.get("SMD_AUDIT_BROKER_SOCKET") or None
    _emit_boot_failure_audit(violation, broker_socket=broker_socket)
    return 3


# ---------------------------------------------------------------------------
# Substrate-runner entrypoint (run_invariants.py compatibility)
# ---------------------------------------------------------------------------


def _self_check_fixtures() -> tuple[bool, str]:
    """Boot-time smoke fixtures. Comprehensive coverage lives in
    ``tests/test_invariant_7.py``.

    Two cases, both against the real Phase-1 model:
      1. A snapshot whose every binding resolves to the slug -> passes.
      2. A snapshot whose skill-bodies bucket points at ANOTHER
         customer's bucket -> fails with the cross-Machine reason text.
    """
    slug = "smoke"

    ok_snapshot = BindingSnapshot(
        customer_slug=slug,
        overlay_slug=slug,
        skill_bodies_bucket=_expected_skill_bucket(slug),
        config_bucket=_DEFAULT_CONFIG_BUCKET,
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    ok_result = verify_storage_bindings(ok_snapshot)
    if not ok_result.passed:
        return (
            False,
            f"FAIL: passing snapshot reported mismatches: {ok_result.refusal_message()}",
        )

    bad_snapshot = BindingSnapshot(
        customer_slug=slug,
        overlay_slug=slug,
        skill_bodies_bucket="ss-operator-other-skills",  # foreign tenant
        config_bucket=_DEFAULT_CONFIG_BUCKET,
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    bad_result = verify_storage_bindings(bad_snapshot)
    if bad_result.passed:
        return (
            False,
            "FAIL: foreign-tenant skill-bodies bucket should have triggered a mismatch",
        )
    if "cross-Machine isolation failure mode" not in bad_result.mismatches[0].reason:
        return (
            False,
            "FAIL: mismatch reason did not name the cross-Machine failure mode: "
            f"{bad_result.mismatches[0].reason!r}",
        )

    return (
        True,
        "PASS: invariant 7 detects cross-Machine binding mismatch "
        "(2 of 2 self-check fixtures held)",
    )


def run() -> tuple[bool, str]:
    """Substrate-runner shape - boot-time smoke check.

    Returns ``(ok, message)``. ``ok=True`` iff the module's enforcement
    loop produces the expected behavior on the bundled self-check
    fixtures. Detailed coverage lives in ``tests/test_invariant_7.py``.

    NOTE: ``run()`` is the fixture smoke check the substrate runner
    invokes. The LIVE boot check is :func:`verify_at_boot`, called
    separately from entrypoint.sh / bootstrap.sh against the real env.
    """
    try:
        return _self_check_fixtures()
    except Exception as e:  # noqa: BLE001 - boot self-check: any raise is a FAIL line for the substrate runner, never a crash at boot
        return (False, f"FAIL: invariant 7 self-check raised {type(e).__name__}: {e}")


__all__ = [
    "BindingKind",
    "BindingMismatch",
    "BindingSnapshot",
    "Invariant7Violation",
    "collect_snapshot_from_env",
    "run",
    "verify_at_boot",
    "verify_storage_bindings",
]


if __name__ == "__main__":
    # Direct invocation runs the LIVE boot check against the process env,
    # so `python3 invariant_7.py` is a usable entrypoint shim. The
    # substrate runner uses run() instead.
    sys.exit(0 if verify_at_boot() == 0 else 3)
