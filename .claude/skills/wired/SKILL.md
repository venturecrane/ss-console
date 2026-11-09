---
name: wired
description: Convert an ask into a reachability contract before planning. Names the act the client performs, the terminal seam the change must land on, and every gate in between, so a feature cannot be reported done while a real client still cannot use it.
version: 1.0.0
scope: enterprise
owner: captain
status: stable
depends_on:
  mcp_tools:
    - crane_skill_invoked
    - crane_verify
---

# /wired - Reachability Contract

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "wired")`. This is non-blocking; if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Converts an ask into a contract that names what the client can do, where the change must land, and every gate in between.

Run it on the **ask**, before writing a plan and before `/critique`. Order matters: a plan written against a component-shaped ask gets critiqued as a good component build, and the critique never notices the feature is unreachable.

## Why this exists

The 2026-07-28 entitlement-control incident in ss-console. Four PRs, each individually honest, each defining "done" as the artifact it added. One of them wrote "Next slices, unbuilt and not implied here." Nobody lied. The artifacts summed to less than the feature, and the work was reported built, wired, and tested while a real client could not perform the act.

That is a definition problem, not a diligence problem. More care would not have caught it, because every PR met its own definition. This skill replaces the definition.

Three terms, used precisely:

- **Built** - the code exists and its own tests pass. The weakest of the three and the easiest to mistake for done, because it produces the most visible evidence.
- **Wired** - every gate between a real client's finger and the effect is open **on the deployment that client uses**. Not "would work once configured." Configured. Secrets and config authoring are part of the deliverable, not prerequisites belonging to someone else.
- **Tested** - someone performed the act **as the client, on the real deployment**, and observed the far end change. A green unit test against a fake token is not this.

## When it applies

Required when the effect is observable by someone outside the repo: a client, the Captain on a live surface, an Operator seat, a prospect on a marketing page.

Skipped for internal refactors, test-only changes, and documentation. Do not grow a gate table on a typo fix. An obstacle course that fires on everything gets routed around, and then it protects nothing.

## Workflow

1. **The sentence** - the act a named person performs, and what they observe.
2. **The terminal seam** - the last place the change must land for that sentence to be true.
3. **The gate chain** - every gate between the finger and the effect, enumerated backwards from the seam.
4. **The feasibility probe** - close-now yes or no per gate, before any code, escalating what cannot be closed.
5. **Emit the contract** - the chain becomes the issue's layer-tagged acceptance criteria.
6. **Hand off to planning** - plan against the contract, then `/critique` against the chain.
7. **Close against the contract** - each runtime row needs a `crane_verify` observation, not an artifact.

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

## Step 3: The gate chain

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
| 8   | Act performed as the client, far end observed | runtime          | agent            | probe         |       |

**Owner** is resolved per the ownership law: agents own execution, the Captain owns strategy, spend, and external commitments, the client owns their own posture (entitlements, autonomy tiers, risk acceptance). A gate whose owner is not the executing agent is not thereby out of scope. It is a gate you must surface, not silently skip.

## Step 4: The feasibility probe

This is the load-bearing step, and the one an agent will want to skip because it feels like stalling before the real work.

**Before writing any code**, probe every row marked `probe` and record closable-now yes or no. Probing costs minutes. It converts "would work once configured" into a known fact at plan time instead of at delivery time.

Then apply the stop clause, pre-registered here so it does not have to be remembered mid-flight:

> If any gate cannot be made true, stop and report which gate and why, **before building the gates that can**.

Not "build my slice and note the rest as unbuilt." That sentence is what turns an honest agent into a producer of honest slices that never reach the client. An unclosable gate is an escalation, and it is due at the top of the work, not at the end.

## Step 5: Emit the contract

Write the gate chain into the tracking issue as acceptance criteria, one per gate, each tagged with its layer:

```markdown
## Acceptance criteria

- [ ] (repo) Entitlement control renders for the Named Administrator role
- [ ] (runtime) Level write persists and survives re-projection
- [ ] (runtime) Authority authored on the customer's seat
- [ ] (runtime) Running Operator adopts the new level without a reprovision
- [ ] (runtime) Level raised and lowered as the client on the real seat, next action observed honoring it
```

The tags are load-bearing, not decoration. In ventures carrying the gate (ss-console today, via `.github/workflows/runtime-ac-proof.yml`), a PR that marks a `(runtime)` AC met without a `crane_verify` ID in the Evidence column is blocked. Elsewhere the tags still tell a reviewer which claims need an observation rather than a file:line.

That gate exists because the acceptance-criteria machinery otherwise certifies the author's own definition of done: `tick-acs-on-merge` parses the merging PR's own status table to tick the linked issue, and `unmet-ac-on-close` skips PR-driven closes. A slice that declares itself met is what closes the epic.

Record the contract's sentence and terminal seam in the issue body above the ACs, so the next agent to pick it up inherits the definition rather than re-deriving one.

## Step 6: Hand off to planning

State the contract, then write the plan against it, then run `/critique`. Critique carries one mandatory added dimension when a contract exists:

> Does this plan close every row of the gate chain, or does it silently defer some? Name any row the plan does not reach.

## Step 7: Close against the contract

At completion, each runtime row needs an observation, not an artifact. Run the act, capture the output, record it with `crane_verify`, and put the returned ID in the row's Proof cell. A row without one is not closed, and reporting it as closed is the failure this skill exists to prevent.

Handoffs and status reports lead with mission-level readiness: what the customer can now do, and what they still cannot. "End-to-end" is banned unless the end is customer-visible.

## Output

```
Sentence:      <act + observed outcome>
Terminal seam: <the last place the change must land>

<gate chain table>

Unclosable now: <rows, owners, and what would unblock them>  |  none
```

If any row is unclosable, that is the whole output. Stop there and escalate.
