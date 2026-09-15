# Fabrication Filter — Runtime Pre-Output Enforcement

> **Amendment (2026-07-04, Captain decision, issue #1687): scope deliberately narrowed.**
> The field-tag / source-existence filter specified below is **not built and is not
> planned as specified**. The 2026-07-04 strategic review flagged the gap between this
> spec and enforcement; the Captain call is downgrade-with-precision: the fabrication
> gate IS the three shipped, code-enforced dimensions, and this spec records the
> narrowing rather than implying the fourth dimension is pending.
>
> **What is enforced today (the actual gate):**
>
> 1. **Fabrication markers** — banned phrases/tells blocked at the overlay's
>    `pre_tool_call` outbound gate (`FABRICATION_FILTER_TRIGGERED`, overlay#121,
>    `shared/fabrication_markers.py`).
> 2. **Fabricated citations** — legal-citation-shaped output refused in law skills
>    (invariant #6, `operator/safety_substrate/citation_filter.py`) and fabricated
>    citations blocked outbound (overlay#121).
> 3. **Unverified identifiers** — asserted identifiers (account/case/phone-shaped
>    values) require provenance (`operator/adapter/identifier_filter.py`, 481 lines;
>    `IDENTIFIER_UNVERIFIED` audit events).
>
> These cover the liability classes the rule exists for: citations (sanctions risk),
> identifiers (wrong-target risk), markers (fabrication tells). The whole-of-output
> claim-vs-records check below adds latency, cost, and false-positive refusals on
> every draft for speculative value.
>
> **Revisit trigger:** post-A&P real draft volume showing unsupported-claim escapes
> that the three dimensions missed. If that evidence appears, this spec resumes as
> the design of record. The client-facing description of the gate
> (`src/pages/ai-disclosure.astro`) names exactly the enforced dimensions.

**Spec for issue #798.** Invariant #8 (fabrication discipline) implemented as a runtime pre-output filter, parallel to citation-refusal (#6). Skill-level `context-detector` is supplementary, not the primary enforcement. The CLAUDE.md "no fabricated client-facing content" rule needs an architectural backstop.

## Source

- CLAUDE.md "No fabricated client-facing content" rule (Pattern A + B)

## Contract

### The filter

Every draft emitted by any skill passes through the fabrication filter before reaching the `draft_queue` table. The filter runs at the runtime layer, between `skill.emit_draft()` and `draft_queue.insert()`. No skill can bypass it.

```python
class FabricationFilter:
    """Pre-output filter. Runs on every draft before it reaches the queue."""

    def check(self, draft: Draft) -> FilterResult:
        violations: list[Violation] = []
        for field in draft.client_facing_fields:
            if field.sourced_from == "none" and field.rendered_value:
                violations.append(Violation(
                    field=field.name,
                    rendered=field.rendered_value,
                    reason="client_facing field has no source tag but renders non-empty",
                ))
            if field.sourced_from in ("memory_rule", "person_mapping", "matter_attribute", "system_of_record"):
                if not field.source_id:
                    violations.append(Violation(
                        field=field.name,
                        rendered=field.rendered_value,
                        reason=f"sourced_from={field.sourced_from} but no source_id provided",
                    ))
                elif not self._source_exists(field.sourced_from, field.source_id):
                    violations.append(Violation(
                        field=field.name,
                        rendered=field.rendered_value,
                        reason=f"sourced_from={field.sourced_from}({field.source_id}) but source not found",
                    ))

        # Pattern-based safety net: even if the field is tagged, scan for high-risk markers
        for marker in HIGH_RISK_MARKERS:
            if marker.matches(draft.rendered_body):
                violations.append(Violation(
                    field="body",
                    rendered=marker.matched_text,
                    reason=f"high-risk marker {marker.name} matched; verify source",
                ))

        return FilterResult(violations=violations)

HIGH_RISK_MARKERS = [
    Marker("specific_dollar_amount", r"\$\d[\d,]{3,}"),         # > $999
    Marker("future_date", r"\b\d{4}-\d{2}-\d{2}\b"),
    Marker("commitment_phrase", r"\b(we'll|we will|I'll|I will)\s+(reach out|schedule|begin|deliver|complete|finish|ship)\b"),
    Marker("guarantee_phrase", r"\b(guarantee|guaranteed|promise|ensure|warrant)\b"),
    Marker("post_engagement_phrase", r"\b(stabilization period|warranty period|follow-up period)\b"),
]
```

### Skill anatomy requirement

Every `SKILL.md` declares its client-facing fields in the frontmatter:

```yaml
---
name: inbox-triage-and-draft
client_facing_fields:
  - name: greeting_name
    sourced_from: person_mapping
  - name: matter_reference
    sourced_from: matter_attribute
  - name: next_step
    sourced_from: memory_rule
  - name: deadline_date
    sourced_from: system_of_record
  - name: signoff
    sourced_from: memory_rule
---
```

Allowed values for `sourced_from`:

- `memory_rule` — a row in `memory_rules` (D1)
- `person_mapping` — a row in `person_mappings` (D1)
- `matter_attribute` — a field on a `Matter` returned by PracticeManagement capability
- `system_of_record` — any other adapter-returned record (Email thread, ESign envelope, etc.)
- `none` — the field is rendered empty-state per docs/style/empty-state-pattern.md; must NOT render plausible content

A skill that emits a draft with a `client_facing_fields` entry omitted, or with `sourced_from` set to an unknown value, fails the filter.

### Behavior on violation

The filter returns one of three outcomes:

| Severity | Outcome                                                                                          | Audit event                                     |
| -------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| `clean`  | Draft proceeds to `draft_queue`                                                                  | (none — happy path)                             |
| `flag`   | Draft proceeds but tagged in dashboard "verify source"; reviewer sees yellow banner              | `FABRICATION_FILTER_TRIGGERED` (severity=flag)  |
| `block`  | Draft rejected; agent re-runs with stricter prompt; if 2nd run also blocked, escalate to Captain | `FABRICATION_FILTER_TRIGGERED` (severity=block) |

Severity mapping:

- `none`-tagged field with non-empty value → block
- `sourced_from` tagged but source not found → block
- High-risk marker matched on a `system_of_record`-tagged field → flag (the source is upstream; reviewer verifies)
- High-risk marker matched on a `memory_rule` or `person_mapping`-tagged field → flag (likely OK; reviewer verifies)
- 3+ flags on a single draft → block

### Relationship to `context-detector` skill

`context-detector` (§8.2 cross-cutting universal) remains in the catalog but its role is **supplementary**:

- It examines drafts that passed the filter as `clean` or `flag` and looks for higher-order concerns (regulatory citations, court-bound content, settlement amounts) that the filter's pattern matching misses.
- It does NOT enforce invariant #8. The filter does.
- `context-detector` runs on a separate worker thread; failure or `trust: disabled` does not break invariant #8 enforcement.

## Failure modes

- **Skill omits `client_facing_fields` frontmatter** → CI gate `tests/operator/skill-frontmatter.test.ts` blocks PR. Provision-time check refuses skill activation if frontmatter missing on `enabled: true` skill.
- **Filter throws an exception** → runtime treats as `block`; emits `FABRICATION_FILTER_TRIGGERED` (severity=error) and `INVARIANT_VIOLATION` audit events; Captain alerted. No draft proceeds while the filter is unavailable.
- **False positives on legitimate dollar amounts/dates** (e.g., a `matter_attribute`-sourced settlement amount): the filter flags rather than blocks for sourced-tagged fields; the reviewer sees a yellow banner and confirms. Captain reviews flag rate weekly; persistent false positives feed back into marker tuning.
- **Skill author tags field as `memory_rule` to bypass the marker scan**: covered by the source-existence check. If `memory_rules` doesn't contain a row matching the tagged ID, block. Source-IDs are not user-controllable from the skill — they're injected by the runtime when the skill calls `memory.get(rule_id)` or similar.

## Verification

1. **Filter test suite** at `tests/operator/fabrication-filter.test.ts` covers: every action class × every sourced_from × clean/flag/block decision. ≥40 cases.
2. **Frontmatter CI gate** at `tests/operator/skill-frontmatter.test.ts` walks every `operator/skills/*/SKILL.md` and asserts `client_facing_fields` block is present and well-formed.
3. **Production audit-log assertion**: a weekly Captain query against `audit_log` reports flag/block rate per skill. If block rate > 5% on any skill, that skill is reviewed.
4. **Regression fixture corpus**: `operator/fixtures/fabrication/` contains 20+ historical Pattern A/B violations (e.g., the "We'll reach out to schedule kickoff" string from CLAUDE.md). Every fixture must produce a `block` outcome — this protects against regression to the SS-console Apr-15 audit failures.

## Implementation notes

> **Mechanism reconciled 2026-05-29 (ADR 0028).** The original `before_emit_draft`
> / `aie_adapter.py` `BeforeEmitDraftHook` mechanism below is **dead** — it
> predates the 2026-05-24 plugin-only realignment and references a hook that is
> not in Hermes' real `VALID_HOOKS`. The filter now runs at the overlay
> `hermes-smd-trust` plugin's **`pre_tool_call`** hook (the only return-value-
> blocking hook), as a second evaluation after the ceiling check, on
> draft-creating tools — inspecting the draft body in the tool `args` before the
> tool runs. With send a configurable entitlement (ADR 0035), the filter runs
> before any client-facing content is emitted — whether the tool drafts or sends —
> at the same `pre_tool_call` hook, after the ceiling check. Provenance is two-tier: Tier-1
> universal markers (`fabrication_markers.json`, every vertical) + Tier-2
> citation filter (law-vertical only). See `docs/adr/0028-outbound-integrity-gates-provenance-and-voice.md`.

- The marker registry is `operator/safety_substrate/fabrication_markers.json`
  (the single source of truth); the overlay vendors a copy with a CI hash-check.
  Updates require PR + Captain sign-off.
- Filter implementation (overlay): `shared/outbound_gate.py` + `shared/fabrication_markers.py`.
- Skills' `client_facing_fields` per-field source-tag model (parsed at skill-load
  time) is the eventual Tier-1; ADR 0028 ships the pattern-marker subset first and
  defers the source-tag model.
- Empty-state pattern reference: `docs/style/empty-state-pattern.md` (existing).
- Citation-refusal substrate (invariant #6) lives at `operator/adapter/citation_refusal.py`; fabrication filter runs first, citation refusal second. Both are pre-emit hooks.

[AMBIGUITY: The 5% block-rate ceiling is a heuristic; tune against real data after week 4 of beta-1. May be too lax (block rate should be near 0 once skills are mature) or too strict (some skills are inherently low-source like brainstorming surfaces). Captain monitors and adjusts.]
