# Output format - retainer-hours-reconciler Slack post

The agent writes ONE Slack message per run to the channel configured in `customer.yaml: connectors.slack.retainer_ops_channel`. The structure below is fixed - predictable scanning matters more than cleverness.

## Top-level structure

```
*Retainer reconciliation - Week of <Mon DATE>*

*Summary:* X over · Y under · Z balanced (N clients total)

*🔴 Needs attention this week*
<client lines per OVER_CRITICAL and UNDER_CRITICAL, see below>

*🟡 Watch*
<client lines per OVER_WARNING and UNDER_WARNING>

*Themes (optional)*
<3-bullet pattern surface, only if a real cross-client pattern exists>

_Run finished <ISO-8601 timestamp> · skill version <hash>_
```

## Per-client line format

A single line per client in the Needs Attention / Watch sections. Format:

```
{emoji} *{Client Name}* - {MTD %} MTD, projected {EOM %} EOM (SOW: {Xhrs/mo})
{Service-line breakdown if available} · {Consecutive-weeks-in-bucket count if > 1}
{One-line suggested action}
```

### Example (OVER_CRITICAL)

```
🔴 *Acme Co* - 88% MTD, projected 110% EOM (SOW: 40hrs/mo)
Strategy 25h · Production 13h · 3rd consecutive week OVER
Worth a call before Friday.
```

### Example (UNDER_CRITICAL)

```
🔴 *Beta Corp* - 35% MTD, projected 38% EOM (SOW: 30hrs/mo)
All-service light · 2nd consecutive week UNDER
Schedule check-in this week.
```

### Example (OVER_WARNING)

```
🟡 *Delta Studios* - 60% MTD, projected 96% EOM (SOW: 50hrs/mo)
Tight; on pace. No action needed unless trend continues.
```

## Rules

1. **Order within section:** OVER_CRITICAL first (most likely to cost money this week), UNDER_CRITICAL second (relationship risk), then WARNINGS. Within each bucket, alphabetical.
2. **BALANCED clients do NOT get individual lines.** They appear only in the summary count.
3. **Consecutive-weeks count only appears if > 1.** A single week in a bucket is normal; multiple is pattern.
4. **Service-line breakdown appears only if SOW specifies service caps.** Otherwise it's just "total hours."
5. **Suggested action is concrete or omitted.** "Worth a call" is concrete. "Consider engagement strategies" is not - omit if you have nothing specific.
6. **No `@<owner>` mention except on `🔴` items.** Don't fire alerts on yellow.
7. **Themes section is optional.** If there's no genuine cross-client pattern (e.g., "5 clients on Production are over - capacity issue?"), omit the section entirely. No padding.
8. **Footer line is non-negotiable.** Timestamp + skill version makes the post auditable.

## Slack formatting specifics

- Bold via `*asterisks*` (Slack convention).
- Italic via `_underscores_`.
- Channel mentions: only `<!here>` on OVER_CRITICAL when the owner has explicitly opted in (per customer.yaml). Default is no channel-wide mention; the agent tags individual users.
- Threads: every line is in the top-level post, NOT threaded. Owner replies open a thread.
- Links: invoice numbers + client names link to their respective tools (Harvest / Toggl / Float invoice records). The connector resolver provides URLs.

## What "no surprises" looks like

The owner should never wonder "where did this number come from?" Every number in the post is traceable to:

- A specific tool call (Harvest entry IDs, Toggl entry IDs, etc.) - logged in the audit trail
- The client's SOW with a specific Drive/Notion path

If a number requires interpretation (e.g., "projected EOM"), the line includes "(projected)" or similar. If a number is uncertain (e.g., a client's SOW reference is missing or contradicts itself), the agent surfaces the uncertainty in a Themes bullet rather than inventing a number.
