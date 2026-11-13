"""Env-bound D1 client construction with optional namespace assertion.

`audit_log.writer_from_env()` returns the raw audit-log writer — that
path is the *one* sanctioned INSERT into `audit_log` and stays
unwrapped by design (see the immutability rationale in
`hermes-smd-overlay/plugins/hermes-smd-audit/immutability.py`, where the
enforcement was ported; #1130).
Every OTHER caller that wants per-customer D1 access at the Machine
boot level should call `namespaced_executor_from_env(customer_slug)`
below; it returns a `NamespacedD1Executor` bound to the slug, with the
audit writer injected so refusals land an `INVARIANT_VIOLATION` row.

The wrapper is a defense-in-depth no-op against today's writer-only SQL
(no slug tokens appear there). It locks the contract so the moment a
caller adds slug-mentioning SQL (e.g. a maintenance script or a
cross-table report that names a per-customer Vectorize index), the
wrapper catches a foreign-slug name before the query reaches D1.

Filed as the recommended migration entry point against
[#1009](https://github.com/venturecrane/ss-console/issues/1009)
(fork-side adoption tracker; do not implement there).
"""

from __future__ import annotations

import os
from typing import Optional

from .audit_log import AuditLogWriter, HttpD1Executor, writer_from_env
from .namespace_assertion import NamespacedD1Executor
from . import seat_config


def namespaced_executor_from_env(
    customer_slug: Optional[str] = None,
    *,
    audit_writer: Optional[AuditLogWriter] = None,
) -> NamespacedD1Executor:
    """Return a slug-bound `NamespacedD1Executor` wired to env-bound D1.

    `customer_slug` defaults to the `CUSTOMER_SLUG` env var (which
    `templates/bootstrap.sh` step 1 already requires non-empty). Passing
    the slug explicitly is supported for call sites that need to bind a
    different slug than the Machine's primary tenant (none today; the
    knob exists for future operator paths).

    `audit_writer` defaults to a fresh `writer_from_env()` so the
    wrapper's refusal-audit emission has somewhere to land. Production
    callers that already hold a writer should pass it in to avoid
    creating a second HTTP executor.

    Raises `RuntimeError` if either the D1 env vars or the customer
    slug is missing — that is a bootstrap-time invariant failure and
    should abort container start.
    """
    slug = customer_slug if customer_slug is not None else seat_config.slug(on_missing="empty")
    if not slug:
        raise RuntimeError(
            "d1_env.namespaced_executor_from_env: CUSTOMER_SLUG env var unset "
            "(and no explicit slug passed); templates/bootstrap.sh step 1 "
            "must set this from the per-customer Machine binding"
        )

    raw = HttpD1Executor(
        account_id=_require_env("CF_ACCOUNT_ID"),
        database_id=_require_env("AIE_D1_DATABASE_ID"),
        api_token=_require_env("CF_API_TOKEN"),
    )
    writer = audit_writer if audit_writer is not None else writer_from_env()
    return NamespacedD1Executor(
        expected_slug=slug,
        inner=raw,
        audit_writer=writer,
    )


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            f"d1_env.namespaced_executor_from_env: required env var {key!r} unset; "
            "bootstrap.sh must set this from the per-customer secret bundle"
        )
    return value


__all__ = ["namespaced_executor_from_env"]
