# Post-incident note: a persona name was removed four times and monitoring still paged on it

**Backfilled 2026-08-17 under #2391.** No note was written at the time. Every fact below is attributed to a source named in the Sources block; nothing is reconstructed.

**On naming.** The retired persona name is deliberately not spelled anywhere in this note. It was retired by repeated Captain directive, and `tests/forbidden-strings.test.ts:979-1027` makes any reintroduction a CI failure across `operator`, `src`, `tests`, `scripts`, `docs/design` and `docs/handbook`. `docs/runbooks` is not currently in that scan list, so writing it here would pass CI today and reintroduce exactly the resurfacing this incident is about. Throughout, it is "the retired name".

| Field                   | Value                                                                                                                                                                                                                                                 |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | 2026-07-02 to 2026-07-26                                                                                                                                                                                                                              |
| Seat / surface          | The persona display name, the D1 `customer_configs` projection, the repo slug and CI guard, and the Fly volume at `/opt/data`                                                                                                                         |
| Severity                | Not classified under ADR 0064 at the time, and this note does not assign one: the source records a monitoring page, not a client-visible outage or an out-of-entitlement action. It is recorded here as the canonical **removal-discipline** failure. |
| Detected by             | Monitoring paged on the retired slug, 12 days after the fourth removal                                                                                                                                                                                |
| Detection lag           | 12 days, as recorded, from the 2026-07-13 removal to the page                                                                                                                                                                                         |
| Detection to resolution | `not recorded`                                                                                                                                                                                                                                        |
| Client impact           | `not recorded`. The source does not state that a client saw the retired name.                                                                                                                                                                         |
| Status                  | Closed as an incident. The structural fixes are named below.                                                                                                                                                                                          |

**Sources.** The "Gone means gone (removal discipline)" section of `CLAUDE.md`, which is the incident's record of account; `tests/forbidden-strings.test.ts:967-1027` (the CI guard and its dated comments); `operator/bin/boot-smoke-test.sh:125-137` (step 6b, the no-unauthored-profile-homes assertion).

## What broke

A persona name was "removed" **four separate times**, and each completion report was honest about the layer it touched while wrong about the job:

1. **2026-07-02**: the display name (set to `Operator`, per the dated comment in `tests/forbidden-strings.test.ts:968-971`).
2. **2026-07-09**: the D1 projection.
3. **2026-07-13**: the repo slug plus every active reference, and the CI guard that bans the word, after "the word kept resurfacing in configs, fixtures, and agent conversation" (same comment block).
4. The fourth pass is described in `CLAUDE.md` as part of the same sequence; the source enumerates three dates for four removals, and the fourth is not separately dated. Recorded as stated rather than inferred.

The mechanism, in the source's own words: **state the repo materializes outlives the repo.** The Fly volume kept the retired slug's profile home and its frozen cron store, because `/opt/data` survives reprovision **by design**. Every git-layer removal was real and none of them reached the volume. Monitoring paged on it 12 days later.

`CLAUDE.md` classifies this as the same failure shape as built-but-not-wired: the claim was scoped to the artifact the agent could see, not to the mission. A removal is complete only when the artifact is absent from every layer it ever lived in, **proven by a probe of each runtime layer**, and never by the diff that deleted it from git.

## How it was detected

Monitoring, not review. `CLAUDE.md` records that the volume kept the retired slug's profile home and frozen cron store "until monitoring paged on it 12 days later". No CI guard could have caught it: the guards cover repo layers, and runtime layers are never covered by CI guards, which the source names as "the whole point".

## Timeline as recorded

| Time (UTC)              | Event                                                                                                   | Source                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 2026-07-02              | Display name removed (set to `Operator`)                                                                | `tests/forbidden-strings.test.ts:968-970`, CLAUDE.md |
| 2026-07-09              | D1 projection removed                                                                                   | CLAUDE.md                                            |
| 2026-07-13              | Repo slug plus every active reference renamed; CI guard added                                           | `tests/forbidden-strings.test.ts:970-972`, CLAUDE.md |
| 12 days after the above | Monitoring pages on the retired slug: the Fly volume still holds its profile home and frozen cron store | CLAUDE.md                                            |
| 2026-07-26              | End of the incident range as recorded in CLAUDE.md                                                      | CLAUDE.md                                            |

The exact page timestamp and the exact remediation timestamp are `not recorded`.

## What changed to prevent recurrence

**Landed, doctrine.** The "Gone means gone" rule in `CLAUDE.md` now states the layer inventory explicitly, so a removal cannot be scoped to the layer the agent happens to be looking at. The enumerated layers for this venture are: git (source, fixtures, docs), D1 projections (`customer_configs`, no auto-sync, #1308), R2 (skills, vaults, config), the Fly volume (`/opt/data`: profile homes, cron stores, tokens; survives reprovision by design), the running Machine (env, loaded config, `skills_list`), monitoring surfaces (heartbeat fields, alert sinks, Sentry), and external records (GitHub issues and PRs, mailboxes, calendars, vendor dashboards). Each runtime layer gets a **negative probe** recorded via `crane_verify`, and a completion report without those verify ids is a repo-layer claim and must be worded as one.

**Landed, repo layer.** `tests/forbidden-strings.test.ts` fails CI on any reintroduction of the retired name across six scan roots, excluding dated grading run logs (rewriting dated records would falsify them) and the test file itself (it must spell the pattern to ban it).

**Landed, runtime layer, and this is the durable one.** The source states the general preference: prefer a **structural fix that makes the layer converge on authored state** over a one-time sweep, because a sweep leaves the class alive. The template it names is the overlay#185 profile-home reconciler, paired with a boot assertion that the convergence held: `operator/bin/boot-smoke-test.sh:125-137`, step 6b, compares the profile homes present under `/opt/data/profiles` against the personas authored in `customer.yaml` and exits non-zero on drift in **either** direction (orphans and missing both fail).

**Open.** `docs/runbooks` is not in the forbidden-strings scan roots. That is noted at the top of this file and is not fixed here; changing the scan list is out of this note's scope and belongs in its own change.

## Shadow-firm scenario

Not in #2389's starting set. The shadow firm drives a running seat adversarially, and this class is a convergence question about persisted state; its continuous instrument is boot-smoke step 6b, which already runs at every boot.

## Ladder consequence

None. No routine's exposure was involved. The incident's bearing on the ladder is indirect and is stated in the instrument: a rung is claimed by an artifact, and the artifact has to come from the layer the client's behaviour actually depends on, which for a removal means a runtime probe rather than a diff.

## Not recorded

- The exact date and time monitoring paged, and the exact remediation time.
- Which of the four removals is the undated fourth.
- Whether any client-visible surface ever rendered the retired name.
- Whether other retired artifacts persisted on the same volume alongside this one.
