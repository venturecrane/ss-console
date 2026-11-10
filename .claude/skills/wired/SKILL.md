---
name: wired
description: Convert an ask into a reachability contract before planning. Names the act the client performs, the terminal seam the change must land on, and every gate in between, so a feature cannot be reported done while a real client still cannot use it.
version: 2.0.0
scope: venture:ss
owner: captain
status: stable
depends_on:
  mcp_tools:
    - crane_skill_invoked
    - crane_verify
---

# /wired - Reachability Contract (SMD Services)

> **Forked from crane-console on 2026-11-09.** This is SMD's own copy and no
> longer receives enterprise updates; improvements originate here.
> The fork point is commit `31c759f5`
> (crane `.agents/skills/wired/SKILL.md`, the last
> commit to touch it; unchanged at crane `origin/main` `55c766a6`); everything
> after it is ours. It lives in `.claude/skills/` for the same reason `/sos`,
> `/eos` and `/code-review` do: `crane ss` re-syncs `.claude/commands/` on every
> launch, and nothing in the crane toolchain writes here.
>
> **Load order, settled empirically 2026-11-09** (Claude Code 2.1.321, headless,
> in a scratch project holding a same-named skill in each place): a skill in the
> project's `.claude/skills/` wins over a same-named `.claude/commands/<name>.md`
> (crane's synced copy) and over `.agents/skills/<name>/`. Claude Code reads
> neither `.agents/skills/` nor `~/.agents/skills/` at all, and
> `~/.claude/commands/` does not exist on this machine. No `skillOverrides`
> entry is needed. If `/wired` ever runs crane's copy instead, follow the
> fallback ladder in `.claude/skills/sos/SKILL.md`.
>
> **Changes since the fork:**
>
> 1. **READY replaces Tested** as the definition of done, word for word with
>    CLAUDE.md "Done means the client can do it" and
>    `docs/doctrine/wired-contract.md` (doctrine #3900, 2026-11-09). A session
>    closes at READY and never stays open waiting for a real request.
> 2. **Step 3, what the client was told,** comes before the gate chain. A
>    feature built for a client starts from the client's letter or the
>    obligation register, cited by path, never pasted here.
> 3. Gate-table rows, the example ACs and Step 8 (close) are rewritten against
>    READY.

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "wired")`. This is non-blocking; if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Converts an ask into a contract that names what the client can do, where the change must land, and every gate in between.

Run it on the **ask**, before writing a plan and before `/critique`. Order matters: a plan written against a component-shaped ask gets critiqued as a good component build, and the critique never notices the feature is unreachable.

## Why this exists

The 2026-07-28 entitlement-control incident in ss-console. Four PRs, each individually honest, each defining "done" as the artifact it added. One of them wrote "Next slices, unbuilt and not implied here." Nobody lied. The artifacts summed to less than the feature, and the work was reported built, wired, and tested while a real client could not perform the act.

That is a definition problem, not a diligence problem. More care would not have caught it, because every PR met its own definition. This skill replaces the definition.

Three terms, used precisely:

- **Built** - the code exists and its own tests pass. The weakest of the three and the easiest to mistake for done, because it produces the most visible evidence.
- **Wired** - every gate between a real client's finger and the effect is open **on the deployment that client uses**. Not "would work once configured." Configured. Secrets and config authoring are part of the deliverable, not prerequisites belonging to someone else.
- **READY** - all five hold, each with evidence (a probe output, a `file:line`, or a `vfy_` id): (1) built and merged, its own tests passing; (2) rehearsed on real saved inputs, through the same code path the seat runs, against an answer key, at $0; (3) running on the seat, which reports the release that contains it; (4) ready to fire, every gate between the client's email and the job (config, secrets, switches, firm files) open on that seat by a probe of the seat; (5) watched by code, not by a session: the hourly work-health triage on smd-ops (`operator/ops-machine/bin/ops-triage`, `operator/bin/work-health.py`) reads job outcomes and flags a run that failed, held or stalled. A green unit test against a fake token is none of these.

When all five hold, the session writes its handoff and closes, however long it is until the first real request. A session never stays open waiting for a real request; the first real run is watched by the hourly triage. READY replaced "Tested" (the act performed as the client on the real seat) on 2026-11-09 (CLAUDE.md, "Done means the client can do it"; doctrine Law 9).

## When it applies

Required when the effect is observable by someone outside the repo: a client, the Captain on a live surface, an Operator seat, a prospect on a marketing page.

Skipped for internal refactors, test-only changes, and documentation. Do not grow a gate table on a typo fix. An obstacle course that fires on everything gets routed around, and then it protects nothing.

## Workflow

1. **The sentence** - the act a named person performs, and what they observe.
2. **The terminal seam** - the last place the change must land for that sentence to be true.
3. **What the client was told** - when the act serves a client, the promise, cited by path from the client's letter or the obligation register.
4. **The gate chain** - every gate between the finger and the effect, enumerated backwards from the seam.
5. **The feasibility probe** - close-now yes or no per gate, before any code, escalating what cannot be closed.
6. **Emit the contract** - the chain becomes the issue's layer-tagged acceptance criteria.
7. **Hand off to planning** - plan against the contract, then `/critique` against the chain.
8. **Close at READY** - each runtime row needs an observation, not an artifact; the session closes when all five READY conditions hold.

## Step 1: The sentence

Rewrite the ask as **an act a named person performs** and **an outcome they observe**. One sentence.

A component noun is not a deliverable. A component can be complete while the feature is dead, which is exactly how the incident happened.

| Rejected                        | Accepted                                                                                                                                       |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| "Build the entitlement control" | "A Named Administrator at the client firm can raise a routine's level and lower it back, and the Operator's next action honors the new level." |
| "Add the pause API"             | "A principal can pause a routine from the portal and the next scheduled run does not fire."                                                    |
| "Wire up webhook ingestion"     | "A countersignature lands and the engagement's status changes on the client's portal without anyone touching the admin console."               |

If you cannot write the sentence without naming a component, you do not yet know what the work is for. Ask.

## Step 2: The terminal seam

Name the **last place the change must land** for the sentence to be true.

Not a merged PR. Not a green test. Not a row in a ledger. Whoever takes the work owns the entire distance to that seam.

## Step 3: What the client was told

When the act serves a client, quote what the client was told **before** enumerating a single gate. A feature built for a client starts from the client's own words, not from the agent's reading of the ask (Captain, 2026-11-09).

Two sources, both read under Law 2 (the dossier first, `operator/customers/<slug>/dossier.md` in `venturecrane/engagements`):

- **The client's letter**, in `venturecrane/engagements` under `operator/customers/<slug>/`.
- **The obligation register**: `.claude/bin/register list --client <slug>`. A promise made in a letter is recorded there with a verbatim quote (ADR 0088).

**Quote it by citing, never by pasting.** Cite the file path and line, or the register row's key. Never paste the client's text into this repo, the contract issue, or a PR body: ss-console is public, and client material lives in `venturecrane/engagements` (Law 2, ADR 0081).

Then hold the sentence from Step 1 against the promise. If they differ, the promise wins: rewrite the sentence, or surface the mismatch to the Captain in one line. A promise is not a question: do not ask whether to build what the client was already told they would get.

A product feature that serves no specific client says so in one line and skips this step. Product work never names a client.

## Step 4: The gate chain

Enumerate every gate between the finger and the effect, **working backwards from the seam**.

Backwards is not a stylistic preference. Forward enumeration produces the artifacts you already planned to build. Backwards enumeration is what surfaces adoption, roles, secrets, and transport, which are the gates nobody plans because they are not code. Start at the effect and ask what must be true immediately before it, then repeat until you reach the client's finger.

Ask the venture where its state can live, and gate every layer that applies. The ss-console list, which is the worked example (its inverse is that repo's "Gone means gone" removal discipline):

| Layer                           | Runtime? | Typical gates                                |
| ------------------------------- | -------- | -------------------------------------------- |
| git (source, fixtures, docs)    | repo     | code exists, tests pass                      |
| Database projections            | runtime  | row written, projection re-synced            |
| Object storage (skills, config) | runtime  | object present at the key the runtime reads  |
| Persistent volumes              | runtime  | profile home, cron store, token on disk      |
| The running process             | runtime  | env loaded, config adopted, behavior changed |
| Monitoring                      | runtime  | heartbeat field, alert sink, error tracker   |
| External records                | runtime  | GitHub, mailbox, calendar, vendor dashboard  |

A layer that survives a redeploy is exactly the layer a merge cannot reach. Those are the rows that kill features.

Emit the chain as a table:

| #   | Gate                                          | Layer            | Owner            | Closable now? | Proof |
| --- | --------------------------------------------- | ---------------- | ---------------- | ------------- | ----- |
| 1   | Control renders for that role                 | repo             | agent            | yes           |       |
| 2   | Write persists                                | runtime (DB)     | agent            | yes           |       |
| 3   | Role granted on the real deployment           | runtime          | ?                | probe         |       |
| 4   | Authority authored for that customer          | runtime (config) | client / Captain | probe         |       |
| 5   | Secret deployed                               | runtime          | agent            | probe         |       |
| 6   | Transport reachable                           | runtime          | agent            | probe         |       |
| 7   | Running process adopts the change             | runtime          | agent            | probe         |       |
| 8   | Rehearsed at $0 on real saved inputs          | repo / workspace | agent            | yes           |       |
| 9   | Seat reports the release that contains it     | runtime          | agent            | probe         |       |
| 10  | Every gate to the job open, probed on the seat | runtime         | agent            | probe         |       |
| 11  | Outcome watched by the hourly triage code     | runtime          | agent            | probe         |       |

For a client job lane, add two rows the table above does not show:

- **Endings.** Every way a job can end (delivered, held, failed, stopped by a check), who resolves each one, and what the firm is told in its own thread. These are decided now, not discovered on the seat (doctrine "Process decisions (2026-11-10)").
- **Review before enforce.** When the change switches a lane to `enforce` for a client, `/code-review` runs first and its findings are fixed or carried as rows.

**Owner** is resolved per the ownership law: agents own execution, the Captain owns strategy, spend, and external commitments, the client owns their own posture (entitlements, autonomy tiers, risk acceptance). A gate whose owner is not the executing agent is not thereby out of scope. It is a gate you must surface, not silently skip.

## Step 5: The feasibility probe

This is the load-bearing step, and the one an agent will want to skip because it feels like stalling before the real work.

**Before writing any code**, probe every row marked `probe` and record closable-now yes or no. Probing costs minutes. It converts "would work once configured" into a known fact at plan time instead of at delivery time.

Then apply the stop clause, pre-registered here so it does not have to be remembered mid-flight:

> If any gate cannot be made true, stop and report which gate and why, **before building the gates that can**.

Not "build my slice and note the rest as unbuilt." That sentence is what turns an honest agent into a producer of honest slices that never reach the client. An unclosable gate is an escalation, and it is due at the top of the work, not at the end.

## Step 6: Emit the contract

Write the gate chain into the tracking issue as acceptance criteria, one per gate, each tagged with its layer:

```markdown
## Acceptance criteria

- [ ] (repo) Entitlement control renders for the Named Administrator role
- [ ] (runtime) Level write persists and survives re-projection
- [ ] (runtime) Authority authored on the customer's seat
- [ ] (runtime) Running Operator adopts the new level without a reprovision
- [ ] (repo) Level change rehearsed at $0 on saved inputs through the seat's code path, against an answer key
- [ ] (runtime) Seat reports the release that contains the change
- [ ] (runtime) Every gate between the client's request and the effect probed open on the seat
- [ ] (runtime) The hourly work-health triage reads the outcome and flags a failed, held or stalled run
```

The tags are load-bearing, not decoration. In ventures carrying the gate (ss-console today, via `.github/workflows/runtime-ac-proof.yml`), a PR that marks a `(runtime)` AC met without a `crane_verify` ID in the Evidence column is blocked. Elsewhere the tags still tell a reviewer which claims need an observation rather than a file:line.

That gate exists because the acceptance-criteria machinery otherwise certifies the author's own definition of done: `tick-acs-on-merge` parses the merging PR's own status table to tick the linked issue, and `unmet-ac-on-close` skips PR-driven closes. A slice that declares itself met is what closes the epic.

Record the contract's sentence and terminal seam in the issue body above the ACs, so the next agent to pick it up inherits the definition rather than re-deriving one.

## Step 7: Hand off to planning

State the contract, then write the plan against it, then run `/critique`. Critique carries one mandatory added dimension when a contract exists:

> Does this plan close every row of the gate chain, or does it silently defer some? Name any row the plan does not reach.

## Step 8: Close at READY

At completion, each runtime row needs an observation, not an artifact. Probe the seat, capture the output, record it with `crane_verify`, and put the returned ID in the row's Proof cell. A row without one is not closed, and reporting it as closed is the failure this skill exists to prevent.

The work is done when it is READY: all five conditions in "Three terms" hold, each with evidence. Then the session writes its handoff and closes. It does not stay open waiting for a real client request to arrive, and it does not ask for a paid proving run; the first real run is watched by the hourly triage code, not by a session.

Handoffs and status reports lead with mission-level readiness: what the customer can now do, and what they still cannot. "End-to-end" is banned unless the end is customer-visible.

## Output

```
Sentence:      <act + observed outcome>
Terminal seam: <the last place the change must land>

<gate chain table>

Unclosable now: <rows, owners, and what would unblock them>  |  none
```

If any row is unclosable, that is the whole output. Stop there and escalate.
