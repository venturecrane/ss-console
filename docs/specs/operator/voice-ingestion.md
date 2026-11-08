# Voice sample ingestion pipeline

**Status:** RETIRED 2026-11-07 (ss ADR 0083 decision 6). The pipeline, its code (`operator/adapter/voice/`, the overlay `hermes-smd-voice` plugin, the ingest tools) and its seat config were removed; this page is the record of what it was.
**Code:** none (removed)
**Migration:** `operator/migrations/0005_voice_ingestion.sql`

---

## Purpose

Voice Layer 2 ("the agent learns the partner's writing by reading what they
actually send") requires a stream of real partner-authored sent messages,
captured in a privacy-preserving form, tagged by recipient cohort, and
removable on demand. This pipeline is the producer side of that stream.

It is the sibling of the memory ingestion pipeline:

- Memory ingestion = matters, documents, recipients from the
  PracticeManagement capability
- Voice ingestion = structural-diffs of partner-sent emails from the
  Email capability

The two pipelines do not share rows. They share the substrate (per-customer
D1 + R2 + audit log) and the architectural rules (ADR 0006, 0008, 0009).

## Contract

The pipeline depends on the **Email capability** interface defined in
`src/lib/operator/capabilities/email.ts`. Specifically the read-side
methods `list_sent_since(cursor)` and `get_sent_item(message_id)`. The
pipeline never sends. The pipeline never calls MS Graph or Gmail directly.

A concrete adapter is wired by issue #822 (MS Graph + Gmail OAuth). Until
that ships, the pipeline runs against `NoEmailSource`, which always
returns the empty sequence and produces a clean zero-items state row.

## Flow

```
                  +---------------------------------+
  scheduled cron  |  VoiceIngestionRunner           |  on-demand call
        \         |  .run_ingestion(mode=...)       |        /
         \-->>----+                                 +----<<-/
                  |  for each SentMessage:          |
                  |    1. dedupe (digest lookup)    |
                  |    2. PartnerAuthoredFilter     |
                  |    3. extract_structural_diff   |
                  |    4. resolve cohort            |
                  |    5. insert provenance row     |
                  |    6. write R2 object           |
                  |  upsert voice_source_state      |
                  +----+---------------------+------+
                       |                     |
                       v                     v
                voice_ingestion_items   {slug}/voice/cohort/{cohort}/{id}.json
                (D1)                    (R2)
```

## Privacy posture

The raw email body is **never persisted**. The structural-diff extractor
(`adapter/voice/diff.py`) computes:

- word_count, sentence_count, paragraph_count, subject_word_count
- sentence_length_distribution (5-bucket histogram)
- avg_sentence_length
- greeting_style + signoff_style (closed-set categorical labels)
- opener_template + closer_template (the category, never the recipient
  name)
- punctuation_rhythm (counts per 100 words)
- recipient_cohort

Output is JSON-serializable, deterministic, and bounded in size. The
body lifetime ends inside `_ingest_one()`; everything downstream operates
on the structural-diff only.

No quoted text, no recipient names, no email addresses, no PII, no
specific content tokens. The `source_message_digest` in D1 is the
SHA-256 of the upstream message ID — the message ID itself is not
stored.

## Partner-authored filter

Three signals, in order:

1. **Adapter-reported provenance.** When the Email adapter populates
   `SentItem.likely_agent_drafted = True`, the message is excluded
   immediately. This is the strongest signal — the adapter is telling
   us it can see the agent's identity in the draft history.

2. **Audit-log digest match.** When the adapter cannot tell, the
   filter computes a SHA-256 of the body and consults the audit log
   for a `DRAFT_CREATED` row whose `input_digest` matches. The audit
   log stores digests only (per issue #891), so this cross-check costs
   nothing extra in privacy terms.

3. **Body-shape heuristic.** A final pass that excludes messages whose
   shape (signature markers, the "Drafted by your Operator for
   review" footer) matches the agent's draft templates. Single hit is
   enough to exclude.

The filter biases toward exclusion. A false-positive exclusion costs
one missed sample; a false-positive inclusion teaches the voice
library the agent's own voice. We accept the asymmetry.

Excluded messages still receive a provenance row with
`partner_authored = 0` and a `filter_reason` string. The dashboard
drill-down surface uses these for "why was this not learned from"
explanations without ever retaining the body.

## Recipient cohort tagging

Per PRD §17.1, recipient cohort is a derived attribute (e.g.,
`opposing-counsel`, `client`, `expert-witness`). The cohort assignment
lives in the customer's memory store as `memory_rules` rows where
`rule_type = 'voice'` and `category = 'recipient_cohort'`.

The pipeline reads the cohort via a `CohortResolver` injected at
construction; it does NOT invent cohorts. When no recipient has a
cohort assignment, the sample is tagged `unassigned` (defined in
`adapter/voice/state.COHORT_UNASSIGNED`). The dashboard surfaces the
unassigned bucket so the principal can add memory rules later.

## Storage layout

**R2 key.** Per `docs/specs/operator/r2-vectorize-naming.md`:

```
{customer-slug}/voice/cohort/{cohort-id}/{sample-id}.json
```

`sample-id` is the same ULID as the `voice_ingestion_items.id` row,
so retention and decommission can resolve a row to its R2 object by
construction.

**D1 tables (migration 0004).**

- `voice_source_state` — one row per `(source_kind, source_id)`.
  Upserted on every run, regardless of outcome. Holds
  `samples_by_cohort_json` so the dashboard renders the per-cohort
  histogram without a join.
- `voice_ingestion_items` — one provenance row per sample, including
  excluded samples. `r2_key` and `structural_diff_digest` are NULL on
  excluded rows; populated on accepted rows.

## Captain review surface (AC #4)

The dashboard reads `voice_source_state` (single SELECT) to render:

- Last ingestion timestamp + status (green / yellow / red)
- Per-cohort sample count from the last run
- Last error text when status is `error`
- Items considered / accepted / filtered breakdown via a follow-up
  count query on `voice_ingestion_items`

Captain can drill into any cohort and see the provenance rows. The
provenance row carries the filter reason, sent timestamp, word count,
and the R2 key (when accepted). The structural-diff JSON itself is
small and human-readable; Captain can fetch it directly from R2 to
audit what the agent is learning.

## Retention (AC #5)

Customer.yaml carries the retention window (proposed addition under
`memory:`):

```yaml
memory:
  d1_namespace: demo-firm
  r2_vault_path: vaults/demo-firm/
  vectorize_index: hermes-demo-firm-vault
  voice_retention_days: 365 # OPTIONAL; default 365
```

`enforce_retention()` is run on a separate daily cron. It walks
`voice_ingestion_items` for rows older than the cutoff, deletes the R2
object, and soft-deletes the provenance row. The state row's
`samples_by_cohort_json` snapshot persists — retention removes samples,
not the dashboard's historical view of the last run.

## Decommission (ADR 0008)

`decommission_source()` is called by `bin/decommission-customer.sh`.
It walks every active provenance row, deletes each R2 object,
soft-deletes every provenance row, and removes the state row. The
result is reported back to the operator as `{removed: N, errors: E}`
so the decommission script can refuse to mark the customer offboarded
when `errors > 0`.

## ADR conformance

- **ADR 0005.** Pipeline reads sent folder only.
  No send paths.
- **ADR 0006 (capability adapter pattern).** Pipeline depends on
  `EmailSource` (which wraps the `Email` capability), never on MS
  Graph or Gmail directly.
- **ADR 0008 (customer-owned memory).** R2 keys land under the
  customer slug; every persisted artifact has a provenance row;
  decommission removes everything.
- **ADR 0009 (cross-machine query prohibition).** No tenant ID on
  rows; isolation is the D1 + R2 binding.

## Out of scope

- The MS Graph and Gmail adapter implementations (issue #822).
- The Captain dashboard endpoint that queries `voice_source_state` and
  `voice_ingestion_items` (filed as a follow-up).
- Voice gate scoring against the ingested library (issues #823, #939).
- Voice library training itself — the structural-diffs are the
  durable input; downstream training reads them on demand.
