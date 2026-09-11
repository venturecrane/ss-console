# Audit log immutability

> **Status — SUPERSEDED (2026-08-20, ss#2500). Do not build from this
> document.** It specifies a D1-era design: a `D1Executor` wrapper rejecting
> UPDATE/DELETE, a Cloudflare Logpush mirror of D1 query logs into an
> Object-Locked R2 bucket, and a `check_audit_integrity` comparator between
> the two. None of its three layers describes the running system, and the
> substrate each one assumed is gone.
>
> **What actually holds the requirement now**, layer by layer:
>
> | This spec's layer                                          | What ships instead                                                                                                                                                                                                                                                                                                                  |
> | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker-layer UPDATE/DELETE rejection against D1         | The ledger is not in D1. It is Machine-local sqlite owned by the capability broker, the single process holding the RW handle; the agent uid has read-only access and the IPC surface exposes no update or delete verb (`operator/workspace_broker/audit_ledger.py`, `server.py`).                                                   |
> | 2. Logpush mirror into an Object-Locked bucket             | `.github/workflows/audit-chain-verify.yml` pulls each seat's full export over the ADR-0043 runtime-read seam daily and writes it to R2 at `audit/<slug>/<date>/<HHMMSS>Z-<head12>.json.gz`, one object per run. Logpush mirrors D1 query logs and there are no D1 queries to mirror.                                                |
> | 3. `check_audit_integrity` comparing D1 against the mirror | `operator/workspace_broker/chain.py` (`verify_chain`) plus `operator/bin/lib/chain_pin.py` (the descends check against a head pinned off the Machine, `audit_head_history`, migration 0108). There is no second store to diff against, so the tamper evidence is the chain itself plus an external pin, not a two-store comparison. |
>
> The **Captain exception process** below (court-ordered redaction: counsel
> review, multi-confirmation script, exceptions ledger, disclosure in the next
> evidence packet) was never implemented and is not superseded by anything. It
> stands as unbuilt design intent. Nothing in the current system provides a
> sanctioned redaction path, which is the honest state: today a court-ordered
> redaction would be performed by root on the Machine and would show up as a
> chain break in the daily verifier.
>
> Kept rather than deleted because the failure-mode table and the exception
> process are the parts worth reading, and because a reader who arrives here
> from an old link needs to be told where the live design went rather than
> finding nothing. Everything below this box is historical.

**Spec for issue #892.** Worker-layer enforcement that rejects UPDATE/DELETE
against the per-customer `audit_log` table, plus an immutable Logpush
mirror, a periodic integrity check, and the Captain-supervised
exception process for court-ordered redactions.

The audit log is the substrate's compliance anchor. Every safety-relevant
action writes a row before its effect lands (see `audit_log.py` design
notes and [d1-schema.md](d1-schema.md) §1). Without immutability the
log's evidentiary value is zero.

## Source

- platform-prd.md §10.1 — "immutable rows" promise
- [d1-schema.md](d1-schema.md) §Failure modes — "Audit-log delete attempt"
- [index.md](index.md) open ambiguity #5 — D1 lacks per-role permissions
- [compliance-evidence-packet.md](compliance-evidence-packet.md) — the
  consumer that the immutability promise underwrites

## Why this exists

Cloudflare D1 does not ship per-role table permissions. A Postgres
deployment would grant the agent-runtime user `INSERT` on `audit_log`
and nothing else, with `DELETE` reserved for a Captain-only role. On
D1 the binding is binary — the Worker has full read/write on the bound
database. Application-layer enforcement is the only available knob.

Three layers carry the immutability promise:

1. **Worker layer (this spec).** A `D1Executor` wrapper rejects
   UPDATE/DELETE/REPLACE/TRUNCATE/DROP/ALTER against `audit_log`. The
   audit-log writer (`AuditLogWriter` in PR #942) is the only path that
   may INSERT; all other callers hold the wrapper.
2. **Logpush mirror.** Every D1 query is streamed to an R2 archive
   bucket with bucket-level Object Lock. Even a compromised Worker
   cannot delete a row from an Object-Lock bucket inside the retention
   window. The mirror is the compliance-grade copy.
3. **Periodic integrity check.** A Cron Trigger Worker runs the
   `check_audit_integrity` library against D1 and the mirror and
   reports drift. Drift is investigated against the exceptions ledger.

If any one layer is bypassed, the next layer catches it. A compromised
Worker that bypasses the executor wrapper still leaves a trail in the
Logpush mirror. A compromised Worker that deletes from D1 leaves a
mirror row with no D1 row — surfaced as `IN_MIRROR_NOT_IN_D1`.

## Layer 1: Worker-layer enforcement

Module: `operator/adapter/audit_log_immutability.py`

### Grammar

The wrapper accepts exactly two statement shapes against `audit_log`:

- `INSERT INTO audit_log ... VALUES ...` — the writer path
- `SELECT ... FROM audit_log ...` — reads

Everything else against `audit_log` raises `AuditLogImmutabilityError`.
Statements that don't touch `audit_log` at all are passed through
unchanged — the wrapper does not care about other tables.

### What the inspector checks

1. Strip SQL comments (`/* ... */` and `-- ...`) so they cannot hide
   the table name from the keyword scan.
2. If the comment-stripped SQL contains the token `audit_log`
   (case-insensitive, word-bounded), proceed; otherwise pass.
3. If the SQL contains an embedded semicolon (multi-statement),
   reject wholesale. D1's HTTP API accepts only single-statement
   parameterized queries by convention.
4. If the leading keyword is one of
   `UPDATE / DELETE / REPLACE / TRUNCATE / DROP / ALTER`, reject.
5. Otherwise pass.

The inspection is conservative. A statement the parser cannot
classify with confidence is rejected — a false-positive blocks one
query and is loud; a false-negative silently violates the safety floor.

### Wiring

The audit-log writer (`AuditLogWriter`) constructs against the raw
executor — this is by design. The writer is the only path that may
INSERT into `audit_log`. Every other caller in the substrate wraps the
raw executor in `D1Executor` before holding it.

```python
from adapter.audit_log_immutability import D1Executor, AuditLogImmutabilityError

raw = HttpD1Executor(...)
safe = D1Executor(raw)

# Reads: allowed
await safe.execute("SELECT * FROM audit_log WHERE id = ?", [ulid])

# Mutations: blocked
try:
    await safe.execute("DELETE FROM audit_log WHERE id = ?", [ulid])
except AuditLogImmutabilityError:
    # caller MUST NOT swallow this; substrate alarm
    raise
```

The writer's wiring is unchanged from PR #942 — `audit_log.py` keeps
using the unrestricted executor.

## Layer 2: Logpush mirror

Logpush is a Cloudflare-platform feature that ships D1 query logs (and
many other streams) to a sink the operator configures. For the
per-customer audit log we ship every INSERT into an R2 bucket
(`smd-audit-archive-{slug}/`) configured with Object Lock in Compliance
Mode.

Object Lock means even Cloudflare account admins cannot delete or
modify the object within the retention window. The retention window is
per-vertical:

- law: 7 years
- default: 3 years
- (operator may extend; never reduce)

The wrangler.toml config block (additive in this PR) declares the
audit-archive R2 binding so the Hermes-side deployment can attach the
Logpush job. The Worker-code implementation that talks to R2 is
deferred — v1 ships:

- `LogpushMirror` Protocol (the contract every implementation must
  satisfy)
- `NoopLogpushMirror` (default; logs the row id and returns)
- `MirroredAuditRow` (the row shape exchanged between writer and
  mirror, 1:1 with the audit_log column set)

When the real implementation lands (Hermes-side deployment work, not
this issue), it slots into the protocol without changes to the writer
or the wrapper.

### Mirror failure handling

The mirror MUST NOT raise on transient failure. The contract: log the
failure, return. The audit-log INSERT into D1 already landed; the
mirror is the _backup_ record. Drift between D1 and the mirror surfaces
in the integrity check (Layer 3) at the next run.

This is deliberate. Coupling the mirror's reliability to the D1 write
would mean a Logpush outage takes down the agent runtime. The substrate
invariant is "every action that touches state has an audit row" — the
D1 row satisfies that. The mirror raises the bar from "auditable" to
"tamper-resistant under a compromised Worker"; failing to mirror does
not erase the D1 row.

## Layer 3: Periodic integrity check

Module: `operator/adapter/audit_log_integrity.py`

### Contract

`check_audit_integrity(d1_loader, logpush_archive_loader, *, start_ts, end_ts)`
returns an `IntegrityReport`. Three finding kinds:

| Kind                  | Meaning                                                         | Common cause                                                                |
| --------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `IN_D1_NOT_IN_MIRROR` | D1 has a row the mirror does not (outside the lag-grace window) | Mirror drop, or Worker INSERT outside the writer path                       |
| `IN_MIRROR_NOT_IN_D1` | Mirror has a row D1 does not                                    | D1 DELETE (substrate violation), or Captain legal-hold redaction (ledgered) |
| `DIGEST_MISMATCH`     | Both stores have the id but a load-bearing column differs       | Forged-then-overwritten D1 row, or mirror corruption                        |

`metadata` is excluded from the column comparison (see the
DIGEST_MISMATCH docstring) because non-writer callers may not preserve
the writer's deterministic JSON serialization.

### Lag grace

Logpush batches. The integrity check applies a 5-minute grace to
`IN_D1_NOT_IN_MIRROR` findings — a row whose `ts` is within 5 minutes
of `now` is not surfaced even if the mirror hasn't seen it yet. The
check is intended to run hourly; the grace prevents per-run false
positives without hiding real drift.

### Cadence

Recommended: hourly. The check is a library — the scheduler is a
deployment concern. A Cloudflare Cron Trigger Worker is the natural
shape:

```toml
# Hermes-side wrangler.toml (deferred)
[triggers]
crons = ["0 * * * *"]
```

The Worker holds both loaders, calls `check_audit_integrity`, and
posts the report into the compliance-evidence stream when `clean=False`.

## Captain exception process

The only legitimate path that modifies an existing `audit_log` row is
a court-ordered redaction. The procedure:

### Step 1: Court order received

The Captain receives a sealed court order (subpoena, GDPR Article 17
"right to erasure" demand, etc.) requiring redaction of specific
audit-log content. The order is filed in the Captain's legal hold
intake.

### Step 2: Counsel review

The Captain consults outside counsel. Counsel determines:

- Whether the order applies to the audit log (most orders are about
  case files, not operational logs)
- Which rows are in scope
- What level of redaction is required (column-level zero-fill, row
  deletion, or full-table redaction)
- Whether the customer must be notified

Counsel's determination is filed in the legal hold record before any
substrate action is taken.

### Step 3: Bin script with multi-confirmation

`bin/audit-redact.sh` (out of scope this PR; tracked separately) runs
the redaction with multi-confirmation:

- Operator types the exceptions-ledger ticket id (assigned by counsel)
- Operator types `REDACT-IRREVERSIBLE` to confirm intent
- Operator confirms the row ids being redacted (echoed back, requires
  re-type)
- Operator types `EXECUTE` to commit

The script writes the exceptions-ledger row first, then constructs a
`LegalHoldException(ticket)` and calls `D1Executor.execute(...,
legal_hold_ticket=ticket)`. The wrapper recognizes the ticket and
allows the bypass.

The script is the ONLY path that may pass `legal_hold_ticket`. The
ticket is meaningless outside the script — the wrapper's contract is
that a non-empty ticket plus a successful exceptions-ledger write
together unlock the bypass.

### Step 4: Exceptions ledger

The exceptions ledger is a separate immutable record on the
Captain-side control plane. Each entry carries:

- Ticket id (matches `legal_hold_ticket`)
- Court order reference + counsel determination
- Row ids redacted
- Redaction level (zero-fill / delete)
- Operator id + timestamp
- Hash of the order document
- Customer notification record (sent / not-required + reason)

The ledger is queried by the integrity check operator when an
`IN_MIRROR_NOT_IN_D1` finding surfaces — every such finding either
matches a ledger entry (legitimate redaction) or is escalated as a
substrate violation.

### Step 5: Compliance packet

The next compliance-evidence packet generated for the affected
customer includes the redaction in the audit-log section, marked with
the exceptions-ledger ticket reference. The packet's CSV export
includes the redaction event; the narrated section
(`04-audit-log-human.md` per
[compliance-evidence-packet.md](compliance-evidence-packet.md)) notes
the redaction with a one-line explanation Captain hand-edits before
delivery.

## Failure modes

- **Compromised Worker bypasses the executor wrapper.** The Logpush
  mirror catches it: the INSERT lands in the R2 archive even if it
  also lands in D1 outside the writer path. The integrity check
  surfaces it as `IN_D1_NOT_IN_MIRROR` if the mirror saw it through
  Logpush, or as `IN_MIRROR_NOT_IN_D1` if the mirror saw the legit row
  and a later D1 mutation deleted it.
- **Compromised Worker deletes from D1.** The mirror retains the row.
  The integrity check surfaces `IN_MIRROR_NOT_IN_D1`. The operator
  cross-checks against the exceptions ledger; no matching entry means
  a substrate violation — escalate per the invariants runbook.
- **Logpush job drops a row.** Surfaces as `IN_D1_NOT_IN_MIRROR` at the
  next integrity check. The operator backfills from D1 into the
  archive (write-once, so backfill is allowed) or investigates if the
  drop is systemic.
- **Object Lock bucket is misconfigured.** Tested at provision time
  (the smoke test in `bin/provision-customer.sh` writes a sentinel
  object and attempts to delete it; non-failure of the delete is a
  provisioning failure).
- **Captain redaction script invoked without a ticket.** The wrapper
  rejects — `legal_hold_ticket=""` is treated as absent.
- **Integrity-check Worker holds the full window in memory.** The
  default window (one hour) is bounded; per-customer audit volume is
  tens of thousands of rows per month. If volume ever exceeds the
  Worker's budget, swap the comparator for a sort-merge over the two
  ULID-ordered iterators.

## Verification

Tests:

- `operator/adapter/tests/test_audit_log_immutability.py` — wrapper,
  inspector, legal-hold bypass, mirror protocol stub
- `operator/adapter/tests/test_audit_log_integrity.py` — comparator
  across clean, only-in-D1, only-in-mirror, mismatch, multi-finding,
  loader-failure cases

Run locally:

```
cd operator && uv run --with pytest python -m pytest adapter/tests/test_audit_log_immutability.py adapter/tests/test_audit_log_integrity.py -v
```

## Cross-references

- [d1-schema.md](d1-schema.md) — audit_log column shape and failure
  modes
- [compliance-evidence-packet.md](compliance-evidence-packet.md) —
  consumer of the immutability promise
- [safety-invariants.md](safety-invariants.md) — substrate invariants
  including the audit-row-before-action rule
- PR #942 — `AuditLogWriter` (the only legitimate INSERT path)
