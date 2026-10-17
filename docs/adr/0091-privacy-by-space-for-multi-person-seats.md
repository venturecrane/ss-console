# ADR 0091: Privacy by space for multi-person seats

- **Status:** Accepted (2026-10-17)
- **Decider:** Captain ("private by default", #3264 Captain decision 1, 2026-10-17); enforcement design by the agent, revised by the 2026-10-17 critique (R1 to R7)
- **Closes for multi-person seats:** the within-customer per-user isolation that ADR 0007 (Out of scope, line 81) and ADR 0009 (Out of scope, line 78) left to "the multi-user role model"
- **Builds on:** ADR 0007 (per-customer Machine isolation), ADR 0009 (cross-Machine query prohibition), ADR 0016 (Honcho disposition), ADR 0071 (confirm ceiling), ADR 0090 (hosted-agent roles)
- **Related:** ADR 0092 (calendar and documents for hosted agents), [#3264](https://github.com/venturecrane/ss-console/issues/3264)
- **Scope:** any seat that authors a `people:` block in `customer.yaml`. Single-person seats, and Brokery's seat, behave as today.

## Context

The couples role (ADR 0090) puts two people on one seat. Each partner talks to the agent privately, on their own Telegram DM and their own approved email, and together in a shared Telegram group. The Captain's first decision was "private by default", with three spaces: shared, partner A and partner B (#3264). The acceptance criterion is a falsifier: a private DM disclosure from partner A cannot be surfaced by partner B through any channel, memory, search or digest, with B asking directly.

ADR 0007 and ADR 0009 isolate customers from each other. Both explicitly left isolation between users of one customer open: ADR 0007 calls per-user isolation within a Machine "a role-permission concern, not a multi-tenancy concern" (`docs/adr/0007-per-customer-machine-isolation.md:81`), and ADR 0009 defers within-customer role separation to the multi-user role model (`docs/adr/0009-cross-machine-query-prohibition.md:78`). That model was never built, because every seat until now served one person or one firm whose staff share the firm's data.

Research on 2026-10-17 found that nothing in the substrate separates two people today:

1. **Hermes native memory is one store per profile and identity-blind.** Disabling the `memory` toolset alone still injects `MEMORY.md` into the system prompt (Hermes at pinned ref `345cd2b`: `agent_init.py:1243-1265`, `system_prompt.py:461-469`). Turning it off needs `memory.memory_enabled: false` and `memory.user_profile_enabled: false` (`config_defaults.py:1215-1229`), and the overlay passes `customer.yaml` memory settings through (`hermes-smd-overlay` `translate.py:1147`).
2. **The record-store fence was open on Telegram and cron turns.** `_viewer` returns None outside an email turn, and None passes everything (`hermes-smd-overlay` `plugins/hermes-smd-record-store/__init__.py:212-224`, `record_store.py:285-286`).
3. **Approvals were unbound.** One pending send per machine, and a yes from any allowlisted user counted (`pending_send.py:75-80,181`, `approval.py:96-120`).
4. **A plugin can tell a DM from a group and identify the sender.** Hermes exposes `HERMES_SESSION_CHAT_TYPE` and `HERMES_SESSION_USER_ID` through its session context (`gateway/session_context.py:33-48,173` at `345cd2b`).
5. **Peer memory keys on `sender_id`,** so one partner reaching the agent by Telegram and by email is two people to it (`hermes-smd-overlay` `plugins/hermes-smd-peer-memory/__init__.py:6-8`).

Several defects found by the same research were fixed before this record and are cited as shipped, not proposed: the record-store path fence (overlay [#464](https://github.com/venturecrane/hermes-smd-overlay/pull/464), "a record is never read by path on a seat that keeps records", and [#466](https://github.com/venturecrane/hermes-smd-overlay/pull/466), "no file tool writes on a seat that keeps records"); approvals bound to their requester (overlay [#465](https://github.com/venturecrane/hermes-smd-overlay/pull/465), "bind each held send to the person and chat that raised it"); and the Telegram allowlist key, which the seat never read (overlay [#463](https://github.com/venturecrane/hermes-smd-overlay/pull/463) and ss-console [#3271](https://github.com/venturecrane/ss-console/pull/3271), phase 1 of 3, pinned to seats by ss-console [#3273](https://github.com/venturecrane/ss-console/pull/3273)).

## Decision

**Privacy on a multi-person seat is by space. Every turn resolves exactly one space, structurally and never from the prompt, and every read and write path is filtered by the spaces that turn may see.**

### 1. Spaces and who sees them

A seat that authors `people:` has the space `shared` and one space `person:<id>` per person. The `people:` block maps each person's Telegram id and email addresses to one person id, which also fixes peer memory seeing one partner as two people.

- A turn in `person:A` reads `shared` and `person:A`.
- A turn in `shared` reads `shared` only.
- No turn reads another person's space. There is no administrator override inside the agent.

### 2. How a turn's space is resolved

The resolver is structural. Its inputs are session facts the model cannot write:

| Turn arrives as                                           | Resolved space                                                                     |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Telegram DM (`HERMES_SESSION_CHAT_TYPE=dm`)               | `person:<id>` for the `HERMES_SESSION_USER_ID` mapped in `people:`                 |
| Telegram group whose chat id is the authored shared group | `shared`                                                                           |
| Email                                                     | `person:<id>` for the verified `SESSION_INBOUND_ORIGIN` sender mapped in `people:` |
| Cron run                                                  | the routine's authored `space`                                                     |

Anything else resolves no space. On a seat that authors `people:`, a turn with no resolved space **fails closed**: it reads nothing private and writes nothing (R3). This applies only to seats that author `people:`; Brokery's scheduled-wake semantics do not change.

### 3. Enforcement points

1. **Native Hermes memory is off.** `memory.memory_enabled: false`, `memory.user_profile_enabled: false`, and the `memory` toolset in `disabled_toolsets`. All three, because the toolset switch alone still injects `MEMORY.md`. `session_search` stays off (`translate.py:1252`), since a transcript search crosses every space.
2. **Space-keyed memory replaces it.** `hermes-smd-peer-memory` (D1, server-side attribution, `__init__.py:124-206`) gains a `space` key and a `remember` tool. Its `pre_llm_call` hook injects only the spaces the turn may read.
3. **The record store takes the space.** Records carry `owner: <person>|shared`; the fence filters by the turn's readable spaces; the viewer is resolved on Telegram turns as well as email turns.
4. **A write from a private turn to anything shared is a pending act (R1).** That covers the shared calendar, the shared Drive folder and Docs, a shared record or list, and a message to the group. The partner who asked sees the exact text that will land and confirms it before it does. The falsifier is a fact from A's DM that must not reach the shared calendar or a shared Doc, and through them B.
5. **The couples persona has no shell, file or delegation tools (R2).** `code_execution` is authored non-autonomous, which drops `terminal`, `code_execution` and `delegation` (`translate.py:1269-1272`), and the `file` toolset is disabled. A model that cannot open a path cannot read a space's files by path. On seats that keep those tools (Brokery), the path fence of overlay #464/#466 is the guard, and it becomes an allowlist of readable roots that also covers cron output (`~/.hermes/cron/output/`), session transcripts and logs.
6. **Approvals are bound to requester and chat** (shipped, overlay #465). A yes counts only from the person who raised the pending act, in the chat where it was raised. A yes in the group never approves a send raised in a DM, and the reverse (R7). A change to joint settings, such as check-in topics or the other partner's settings, is a two-person pending act (#3264, Captain decision 4).
7. **Cron delivery names a space.** A routine authors `deliver: person:<id>|shared` and a `space`. The materializer resolves the target to the authored Telegram chat and binds the run's space (`hermes-smd-overlay` `bootstrap/cron_materialize.py:307-320`; Hermes `scheduler_delivery.py:599-622`). A nudge to A is composed in A's space and lands only in A's DM.
8. **Portal views are filtered by the signed-in person's space (R7).** The portal is another read path and follows the same rule.
9. **The group answers only `people:` senders (R7),** and says so when anyone else is present in the group.

### 4. What leaves the seat

Private-space artifacts never leave the seat toward the other partner (R5). A gift list or a surprise plan stays in the record store and is shown in a DM only. A calendar invite for a private item goes only to its owner, and only when the item is meant to appear on a calendar at all (ADR 0092). If a couple later connects a Google account (the optional upgrade in ADR 0092), Google holds shared items only, because content in Google lives in the connecting partner's account and that partner could read it there. The persona says so if asked.

### 5. Telegram identity is authored, not appended at runtime (R4)

A Telegram id joins `people:` only through the reviewed `customer.yaml` change that onboarding produces (ADR 0090 decision 4). A deep-link token is single-use, short-lived and bound to one person; the bind writes the id through the generator PR and the seat restarts. Until both partners are bound, the seat boots with an SMD sentinel id on the allowlist, which satisfies the boot guard against an empty allowlist (`translate.py:1010-1016`). The allowlist key migration ships in three phases (accept both forms, migrate the yaml, reject the old key), so that a `customer.yaml` published to R2 on merge never reaches an image that cannot read it.

## Consequences

- ADR 0007 and ADR 0009 stand unchanged for isolation between customers. Within a customer, a seat that authors `people:` now has per-person isolation by construction. A firm seat whose staff share the firm's data does not author `people:` and is unaffected.
- Each enforcement point ships with a unit falsifier: partner B asks for partner A's DM fact in B's DM, in the group, through a record read, a `read_file` path, a terminal command and a cron digest, and each must refuse. The live acceptance on the real seat, as each partner, is recorded with `crane_verify` (#3264).
- The couples persona loses native memory's convenience and the shell. That is the price of a guarantee that does not depend on the model choosing to keep a secret.
- ss-console's validator and contracts gain `people`, `space`, `deliver` and the Telegram group keys, and the overlay's mirrored fixture is regenerated and copied in the same change.
- The confirm-of-exact-text gate (R1) adds a step to every private-to-shared write. Real use will show whether that is too much friction; the Captain's confirmation rules are explicitly "to be refined from real use" (#3264, Captain decision 4).

## Rejected alternatives

- **Two seats per couple, one per partner, plus a third for the shared space.** Isolation would be free, but the shared calendar, lists and the group conversation would need a cross-Machine data path, which ADR 0009 prohibits.
- **Persona instructions alone ("never repeat what one partner told you").** A prompt is not an enforcement point. Native memory and session search would still hold both partners' words in one store the model reads.
- **Disabling the `memory` toolset without the config flags.** It still injects `MEMORY.md` (`agent_init.py:1243-1265`).
- **Resolving the space from what the model says or from message content.** Anything the model or the sender can write can be forged. The resolver reads only session facts and authored config.
- **Failing closed on every viewer-less turn fleet-wide.** It would break Brokery's scheduled wakes, a live seat, for no gain on a seat with one principal (R3).
- **Appending Telegram ids at runtime from the deep link.** It would let a runtime path widen the allowlist outside reviewed config (R4).
- **Mirroring private items to Google for convenience.** The owner partner could read them in their own account (R5).
