# ADR 0088: The work SMD owes a client is a register, derived from the systems that already know, and closed only by a probe

Status: Accepted (Captain decision 2026-09-17), amended the same day

**Amendment, 2026-09-17.** As first built, the register carried two admin pages: a per-client panel on the client hub and a fleet-wide `/admin/obligations`. Both were deleted hours later on the Captain's instruction. He works in terminal sessions and does not open the admin console to look things up, so the pages answered a question nobody was going to ask there — and one of them shipped with no nav entry at all, which is how the mismatch surfaced. The register is read from the terminal (`.claude/bin/register list`). Nothing else changed: the table, the grounded capture, the reconciler, and the alerting into `cost_anomaly_alerts` are unaffected. The design error was mine, not the Captain's: "queryable and visible on demand" was read as "a web page" without asking where the reading happens.

Related: ADR 0075 (commitments pinned from a letter), ADR 0083 (output classes), migrations `0117_client_obligations.sql` / `0118_alert_source_obligation.sql`, `scripts/ci-reconcile-obligations.ts`, `.claude/bin/register`, Law 9 (done means the client can do it), Law 12 (a check that cannot fail has measured nothing), Law 14 (a program's report about the world is a claim)

## Context

SMD now implements and supports clients, and the work of keeping them served lives nowhere in particular. For Ashton & Price on the day this was written, all of the following were real obligations and all of them lived in a different place:

| Obligation                                            | Where it lived                                                         |
| ----------------------------------------------------- | ---------------------------------------------------------------------- |
| Smokeball task cleanup                                | correspondence letters 26/27/28, with the wrong blocker issue attached |
| Routine 11's per-cycle duty                           | an agent memory file                                                   |
| Smokeball consent expiring March 2027                 | a memory file with a hand-written refresh probe                        |
| A delivered chronology whose ledger row read `failed` | the ledger, saying the opposite of the truth                           |
| A pending vendor scope approval                       | a peer session's mission line                                          |
| Four unshipped fixes blocking delivery                | GitHub                                                                 |

Nine classes of work, one client, no way to ask what we owe. The Captain's requirement was explicit: a system he does not have to generate, update or maintain, that flows from the work agents already do and is queryable on demand.

The precedent for what happens without one is in this enterprise already. The cadence engine is a reminder queue with no forcing function; at the time of this decision it stood at **7 of 16 items overdue, one of them by 134 days**, while reporting itself perfectly healthy. A register that only accumulates is a register nobody reads by week three.

Research across four fields (MSP/PSA practice, legal docketing, agency retainer ops, and SRE control loops) converged on three controls, and all three are adopted below. Notably, **nothing in the literature describes agents maintaining a backlog of obligations owed to a client** — agents tracking their own execution steps is a forming convention, and this is not that. The design is therefore assembled from adjacent disciplines rather than adopted from one.

## Decision

**Every obligation SMD owes a client is a row in one D1 register. Rows are derived by a scheduled reconciler from the sources a machine can enumerate; a CLI covers only what no source exposes. A row reaches `verified` or `closed` only through a probe of the real surface performed by a recorded CI run.**

### 1. An obligation is not a task

The schema is a contract-obligation register, not a ticket table. A row carries a **window** (`window_start`/`window_end`) distinct from a **due date**, a closed `kind` vocabulary that makes the register reportable, and a pointer to the source that created it. Ticket shapes conflate all three, and a register that cannot separate "applies during this billing cycle" from "must be done by the 15th" cannot answer either question.

### 2. Derive first; capture only what cannot be derived

The first draft of this design made agents record obligations as a byproduct of their work, guarded by a Stop hook. Critique killed it: of the six obligations above, **only one** (the letter-stated cleanup) arrives by a route a write-signal hook can see. The rest are already visible to a machine.

So the reconciler imports each run from client-labelled GitHub issues, open `fleet_alert_state` conditions (a `connector_token_expiring:` row is a renewal we owe), and `operator_change_requests`. `.claude/bin/register` covers letters, which live as prose in a private repo and which no source enumerates.

A source that stops listing a row does **not** close it. Import proves an obligation is still stated; only a probe proves it discharged.

### 3. Capture is grounded mechanically, and only dated rows alarm

Extraction faithfulness tops out around 0.83 — roughly one statement in six is unsupported by its source. Prompting does not fix that, so the CLI requires a verbatim quote, normalizes both sides (NFKC, whitespace, smart punctuation, markdown), string-matches it against the source file, and **refuses** a row whose quote is not found. There is no `--force`. When the engagements repo is absent the CLI fails closed, on the Law 2 discriminator: "cannot evaluate" must never read as "permitted".

That grounds the **citation**, never the **interpretation**. A hallucinated sentence attached to a real quote still passes. Therefore only **dated** obligations generate alerts, and a due date requires its own `date_quote` containing that date (schema CHECK). The residual error stays in a list the Captain reads deliberately instead of arriving in his inbox.

### 4. There is no failure state

`medchron_ledger.py:58` made `failed` terminal with no transition out, and a chronology that had actually been delivered sat stranded in a state saying otherwise, recoverable only by hand. Here failure is **`parked`**, carrying its reason and a resume token, with edges back to every working state. `closed` is the only terminal state and keeps one outbound edge (a Captain reopen). The schema refuses a parked row with no reason, because a park without a reason is the same stranding under a friendlier name.

### 5. Closure is never self-certified

The party that owes an obligation does not get to declare it discharged. `verified` and `closed` require a `reconcile_run_id`, and that column is a **foreign key** into `reconcile_runs` — so a hand-written certification naming an invented run is rejected by the database, not by review. Writing the test for this established it: the design had assumed the forgery would land and need detecting afterwards.

What the key cannot stop is fabricating the run row too. Only CI can supply `workflow_run_url`, so a certification resting on a URL-less run is surfaced as `obligation_unverifiable`. That is the check that can still fail in the field, which is why it exists.

### 6. Evidence is probeable or attested, and the row says which

CI holds credentials for D1, R2, GitHub, AgentMail, MS Graph and the Operator runtime read. It holds **no Smokeball credential** — the seat owns that OAuth token. So a Smokeball filing is `attested`: CI verifies a receipt exists and is fresher than the row's last state change, and can never confirm the document itself.

Collapsing the two classes is the design's main failure mode, because an unprobeable surface would silently read as probed. They are therefore a column, and `cannot_evaluate` splits on them: a broken **probeable** surface is a broken control (exit 1); a stale **attestation** is a finding (exit 2).

### 7. The control that can catch its own silence

Every other control validates rows that exist. None of them notices rows that should exist and do not — the failure mode that produced the 134-day cadence item.

So each run prints a **coverage census**: artifacts per source class against obligations derived from each. A class holding artifacts and producing zero obligations raises `obligation_capture_gap`. And the reconciler prints **two denominators** (`total`, `universe`), because one count cannot distinguish "nothing to do" from "the selector is broken". An empty register exits 1 only once a prior run recorded rows (a high-water mark) — alarming from day one would teach the Captain to ignore it, which is the same failure by a different route.

### 8. Escalation reuses the surface that already exists

`src/lib/admin/fleet-alerts.ts:17-22` states the rule: a new alert source is a writer, not a new surface. Findings are written into `cost_anomaly_alerts` with `source = 'obligation'` (migration 0118 widens the CHECK by full-table rebuild, since SQLite cannot ALTER one), keyed `(entity_id, alert_date, driver)` with `driver = obligation:<id>:<condition>`. `workers/fleet-alerts/src/sink-notify.ts:57` then emails any non-cost row to the ops inbox, so Captain notification required no new code. `work_overdue` is **not** reused: it is live and means the seat's scheduler.

## Consequences

- The Captain reads the register from a terminal — `.claude/bin/register list [--client <slug>]` — or by asking an agent in the session he is already in. It is read-only there for the same reason a page would have been: a control that marked something done by hand is exactly the self-certified closure this ADR forbids.
- An agent adding an obligation writes to production D1 from its session. This is deliberate and its trade-off is named: no human reviews row content before it lands. The grounding gate, the journal, and the register's internal-only status are what make that acceptable; a bad row costs a correction, not a client.
- **Two obligation classes remain structurally uncapturable**: one created in a phone call, and correspondence read and acted on without any write. Neither is solved here, and neither should be reported as covered.
- Recurrence stores the rule and materializes one instance ahead, so "what is due" stays a query rather than a computation, and the register cannot go quiet because nobody ran a generator.

## Rejected

- **Extending `milestones` / `parking_lot` / `follow_ups`.** All three model a consulting engagement and hold **zero** production rows (verified live 2026-09-17); the live client key is `customer_configs`. Building on them would be building on something dead.
- **Hook-based capture as the primary path.** Blind to four of the five obligations it was meant to catch, and `Stop` fires for the main agent while this venture runs work through subagents.
- **Keying identity on the source quote.** Letters get revised and renumbered (57, 58 and 59 all landed on 2026-09-16); a quote-keyed row orphans itself on any edit and a restatement duplicates. Identity is `(customer_slug, kind, stable_key)`.
- **Borrowing an existing alert source** to avoid the table rebuild. `audit_integrity` is closest in spirit, but an obligation alert derives a different severity, links elsewhere, and means something different. Borrowing a source tag to dodge a migration is how an alert feed stops meaning anything.
- **A client-visible view.** The Captain's answer was explicit: this is ours, not a client tool. It also removes the fabrication risk entirely, since no register text is ever client-facing.
