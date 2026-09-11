# Safety Invariants #6 and #7

**Spec for issue [#865](https://github.com/venturecrane/ss-console/issues/865).** Runtime implementation of two platform PRD §7.5 invariants. Base invariants #1-#5 ship from PR #812 and are exercised by per-fixture tests under `operator/safety-substrate/tests/`. Invariant #6 is partially shipped as `citation_filter.py` (law-vertical refusal layer) plus the citation-enforcement layer documented here. Invariant #7 is shipped here for the first time. Invariant #8 (fabrication discipline) lives in [fabrication-filter.md](fabrication-filter.md) (issue #798).

## Source

- platform-prd.md §7.5 ("Safety substrate") - invariant text
- platform-prd.md §10 (`09-boot-checks.csv`) - compliance evidence for invariant #7
- d1-schema.md §1 - `audit_log` action_type vocabulary
- r2-vectorize-naming.md §"Invariant #7 boot-check" - binding-name contract
- fabrication-filter.md - companion runtime filter for invariant #8

## Invariant #6 - citation enforcement for fact-bearing fields

Platform PRD §7.5 invariant #6 names a "citation-refusal layer." The law-firm vertical implements that as **refusal on fabricated legal citations** in any output (case names, reporter cites, statute references, court rules). That layer is `operator/safety-substrate/citation_filter.py`, shipped with PR #812 and exercised by `tests/test_invariant_6_no_citations.py`.

The complement shipped here is **citation enforcement on fact-bearing fields**. Every fact a skill renders into a declared fact-bearing field must carry a `Citation` attached to a real source. The two layers cover the two failure modes:

| Failure mode             | Example                                                                                                | Layer                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| **Fabricated authority** | Agent invents `Smith v. Jones, 123 U.S. 456 (1990)` in a draft.                                        | `citation_filter.py` (law-vertical)        |
| **Unsourced assertion**  | Agent renders `case_value_range = "$80,000-$150,000"` into a client-facing field with no source cited. | `invariants/invariant_6.py` (this section) |

Both layers ship together. Either failing on its own is a single-layer enforcement; both failing is the failure the substrate exists to prevent.

### Module shape

`operator/safety-substrate/invariants/invariant_6.py`. Pure-function contract.

```python
from invariants.invariant_6 import (
    Citation,
    SourceKind,
    enforce_citations,
)

result = enforce_citations(
    output={
        "case_value_range": "$80,000-$150,000",
        "client_name": "Maria Diaz",
        "liability_section": "",
    },
    citations={
        "case_value_range": Citation(
            source_kind=SourceKind.MEMORY_RULE,
            source_id="01HQTESTRULE00000000000000",
        ),
        "client_name": Citation(
            source_kind=SourceKind.SYSTEM_OF_RECORD,
            source_id="filevine:contact:42",
        ),
    },
    expected_fields={
        "case_value_range": "fact_bearing",
        "client_name": "fact_bearing",
        "liability_section": "none",
    },
)
if result.has_violations:
    raise CitationEnforcementError(result)
```

### Citation schema

```python
class SourceKind(str, Enum):
    MATTER_DOCUMENT = "matter_document"
    MEMORY_RULE = "memory_rule"
    SYSTEM_OF_RECORD = "system_of_record"
    VERBATIM_QUOTE = "verbatim_quote"

@dataclass(frozen=True)
class Citation:
    source_kind: SourceKind
    source_id: str                       # required, non-empty
    span: Optional[tuple[int, int]]      # required for VERBATIM_QUOTE; optional otherwise
```

Source kinds are a closed enum. A `Citation` whose `source_kind` is not a `SourceKind` instance is rejected at construction. `source_id` must be non-empty. `span` is a half-open `(start, end)` interval with `0 <= start < end` and is required for `VERBATIM_QUOTE`.

### Field tagging

The invariant reads a per-skill field tag map. Tags drawn from the skill's `output-format.md` per skill anatomy §8.4:

| Tag            | Meaning                                                                          | Invariant #6 behavior            |
| -------------- | -------------------------------------------------------------------------------- | -------------------------------- |
| `fact_bearing` | Field renders a fact from a source.                                              | Citation required when rendered. |
| `none`         | Load-bearing legal-judgment content; the partner fills in after the draft lands. | **Skipped.** See edge case.      |
| (other)        | Header copy, fixtures, free prose the skill author did not declare fact-bearing. | Skipped.                         |

### PRD edge case: `none`-tagged fields

The named edge case in the task instruction. Per [fabrication-filter.md §3.2](fabrication-filter.md), a `none`-tagged field must render empty - the partner fills it in. A `none` field that renders non-empty is invariant #8's failure (fabrication discipline), not invariant #6's. The invariant therefore SKIPS `none` fields regardless of whether they render empty or non-empty. The fabrication filter handles them separately.

Covered by test `test_skips_none_tagged_field_even_when_rendered`.

### Violation kinds

```python
class CitationViolationKind(str, Enum):
    MISSING_CITATION = "missing_citation"     # field rendered, no Citation attached
    EMPTY_SOURCE_ID = "empty_source_id"       # Citation attached, source_id empty
    MALFORMED_CITATION = "malformed_citation" # Citation shape invalid
    UNEXPECTED_FIELD = "unexpected_field"     # only when allow_extra_output_keys=False
```

### Scope boundaries

Three things invariant #6 does NOT do:

1. **It does not scan free prose for citation-shaped strings.** That is `citation_filter.py`'s job.
2. **It does not verify `source_id` resolves to a real record.** That is invariant #8's job (`fabrication-filter.md`).
3. **It does not gate sends.** It is a pure function; production callers MUST treat `has_violations` as block-emission.

### Audit emission

Every violation set the caller decides to enforce on writes one row via `AuditLogWriter`:

| Field         | Value                                                                  |
| ------------- | ---------------------------------------------------------------------- |
| `action_type` | `INVARIANT_VIOLATION` (reuses existing `ACCEPTED_ACTION_TYPES` member) |
| `actor`       | `agent`                                                                |
| `actor_role`  | `ActorRole.AGENT`                                                      |
| `skill_name`  | name of the skill whose output failed                                  |
| `metadata`    | `result.to_audit_metadata()` - see shape below                         |

```json
{
  "invariant": 6,
  "violations": [
    {
      "field_name": "case_value_range",
      "kind": "missing_citation",
      "rendered_excerpt": "$80,000-$150,000",
      "detail": "field is declared fact_bearing and rendered non-empty content, but no Citation is attached"
    }
  ]
}
```

We reuse `INVARIANT_VIOLATION` rather than introducing a new `CITATION_VIOLATION` member - consistent with how `sticky_stop.py` routes an observation that changes no level (since the 2026-09-02 two-state collapse, the time-budget overrun; before it, the WARN/SOFT_STOP transitions). Adding a new closed-set action type requires updating both the constant and `d1-schema.md` §1.

Excerpts are truncated to 80 characters with a trailing ellipsis to bound the audit-row size; the full draft is content-addressed via the writer's existing `output_payload` SHA-256 digest channel.

## Invariant #7 - cross-Machine query prohibition

Platform PRD §7.5 invariant #7:

> No agent reads storage bound to another customer's Machine. At Machine boot, the runtime verifies its storage bindings include only its own customer's namespaces and refuses to start if it detects bindings outside its namespace.

The mechanism is the load-bearing trust commitment of per-customer isolation. Any customer who can convince themselves another customer's data could ever surface inside their agent will not sign. The check is mechanical: no heuristics, no ML, no fuzzy matching.

### Boot-time check, not runtime filter

The invariant runs once at Machine startup before any request is served. Fly/Cloudflare bindings are immutable for the Machine's lifetime, so a single boot-time check is sufficient. The companion runtime check is **dead-letter**: any attempt to access a binding by an unexpected name at runtime is a bug in the binding-resolver helper (`adapter/r2_helper.py` and its TS twin), not a separate runtime invariant.

### Module shape

`operator/safety-substrate/invariants/invariant_7.py`.

```python
from invariants.invariant_7 import (
    BindingSnapshot,
    verify_storage_bindings,
)

snapshot = BindingSnapshot(
    d1_database_name="hermes-acme-d1",
    r2_bucket_name="hermes-acme-r2",
    vectorize_vault_index="hermes-acme-vault",
    vectorize_corrections_index="hermes-acme-corrections",
)
result = verify_storage_bindings(
    customer_slug="acme",
    snapshot=snapshot,
)
if not result.passed:
    log.error(result.refusal_message())
    await writer.write(AuditEvent(
        action_type="INVARIANT_BOOT_CHECK_FAILED",
        actor="agent",
        actor_role=ActorRole.AGENT,
        metadata=result.to_audit_metadata(),
    ))
    sys.exit(3)
```

### Binding kinds

Closed set of four safety-relevant bindings per [r2-vectorize-naming.md](r2-vectorize-naming.md):

| Kind                    | Expected name               | Cloudflare resource  |
| ----------------------- | --------------------------- | -------------------- |
| `D1`                    | `hermes-{slug}-d1`          | D1 database name     |
| `R2`                    | `hermes-{slug}-r2`          | R2 bucket name       |
| `VECTORIZE_VAULT`       | `hermes-{slug}-vault`       | Vectorize index name |
| `VECTORIZE_CORRECTIONS` | `hermes-{slug}-corrections` | Vectorize index name |

Adding a new kind requires an ADR, an update to the binding-resolver helper, and an update to `provision-customer.sh`.

### Failure modes

Three distinct failure-mode reasons surface in `BindingMismatch.reason`:

1. **Cross-Machine** - binding name does not start with `hermes-{slug}-`. The named PRD failure: a binding resolved to another customer's resource. Reason text contains `"cross-Machine isolation failure mode"`.
2. **Wrong kind suffix** - binding name starts with the right prefix but the kind suffix is wrong. Config drift, typically a typo at provisioning time. Reason text contains `"wrong kind suffix"`.
3. **Empty / unbound** - binding name is the empty string. Reason text contains `"unbound"`.
4. **Malformed slug** - `customer_slug` itself is invalid (uppercase, leading/trailing hyphen, too short/long, contains characters outside `[a-z0-9-]`). Every binding is flagged because the expected-name derivation is meaningless. Reason text contains `"is not a valid slug"`.

The slug rule (lowercase letters, digits, hyphens; 2-32 chars; no leading or trailing hyphen) mirrors the canonical `customer.yaml` validator at `src/lib/operator/customer-yaml/` (per ADR 0019).

### Audit emission

A failed boot-check writes one row:

| Field         | Value                                                            |
| ------------- | ---------------------------------------------------------------- |
| `action_type` | `INVARIANT_BOOT_CHECK_FAILED` (existing `ACCEPTED_ACTION_TYPES`) |
| `actor`       | `agent`                                                          |
| `actor_role`  | `ActorRole.AGENT`                                                |
| `metadata`    | `result.to_audit_metadata()` - see shape below                   |

```json
{
  "invariant": 7,
  "customer_slug": "acme",
  "mismatches": [
    {
      "kind": "d1",
      "expected": "hermes-acme-d1",
      "observed": "hermes-other-d1",
      "reason": "observed binding does not start with the expected per-customer prefix 'hermes-acme-' - this is the cross-Machine isolation failure mode"
    }
  ]
}
```

A passing boot writes no audit row at boot - the absence of a failure row is the compliance evidence. The PRD §10 `09-boot-checks.csv` packet renders these audit entries.

### No PII in failure rows

Mismatch metadata contains only binding names and customer slugs. Slugs are business slugs, not natural-person identifiers. No customer data is exfiltrated by the failure row.

## Boot-path integration

> **TARGET STATE — not yet wired.** The bootstrap order below is the intended boot sequence. `verify_storage_bindings` is defined and tested in this repo, but its `bootstrap.sh` integration (reading customer.yaml, collecting the `BindingSnapshot` from Fly env metadata, writing the audit row, `sys.exit(3)`) is not yet implemented — see "Open items deferred" below ("Wiring `verify_storage_bindings` into `bootstrap.sh`"). Do not read this sequence as evidence that the boot-check runs in production.

Bootstrap order (target, per `bootstrap.sh`):

```
1. Read customer.yaml → customer_slug
2. Collect BindingSnapshot from runtime env metadata
3. verify_storage_bindings(slug, snapshot)
4. If !passed:
     - log refusal_message() to stderr
     - write INVARIANT_BOOT_CHECK_FAILED audit row (best-effort; if D1 is the failed binding, the row is logged only)
     - sys.exit(3)
5. Otherwise proceed to skill loading + dispatch
```

The exit code `3` is reserved for invariant-boot-check failures per [r2-vectorize-naming.md](r2-vectorize-naming.md) §"Invariant #7 boot-check".

## Where invariant #6 enforcement plugs into the dispatch path

> **TARGET STATE — not yet wired.** The flow below is the intended call-site contract, not a live control. `enforce_citations` is a pure function defined and tested in this repo's safety-substrate; nothing calls it from the Hermes skill-output dispatch path today. See "Open items deferred" below ("Wiring `enforce_citations` into the dispatch path") — that surface lives in the Hermes runtime, not in this repo, and is tracked as a separate wiring task. Do not read this diagram as evidence that citation enforcement runs in production.

Skill output flows (target):

```
skill.emit_draft()
  ↓
enforce_citations(output, citations, expected_fields)
  ↓ (if violations) AuditLogWriter.write(INVARIANT_VIOLATION)
  ↓ (if violations) raise CitationEnforcementError → caller blocks emission
  ↓ (if clean) citation_filter.contains_citation(rendered_body)
  ↓ (if hit) refuse on legal-citation pattern
  ↓ (if clean) fabrication_filter.check(draft)
  ↓ (if violations) refuse on unsourced fact
  ↓ (if clean) draft_queue.insert(draft)
```

The order is intentional. Citation enforcement runs first because its violation list is the most actionable to the skill author (the field name and reason are explicit). Citation filtering catches outputs the field-level layer cannot - free-prose case-cite fabrication. Fabrication filter catches everything else with the per-field source-existence check.

## Tests

| Test file                                             | Coverage                                                                                                                   |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `operator/safety-substrate/tests/test_invariant_6.py` | 18 cases. Citation constructor contract, passing cases, `none`-edge case, missing-citation, partial coverage, audit shape. |
| `operator/safety-substrate/tests/test_invariant_7.py` | 14 cases. Snapshot contract, passing case, cross-Machine, wrong-suffix, empty, malformed slug, audit shape, refusal text.  |

Run from repo root:

```bash
cd operator && uv run --with pytest python -m pytest \
    safety-substrate/tests/test_invariant_6.py \
    safety-substrate/tests/test_invariant_7.py -v
```

Each invariant module also exports a `run() -> (bool, str)` callable consumed by the substrate runner at `safety-substrate/run_invariants.py` for boot-time smoke fixtures. Comprehensive coverage is the pytest path.

## Open items deferred

- **Wiring `enforce_citations` into the dispatch path.** This spec defines the function and contract. Plugging the call site into the Hermes skill-output path is tracked separately because that surface lives in the Hermes runtime, not in this repo's safety-substrate.
- **Wiring `verify_storage_bindings` into `bootstrap.sh`.** The function is ready; the bootstrap-shell integration (reading customer.yaml, collecting the snapshot from Fly env metadata, writing the audit row) is a separate boot-script change tracked under the invariant-7 boot-check rollout.
- **Closed-set audit action type for citation violations.** This spec reuses `INVARIANT_VIOLATION` with `metadata.invariant=6`. If the compliance-evidence packet's roll-up needs first-class bucketing, a dedicated `CITATION_VIOLATION` action type can be added under a follow-on ADR.

## Cross-spec references

- [fabrication-filter.md](fabrication-filter.md) - invariant #8, the per-field source-existence check that complements invariant #6.
- [r2-vectorize-naming.md](r2-vectorize-naming.md) - the binding-name contract invariant #7 enforces against.
- [sticky-stop.md](sticky-stop.md) - invariant #4's runtime mechanism; the same audit-emission and `INVARIANT_VIOLATION` action-type pattern is used here.
- [d1-schema.md](d1-schema.md) - the `audit_log` table schema and accepted-action-type vocabulary.
