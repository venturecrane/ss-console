"""SMD Operator — control-plane Python helpers.

Per the 2026-05-24 architectural realignment (ADRs 0015, 0016, 0017
rewrites), the runtime Hermes integration moved to the separate plugin
overlay at `venturecrane/hermes-smd-overlay`. The plugins there
(`hermes-smd-audit`, `hermes-smd-trust`, `hermes-smd-hook-probe`, and
the rest) own everything that runs inside the customer Machine:
trust-ceiling enforcement, audit emission and hook probing.

What survives in this package is the control-plane substrate —
modules used by operator-side tools (`bin/lib/decommission.py`,
`bin/lib/evidence.py`) and by the safety_substrate invariant tests.
(The ADR-0008 memory/voice ingestion + retention plane — `memory/`,
`voice/pipeline.py`, `bin/cron-retention.py`, `bin/lib/export.py` —
was removed by #1355: it read a per-customer control-plane Cloudflare
D1 that was never provisioned and that the live Machine-local runtime
never wrote. Preservation now pulls the live stores through the
ADR-0043 runtime-read seam; see `bin/lib/seam_pull.py`.) Specifically:

* Per-customer namespace isolation primitives (`namespace_assertion.py`,
  `d1_env.py`) — used by control-plane D1 access and the `invariant_7`
  substrate fixture. The overlay's `hermes-smd-trust` plugin has its
  own runtime copy.
* The audit-log D1 client (`audit_log.py`) — read/write surface used by
  decommission and evidence-packet generation, both of which run
  outside the customer Machine after pause.
* The evidence-packet generator (`evidence/`). (The sample-transform
  voice toolchain that lived in `voice/` was removed with ss ADR 0083
  decision 6, 2026-11-07.)
* Cost telemetry (`cost_ingest.py`, `cost_rollup.py`,
  `cost_telemetry/`) — billing rollup against per-customer Machines.
* (The trust-ceiling rule is not here. Its copy, `trust_ceiling.py`, ran
  on no seat and was deleted 2026-11-12; the one rule is the overlay's
  `hermes-smd-trust/enforce.py`, which the boot invariants load.)
* Customer.yaml schema validator (`validate_customer_yaml.py`) — see
  the in-flight follow-up that replaces this with the overlay's
  `bootstrap/validate.py` per ADR 0019.

Public surface (TOCTOU hardening, #861 follow-on)
-------------------------------------------------

`from operator.adapter import *` produces exactly the names below:

* `NamespaceAssertionError` — the structured refusal raised on
  cross-customer access attempts.
* `NamespacedD1Executor`, `NamespacedR2Client`,
  `NamespacedVectorizeClient` — the three slug-bound wrappers.
* `namespaced_executor_from_env` — the env-bound D1 entry point.
"""

from .d1_env import namespaced_executor_from_env
from .namespace_assertion import (
    NamespaceAssertionError,
    NamespacedD1Executor,
    NamespacedR2Client,
    NamespacedVectorizeClient,
)

__version__ = "0.1.0"

__all__ = [
    "NamespaceAssertionError",
    "NamespacedD1Executor",
    "NamespacedR2Client",
    "NamespacedVectorizeClient",
    "namespaced_executor_from_env",
]
