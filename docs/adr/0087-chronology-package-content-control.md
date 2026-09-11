# ADR 0087: The chronology package is filed by the runner, and the runner's gates are its content control

Status: Accepted (Captain decision 2026-08-27, option (a); recorded 2026-08-29), amended 2026-09-09
Issue: [#2611](https://github.com/venturecrane/ss-console/issues/2611) (slice 2 of epic [#2618](https://github.com/venturecrane/ss-console/issues/2618))
Related: [#2439](https://github.com/venturecrane/ss-console/issues/2439) (the forcing incident), ADR 0062 (cost plane), ADR 0075 (scalar skill settings), ADR 0083 (output classes), ADR 0086 (identifier gate posture), `operator/contracts/runtime-controls.yaml`, engagements `service-agreement.md` Exhibit A row 11 (routine 11, two forms)

## Context

Routine 11 (medical chronology) now has two contract forms (Exhibit A, engagements #89, 2026-08-28). The **running chronology** is the memo the seat agent writes into Smokeball as records land; it is unmetered. The **chronology package** is what the firm requests on a matter it designates: a chronology document with a records-reviewed and limitations section, records-only exhibit volumes by provider, and a billing worksheet where ledgers exist; it carries the monthly allowance.

The package is not an agent artifact. Sixteen of them were delivered in August 2026 by a forty-script pipeline that reads hundreds of documents, transcribes scans, composes with a large model, and then audits every claim in the document against the page it cites. That audit is 6 to 55 percent of a run's cost depending on the matter (2026-08-29 homework, `#2618`). It is the content control the firm has actually been receiving.

The seat agent's writes go through the trust plugin's outbound gates: a fabrication marker that refuses dollar figures the provenance register did not see, a citation filter, and an identifier gate that refuses dates the session did not read (overlay `plugins/hermes-smd-trust/outbound.py`, refuse mode on both product seats). On 2026-08-19 those gates refused all three attempts to write the running chronology memo on a real A&P matter (#2439): a chronology that quotes superbills inherently carries amounts, codes, and dates. The gates were right about their job and wrong for this artifact, and the question the incident forced was which control governs a chronology package: the agent-turn gates, or the runner's own audit.

## Decision

**The chronology package is produced by an SMD-owned runner outside the agent turn and filed into the matter through the connector's internal-write tools. The agent-turn outbound gates never inspect it. The runner's own gates are the registered content control for the package.**

### The write path, named

The runner calls the Smokeball connector's `create_folder` and `add_file` from its own process, not from a Hermes tool call. No `pre_tool_call` hook runs, so the trust plugin's `check_outbound_draft` (`outbound.py:793-890`) does not see the bytes. The provenance stamp and the matter-number verification that the connector applies to every write still apply, because they live in the connector, not in the hook. Had the package gone through an agent `add_file` with `content_text`, Tier 1 and Tier 2 would refuse it and Tier 3 would report it (`outbound.py:140-185,293-296`); that route is not used and must not be.

### The controls that replace the gate for this artifact

Four gates in the runner, registered in `operator/contracts/runtime-controls.yaml` as `unprobed` until the Machine-resident driver ships (slice 5, #2614):

1. `medchron_claim_audit`: every dated claim in the document is checked against a rendering of the page it cites; a claim that is not supported is repaired or dropped, never shipped (`tools/medchron/audit_citations.py`, `audit_repair_loop.py`).
2. `medchron_extractive_gate`: the document carries no causation, severity, or valuation language as the runner's own finding, and no totals; charges appear exactly as the source states them, cited (`strip_nonrecord.py` and the composition prompts' extractive floor, agreement section 2.3(c)).
3. `medchron_cross_client_gate`: on joint matters, no page belonging to one client lands in another client's exhibits or chart (`check_unit_identity.py`, per-unit quarantine).
4. `medchron_provenance_gate`: every file the package reads is owned by a unit, byte-duplicate, name-excluded, or an explained orphan; an unexplained file halts delivery (`coverage_gate.py`).

Each row carries a candidate seat probe that HOLDs until a driver exists; none is reported green by inference.

### The running memo stays behind the agent gates

The memo remains an agent write and remains subject to the outbound gates. Its format is rewritten so it passes by construction (slice 1, #2439): figures and codes are pointed to by document and page rather than restated, page cites take a shape the citation filter does not match, and every date in the memo was read this run. The memo is also the covered-set record: it names the documents read and, when a package folder exists on the matter, that folder.

### What is authored on the seat, and what is not

`customer.yaml` authors one routine-11 figure: `chronology_package_document_allowance_per_month`, the Exhibit A row 11 allowance. Per ADR 0075 skill settings are scalar and are rendered verbatim into the agent's profile; a value authored there is readable by the agent and, this being a public repository, by anyone. The runner's per-matter gates, per-job spend cap, and behavioral defaults (pre-incident history, email-attachment folding, unmatched-folder handling, supersede policy, folder naming) are SMD posture, some of it pricing-adjacent, and live in the runner's per-firm configuration in the private engagements repo, where their consumer is.

### Rejected alternatives

- **Route the package through the agent gates with a report-only carve for `add_file`.** The carve exists (`_REPORT_ONLY_DRAFT_TOOLS`) and would let the bytes through, but "report" on a 120-page document with hundreds of legitimate figures is noise that teaches nothing, and the gate's provenance register cannot hold a corpus the agent never read. It would be a control that passes by disposition.
- **Widen the memo format until the gate accepts amounts.** The gate's money exemption already admits verbatim figures read this session; widening beyond that means admitting figures the session cannot trace, which is the fabrication the gate exists to catch.
- **Register nothing until the runner is on the Machine.** The four gates ran on every package delivered to date, unregistered. The registry exists so that fact is on paper; `unprobed` is the honest status for a live, unproven control.

## Accepted gaps (named, not hidden)

- The runner's home is decided (the firm's own Machine, upsized, root-owned; #2612, #2614) and not yet built. When it lands, the write path in this ADR is re-checked on the seat: the package must still arrive through the connector out of band of any agent turn.
- The four controls have no automated driver. Their probes HOLD. A green appears only when slice 5 ships a driver that plants a violation and observes the refusal.
- The gates audited every delivered package from a laptop. This ADR does not retroactively certify those deliveries; it records that the same code is the control going forward.
- The identifier gate's date rule can still refuse a memo date the session did read if canonicalisation drifts. That is an overlay defect when it happens, filed as such, never a reason to loosen the memo.

## Acceptance criteria

- [ ] `customer.yaml` on ashton-price authors exactly two routine-11 settings keys (`treatment_gap_flag_days`, `chronology_package_page_allowance_per_month`); pinned by `tests/customer-commitments.test.ts`. (Amended 2026-09-09: the document key is carried alongside for one release and the test allows both until the reprovision.)
- [ ] Four `unprobed` rows in `runtime-controls.yaml` with paired HOLD probes; conformance tests green.
- [ ] (runtime) A running-chronology memo in the slice-1 format lands on a pilot matter through the live gates; `crane_verify` id in the slice-1 PR.
- [ ] (runtime, slice 5) A planted violation in each runner gate is refused on the Machine; the rows move to `enforced`.

## Verification

`cd operator && python -m pytest bin/tests/test_runtime_control_conformance.py bin/tests/test_control_probes.py`; `npm run verify`; `operator/bin/control-probes.py --kind seat --seat pilot-smokeball` reports the four probes as HOLD.

## Amendment 2026-09-09: the package is metered in pages, and the cost limits are enforced per paid call

The forcing case was a delivered package that crossed no limit anything enforced: 3,312 pages, 88 percent of them scanned, and a cost several times what a package of that shape had been sized at. Four things were wrong at once, and each is now a mechanism.

**1. The unit is pages, not documents.** `chronology_package_document_allowance_per_month` becomes `chronology_package_page_allowance_per_month` (ashton-price 15,000; pilot-smokeball 40). A document is not a unit of work: the delivered packages ranged from a one-page bill to a several-hundred-page hospital chart, and what a package costs tracks its pages. The old key is **not** read as a fallback; a seat still carrying only it reads as unauthored and submits nothing, and the refusal names the new key. Both keys are authored for ONE release so an older broker restarting inside the rollout window still meters something, and the document key is removed after the reprovision.

**2. One debit rule.** A job debits the month's pages and cents when it **recorded cents**, in whatever state it ended, counted against the month the job was **created** in. The previous rule counted delivered jobs only, so a run that read thousands of pages and spent real money left no mark whenever it held or failed after the money had moved. Held-at-zero jobs are not debits: nothing was read and nothing was spent.

Keyed on creation, not on the month the cents landed. A month-of-charge key needs a ledger column that is not in the broker's `PROJECTION`, and `PROJECTION`'s shape is pinned by the overlay's `_MEDCHRON_JOBS_COLUMNS` this release, so the console could never read it: a job created on the 31st whose cents land on the 1st would be debited to the new month on the seat and shown in the old month on the console. The two surfaces disagreeing about the same month is the one thing this rule exists to prevent, and `created_at` is a column both surfaces already have. Moving to month-of-charge keying belongs with the next overlay bump.

**3. Private controls, required not defaulted.** The runner's firm config (engagements repo) gains `monthly_budget_usd`, `usd_per_scanned_page`, and `usd_per_audit_claim`, all required and all `> 0`. (A fourth, `single_matter_page_threshold`, shipped 2026-09-09 and was removed 2026-09-10 -- see the second amendment.) A firm.yaml predating them refuses to load rather than running unmetered, which is exactly the state routine 11 was in. They stay private because they are pricing-adjacent SMD posture; the seat still authors exactly one figure, the allowance.

**4. Enforcement moves from the stage boundary to the paid call, and the envelope can only lower the cap.** `cap = min(envelope, firm)` -- the envelope used to win outright, which made the firm's cap advisory. The cap and the monthly budget are re-read through the doorway's `before_request` hook before every paid call in live mode, and before every batch submission with the batch's projected cost through `before_batch`, so an overshoot is **bounded to one call or one batch** rather than one stage. Batch mode needs its own hook because a batch is one commitment: nothing checks between its items and the whole thing is billed, so the only place a limit can bind is before the submission. Both product seats run `batch_stages: []` today, which is why the claim has to be true of batch mode rather than conditional on nobody using it; the two page limits are still read once, before the first paid stage, from this matter's own extract output, so a matter too big to build costs nothing to refuse.

**The hold grammar.** Every hold reason begins with the name of the setting that held it and carries **no dollar figure**, the per-job cap's included. Two reasons: the seat's outbound content gates refuse an agent-drafted dollar amount on sight, so a reason carrying one cannot be relayed to the requester at all (live 2026-08-31, refused four times); and these strings are fixtures in a public repository. Page counts are allowed: they are the metered unit and the firm authored the allowance. Figures live in the run's state file, `log-<stage>.txt`, and the job's console row.

**Console.** The chronology page shows the month's pages against the authored allowance and the month's runner spend across all jobs, both by the debit rule above so the page and the seat cannot disagree about the same month. The monthly cost budget is deliberately **not** a denominator there: dollars stay off D1 (this ADR's private-posture clause), so the page shows spend without asserting a budget it does not hold.

**First items of the next `OVERLAY_REF` bump.** Three, all blocked on the same pin. The overlay's tool relays `allowance_remaining_documents` by name, so renaming it to `allowance_remaining_pages` comes first, and the compatibility key then comes out of the broker and the seat config. A `budget_cents` column on the console seam comes with it. So does **month-of-charge keying**: the debit rule keys on `created_at` today only because `_MEDCHRON_JOBS_COLUMNS` pins `PROJECTION`, and a month-of-charge column can be added to both surfaces in the same bump.

### Amended acceptance criteria

- [ ] The broker meters pages, applies one debit rule, and its allowance verb reports its `unit`; the runner refuses a firm config missing any of the four controls; every hold reason names its setting and carries no figure. Pinned by `runners/medchron/tests/test_limits.py`, `test_config_job.py`, `test_decisions_driver.py`, and `workspace_broker/tests/test_medchron_verbs.py`.
- [ ] (runtime) On a live seat, a matter over the CYCLE page allowance is held at zero spend, a matter merely larger than any former per-matter line runs, and a job whose cap is reached mid-stage stops within one paid call; `crane_verify` ids in the rollout PR.
- [ ] (runtime) The admin chronology page shows the month's pages against the authored allowance on `admin.smd.services`; `crane_verify` id.

## Amendment, 2026-09-10 (Captain): the per-matter page gate is removed; the cycle allowance is the only page limit

`single_matter_page_threshold` is deleted from the runner's closed key set, from
`Limits`, and from both firm configs. The firm buys a CYCLE page allowance and
spends it as it likes: one matter that consumes the whole cycle is a legitimate
use of what it bought, not a refusal.

**Why the 09-09 shape was wrong.** Four numbers expressed only two constraints.
`15,000 pages/cycle` and `monthly_budget_usd: 800` are the same ceiling (our cost
of goods) in two units; `3,000 pages/matter` and `per_job_cap_usd: 150` are the
same ceiling (one run's blast radius) in two units. So the per-matter page line
was the per-job cost cap wearing client-facing clothes, and because both were
sized off the same measured rate it refused matters the cycle allowance could
plainly afford. `limits.py` made this concrete by checking the per-matter line
BEFORE consulting the allowance at all -- a firm sitting on its full pool was
refused for a matter 3% over a per-matter line. Measured instance: matter 200454
is 3,098+ pages of PDF against a 3,000-page line, with all 15,000 pages unused.

**Why removing it does not expose margin.** `monthly_budget_usd` binds
independently, in DOLLARS, before every paid stage and every paid call. Dollars
are the honest unit: a scanned page measured about 4x a text page (6.3c vs 1.5c),
so pages are a client-facing approximation and the budget is the real control.
`per_job_cap_usd` is unchanged and stays INTERNAL -- an engineering limit on one
run, never a contract term quoted to the firm.

**Fail-loud, not fail-quiet.** The key stays out of the closed schema rather than
being accepted-and-ignored, so a firm.yaml still carrying it refuses to load. A
number a human believes is enforcing something, which silently is not, is the
failure this project keeps rediscovering.

**Paper.** Letter 38 (F-027) told the firm 15,000 pages/month AND a 3,000-page
single-matter threshold. Exhibit A row 11 must be re-cut (engagements open item
18). Relaxing the per-matter line is MORE generous, so it retracts no sent
commitment; it is given unilaterally and is never to be presented as a negotiated
concession.

**Still open after this amendment.** The window is still keyed to the CALENDAR
month; the Captain's decision (2026-09-10) is that it must key to the firm's
BILLING CYCLE. That change spans the broker's window computation, the console's
matching roll-up, and an `OVERLAY_REF` bump for the allowance response shape, and
it is inert until the firm actually starts a subscription (probed 2026-09-10:
`sub-op-ashton-price` is `provisioning`, `stripe_subscription_id` NULL).
