# Trust-ceiling decision logging

**Spec for issue #864.** Emission contract for trust-ceiling enforcement
decisions. Wraps `enforce()` with a synchronous audit_log write so the
substrate's allow / draft_for_review / refuse decisions are visible to
operators, customers, and the compliance evidence packet.

Before this issue, the trust ceiling was a code-path decision with no
visibility. The Captain dashboard had no way to aggregate enforcement
across customers; the customer dashboard had no recent-decisions feed;
the compliance-evidence packet (#802) could not show enforcement history.
This spec describes the audit-row contract that fixes that gap.

## Source

- platform-prd.md §11 (Trust Ceiling Model)
  - §11.1: the three ceilings (autonomous, draft_for_review, disabled)
  - §11.4: every action at every ceiling is logged
- platform-prd.md §7.5: safety substrate invariants (invariant #5: ceiling
  enforced in code, not in prompt)
- `operator/adapter/trust_ceiling.py`: the `enforce()` decision tree
  this module wraps
- `operator/adapter/audit_log.py` (PR #942): the writer + closed-set
  `ACCEPTED_ACTION_TYPES` vocabulary this module emits into
- d1-schema.md §1: `audit_log` table the emission lands in
- compliance-evidence-packet.md (#802): downstream consumer that
  aggregates the emitted rows

## Module + integration point

The emission module is `operator/safety_substrate/trust_ceiling_log.py`.
The public surface is one function:

```python
async def log_decision(
    writer: AuditLogWriter,
    *,
    customer: str,
    skill: str,
    action_class: ActionClassName | str,
    ceiling_level: CeilingLevel | str,
    decision: Decision,
    reason: DecisionReason,
    skill_version: Optional[str] = None,
    trace_id: Optional[str] = None,
    matter_ref: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> str
```

Returns the inserted ULID. Synchronous: returns only after the underlying
INSERT lands. On `AuditWriteError` the caller (dispatch path) MUST abort
the pending action, same invariant as the audit log writer itself.

### Where `log_decision()` plugs into the dispatch path

> **TARGET STATE — not yet wired.** The integration described here is the
> intended call-site contract, not a live control. `log_decision()` is
> defined and tested in this repo's safety_substrate, but no Hermes
> dispatch-path code calls it today, so `trust_ceiling_decision` audit rows
> do not yet emit in production. The Hermes dispatch integration is the
> adapter team's work and is tracked separately — see "Out of scope (filed
> elsewhere)" below ("Hermes dispatch integration"). The mapping table is
> the contract that work will implement against; do not read it as evidence
> the wiring exists.

`enforce()` in `operator/adapter/trust_ceiling.py` returns an
`EnforcementDecision(allowed, reason, audit_action)`. The dispatch path
(Hermes side, separate PR) is to call `enforce()` once per tool invocation
and then call `log_decision()` on the same code path with the result. The
mapping from `EnforcementDecision` to the wrapper's `(Decision,
DecisionReason)` pair is the integration point. The mapping table:

| Branch in `enforce()`                        | `Decision` | `DecisionReason`                       |
| -------------------------------------------- | ---------- | -------------------------------------- |
| `ceiling == REFUSED`                         | REFUSE     | CEILING_DISABLED                       |
| `action == READ`                             | ALLOW      | READ_ALLOWED                           |
| `COMMITMENT` + `DRAFT_FOR_REVIEW`            | DRAFT      | COMMITMENT_REQUIRES_AUTONOMOUS         |
| `COMMITMENT` + no approval                   | REFUSE     | COMMITMENT_NO_APPROVAL                 |
| `COMMITMENT` + approval                      | ALLOW      | COMMITMENT_WITH_APPROVAL               |
| `DESTRUCTIVE` + `DRAFT_FOR_REVIEW`           | REFUSE     | DESTRUCTIVE_DRAFT_CEILING              |
| `DESTRUCTIVE` + no approval                  | REFUSE     | DESTRUCTIVE_NO_APPROVAL                |
| `DESTRUCTIVE` + approval                     | ALLOW      | DESTRUCTIVE_WITH_APPROVAL              |
| `EXTERNAL_SEND` + `AUTONOMOUS` + approval    | ALLOW      | EXTERNAL_SEND_AUTONOMOUS_WITH_APPROVAL |
| `EXTERNAL_SEND` + `AUTONOMOUS` + no approval | REFUSE     | EXTERNAL_SEND_NO_APPROVAL              |
| `EXTERNAL_SEND` + `DRAFT_FOR_REVIEW`         | DRAFT      | EXTERNAL_SEND_DRAFT_ROUTE              |
| `INTERNAL_WRITE` + `AUTONOMOUS`              | ALLOW      | INTERNAL_WRITE_AUTONOMOUS              |
| `INTERNAL_WRITE` + `DRAFT_FOR_REVIEW`        | DRAFT      | INTERNAL_WRITE_DRAFT_ROUTE             |
| Unknown action class                         | REFUSE     | UNKNOWN_ACTION_CLASS                   |

The mapping table is the dispatch path's contract. Adding a branch in
`enforce()` means adding a `DecisionReason` member here and updating both
the module and this table in the same PR.

## Closed enums

The wrapper imposes closed enums to keep the dashboard aggregation queries
stable. Free-text reasons would degrade the aggregate view as new variants
appeared in production.

### `Decision`

Mirrors `EnforcementDecision.audit_action` from
`adapter/trust_ceiling.py`. Three members:

| Member   | Value                | Meaning                                    |
| -------- | -------------------- | ------------------------------------------ |
| `ALLOW`  | `"allow"`            | Action proceeds normally                   |
| `DRAFT`  | `"draft_for_review"` | Action routed to drafts for human approval |
| `REFUSE` | `"refuse"`           | Action blocked by the trust ceiling        |

### `CeilingLevel`

Mirrors `Ceiling` from `adapter/trust_ceiling.py` with one wrinkle: the
platform PRD §11.1 names the third value `disabled`; the adapter uses
`refused`. Both spellings are accepted; aggregations treat them as
equivalent.

| Member             | Value                |
| ------------------ | -------------------- |
| `AUTONOMOUS`       | `"autonomous"`       |
| `DRAFT_FOR_REVIEW` | `"draft_for_review"` |
| `DISABLED`         | `"disabled"`         |
| `REFUSED`          | `"refused"`          |

### `ActionClassName`

Mirrors `ActionClass` from `adapter/trust_ceiling.py`.

| Member           | Value              |
| ---------------- | ------------------ |
| `READ`           | `"read"`           |
| `INTERNAL_WRITE` | `"internal_write"` |
| `EXTERNAL_SEND`  | `"external_send"`  |
| `COMMITMENT`     | `"commitment"`     |
| `DESTRUCTIVE`    | `"destructive"`    |

### `DecisionReason`

Closed set of reasons; each maps to exactly one branch in the
`enforce()` decision tree. See the mapping table above for the
branch-to-reason crosswalk.

Members are grouped by outcome (ALLOW / DRAFT / REFUSE). Adding a member
requires updating the module, this spec, the wrapper tests, and the
dispatch-path mapping table.

## Audit row shape

The wrapper emits one `audit_log` row per `enforce()` invocation. Columns
populated (rest left NULL):

| Column          | Value                                                              |
| --------------- | ------------------------------------------------------------------ |
| `id`            | ULID (assigned by `AuditLogWriter`)                                |
| `ts`            | ISO 8601 UTC, ms precision (assigned by writer)                    |
| `action_type`   | `DRAFT_CREATED` (ALLOW or DRAFT) or `INVARIANT_VIOLATION` (REFUSE) |
| `actor`         | `"agent"`                                                          |
| `actor_role`    | `"agent"`                                                          |
| `skill_name`    | from `skill` argument                                              |
| `matter_ref`    | from `matter_ref` argument (optional)                              |
| `trust_ceiling` | from `ceiling_level` argument                                      |
| `metadata`      | JSON, see below                                                    |

### Why action_type reuses the closed-set vocabulary

`audit_log.py::ACCEPTED_ACTION_TYPES` is closed-set, on main from PR
#942. This PR's strict file scope (issue #864 brief) forbids modifying
it. The sticky-stop module (PR #948) made the same call:

> Action types reuse the existing closed-set vocabulary
> (ACCEPTED_ACTION_TYPES) so this module does not need to extend the
> audit-log contract.

This module follows the same convention. Mapping:

- `Decision.ALLOW` → `DRAFT_CREATED` (carries `metadata.decision="allow"`)
- `Decision.DRAFT` → `DRAFT_CREATED` (carries `metadata.decision="draft_for_review"`)
- `Decision.REFUSE` → `INVARIANT_VIOLATION` (the substrate enforced an invariant)

Downstream readers (dashboard, audit viewer, compliance packet) MUST
filter on `metadata.trust_ceiling_decision = true` to identify trust-
ceiling rows, NOT on `action_type` alone. This mirrors the sticky-stop
convention of `metadata.sticky_stop_transition`.

The trade-off: `action_type` alone does not identify a trust-ceiling
row. The upside: zero coupling between this module and the closed-set
vocabulary, and zero risk of dashboard miscount when other emitters use
the same action_type for unrelated reasons. If a future PR (with the
right scope) adds a dedicated `TRUST_CEILING_DECISION` action_type to
`ACCEPTED_ACTION_TYPES`, this module's mapping can flip to the new
type in one place; the metadata contract stays unchanged.

### `metadata` JSON shape

```json
{
  "trust_ceiling_decision": true,
  "customer": "acme",
  "skill": "inbox-triage",
  "action_class": "external_send",
  "ceiling_level": "draft_for_review",
  "decision": "draft_for_review",
  "reason": "external_send_draft_route",
  "skill_version": "2.1.0",
  "trace_id": "trace-01HXY..."
}
```

All nine keys are canonical. The dashboard aggregations key on these
field names; they do not change without a coordinated spec + dashboard
update. `skill_version` and `trace_id` are nullable; the rest are
populated on every emission.

`extra_metadata` may add fields BUT may NOT collide with canonical keys
(the wrapper raises `ValueError` on collision). Extra fields are
opaque to the dashboard aggregations; use them for forensics, not for
KPIs.

### What does NOT land in the audit row

- The substantive payload (the email body the agent wanted to send,
  the diff of a memory rule edit, etc.). Substantive content goes to
  R2 per `r2-vectorize-naming.md`; the audit row carries only the
  decision and its closed-vocabulary reason.
- The free-text `EnforcementDecision.reason` string from
  `adapter/trust_ceiling.py`. The closed-enum `DecisionReason`
  replaces it for audit purposes; the free-text string may still be
  surfaced in operator UIs (e.g., a tooltip on the audit row) but
  must NOT be a basis for aggregation.
- PII. The reason enum is closed; customer / skill / action_class /
  ceiling_level are opaque identifiers and closed-vocabulary values.

## Failure modes

- **Audit write fails.** `AuditWriteError` propagates out of
  `log_decision()`. The caller (dispatch path) MUST treat this as
  fatal and abort the pending action. Same invariant as the audit
  log writer (PR #942): a state the substrate cannot evidence is a
  state the agent does not run.
- **Free-text reason supplied.** `ValueError` raised before the SQL
  executes. The dispatch path must map to a `DecisionReason` member;
  the integration table in this spec is the contract.
- **Invalid ceiling_level / action_class.** Strings are validated
  against the closed enums; `ValueError` raised on a typo.
- **`extra_metadata` collides with a canonical key.** `ValueError`
  raised. Use a non-canonical key name (e.g., `tool_name`,
  `recipient_domain`) for caller-side forensics.

## Aggregation

The Captain dashboard's aggregate view and the customer dashboard's
recent-decisions feed both filter on the `trust_ceiling_decision = true`
flag in `metadata`. The dashboard implementer (separate issue) uses
these queries:

### Captain: aggregate decisions over time

```sql
SELECT
  json_extract(metadata, '$.decision') AS decision,
  json_extract(metadata, '$.reason')   AS reason,
  json_extract(metadata, '$.action_class') AS action_class,
  COUNT(*) AS count
FROM audit_log
WHERE json_extract(metadata, '$.trust_ceiling_decision') = 1
  AND ts >= ? AND ts < ?
GROUP BY decision, reason, action_class
ORDER BY count DESC;
```

Bucketed by hour / day / week: add
`strftime('%Y-%m-%d', ts) AS day` to the GROUP BY.

### Customer dashboard: recent decisions for this account

```sql
SELECT
  id,
  ts,
  skill_name,
  json_extract(metadata, '$.decision')      AS decision,
  json_extract(metadata, '$.reason')        AS reason,
  json_extract(metadata, '$.action_class')  AS action_class,
  trust_ceiling
FROM audit_log
WHERE json_extract(metadata, '$.trust_ceiling_decision') = 1
ORDER BY ts DESC
LIMIT 100;
```

The customer dimension is the D1 binding (one DB per customer per ADRs
0008 + 0009); no cross-customer JOIN is permitted. Aggregations against
multiple customers happen at the Captain control-plane layer, summing
per-customer results.

### Cross-row correlation via `trace_id`

A single dispatch turn may emit:

1. One trust-ceiling decision row (from `log_decision()`)
2. One action-specific audit row downstream (e.g., the writer's own
   `DRAFT_CREATED` row when the draft was actually persisted, or the
   downstream `SENT_DETECTED` row when an autonomous send went out)

Both rows carry the same `metadata.trace_id` so the dashboard can join
them when rendering the per-turn audit trail. The `trace_id` is set by
the dispatch path; this module passes it through unchanged.

## Verification

`operator/safety_substrate/tests/test_trust_ceiling_log.py` exercises:

- One happy-path row exercises every canonical metadata key
- Each of three decisions maps to the correct action_type
- Each of the 14 `DecisionReason` members emits a correctly-shaped row
  (parametrized over allow / draft / refuse reason sets)
- Free-text `decision` and `reason` arguments raise `ValueError`
- Invalid `ceiling_level` / `action_class` string values raise
  `ValueError`
- Missing required `customer` / `skill` arguments raise `ValueError`
- String-valued `ceiling_level` / `action_class` (the dispatch path's
  natural shape) pass through and validate
- The PRD's `disabled` synonym for `refused` is accepted
- `extra_metadata` merges after canonical keys and may NOT override them
- Optional fields (`skill_version`, `trace_id`, `matter_ref`) default
  to `None` and land as JSON nulls in metadata
- Audit write failure propagates as `AuditWriteError` (caller-abort
  invariant)
- Metadata key set is stable (the dashboard aggregation contract)
- The dashboard's filter predicate
  (`metadata.trust_ceiling_decision = true`) identifies trust-ceiling
  rows without false positives against unrelated audit rows

Run locally:

```
cd operator && uv run --with pytest python -m pytest \
  safety_substrate/tests/test_trust_ceiling_log.py -v
```

## Out of scope (filed elsewhere)

- **Dashboard surfaces.** The Captain aggregate view and the customer
  recent-decisions feed are separate issues. This spec is the emission +
  audit row contract; the dashboards consume it.
- **Hermes dispatch integration.** Wiring `log_decision()` to the
  dispatch path is the adapter team's work and tracked separately. The
  integration-point mapping table above is the contract.
- **`TRUST_CEILING_DECISION` action_type promotion.** If a future PR
  (with the right scope) extends `ACCEPTED_ACTION_TYPES`, this module's
  mapping can be updated in one place. The metadata contract stays the
  same, so the dashboard queries continue to work either way.

## Cross-references

- platform-prd.md §11 (trust ceiling model): vocabulary source
- d1-schema.md §1 (audit_log): column shape and accepted action types
- sticky-stop.md: precedent for reusing closed-set action_types with
  metadata-bearing transition details
- compliance-evidence-packet.md (#802): downstream consumer that
  groups rows by `metadata.trust_ceiling_decision`
- PR #942 (audit log persistence): provides `AuditLogWriter`
- PR #948 (sticky-stop mechanism): convention source
- ADR 0008 (customer-owned memory artifact)
- ADR 0009 (cross-machine query prohibition): why the customer
  dimension is the D1 binding, not a row column
