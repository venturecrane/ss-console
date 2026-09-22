# Categorization rubric - retainer-hours-reconciler

How the agent assigns each active retainer client to a bucket. The thresholds below are defaults; per-customer overrides may live in `customers/<slug>/customer.yaml` under `skills.retainer-hours-reconciler.thresholds`.

## Inputs per client

The agent computes per client:

- `actual_hours_mtd` - total hours logged in the current month against this client across all enabled time-tracking tools (Harvest / Toggl / Float)
- `sow_hours_monthly` - the client's contracted monthly retainer hours from the current signed SOW
- `business_days_elapsed_in_month` - count of business days from month-start through today
- `business_days_in_month` - total business days in the current month
- `mtd_percent = actual_hours_mtd / sow_hours_monthly`
- `pace_factor = business_days_elapsed_in_month / business_days_in_month`
- `projected_eom_percent = mtd_percent / pace_factor` (linear extrapolation)

Per service line if SOW specifies caps:

- `service_line_actual` / `service_line_cap` / `service_line_projected_eom`

## Bucket thresholds

| Bucket           | Condition                                        |
| ---------------- | ------------------------------------------------ |
| `OVER_CRITICAL`  | `projected_eom_percent >= 1.10` (110% or higher) |
| `OVER_WARNING`   | `0.95 <= projected_eom_percent < 1.10`           |
| `BALANCED`       | `0.65 <= projected_eom_percent < 0.95`           |
| `UNDER_WARNING`  | `0.40 <= projected_eom_percent < 0.65`           |
| `UNDER_CRITICAL` | `projected_eom_percent < 0.40`                   |

## Bucket-promotion rules (override the basic threshold)

These promote a bucket TO MORE SEVERE based on context. Bucket is never demoted by these.

1. **Consecutive-week pattern.** If a client has been in OVER_WARNING for 2+ consecutive weeks, the agent promotes to OVER_CRITICAL for this week's post. Same logic for UNDER_WARNING → UNDER_CRITICAL.

2. **Service-line over while total is balanced.** If a single service line is at > 130% of its cap while total is BALANCED, promote to OVER_WARNING and surface the service-line breakdown. This catches "production was supposed to be 10hrs but we've spent 25hrs because the project is bigger than scoped."

3. **Mid-month spike.** If the projection jumped > 20 percentage points week-over-week (e.g., last week projected 75%, this week projects 110%), surface the jump in the per-client line: "WoW shift from 75% to 110%." Promote one bucket if not already CRITICAL.

4. **Renewal proximity.** If the client's SOW renewal is within 60 days AND they're UNDER_WARNING or worse, promote to UNDER_CRITICAL. UNDER + renewal-coming is a serious churn risk.

## Bucket-demotion rules (override toward less severe)

These demote based on context - used sparingly.

1. **New client (first 60 days).** If the client signed an SOW within the last 60 days, the agent applies a wider tolerance: thresholds shift by 15 percentage points wider in both directions. (New engagements have ramp variability that doesn't predict month 3.)

2. **Holiday-shortened month.** If the month includes 3+ holidays (e.g., December US), the agent uses `business_days_in_month` excluding holidays per the configured holiday calendar. This is a calculation refinement, not a bucket shift - but means pace is naturally adjusted.

## Service-line allocation rules

For SOWs that specify service-line caps (e.g., "10 hrs strategy + 30 hrs production"), the agent computes per-service-line MTD and projected EOM independently. Surface in the per-client line if:

- Any service line is > 130% of its cap (regardless of total)
- Total is balanced but one line is > 100% AND another is < 40% (scope misalignment, not size)

If the SOW has no service-line caps, the agent uses total only. Service-line breakdown line is omitted.

## Tie-breakers + edge cases

- **No SOW found.** If the client is active in time tracking but the agent can't find a SOW (Drive path missing, Notion link broken, etc.), the client appears in a "SOW lookup failed" bullet at the bottom of the post - NOT in any bucket. The owner needs to fix the SOW reference before the agent can grade utilization.

- **SOW shows 0 hours.** Some "non-retainer" client engagements are time-tracked even though they're not retained. Filter these out: if `sow_hours_monthly == 0`, exclude from the report entirely.

- **Multiple SOWs per client.** If a client has overlapping SOWs (e.g., main retainer + a project SOW), the agent sums the caps. The owner's responsibility to flag if that's wrong; the agent uses what's in the SOW store.

- **Mid-month SOW change.** If the SOW changed during the current month, the agent uses the most recent SOW for the bucket math but adds a note: "SOW updated <date> - pre-change utilization not retroactively adjusted."

## Calibration baseline

The first 3 weeks of grading runs against synthetic fixtures should produce these rough distributions for a "healthy agency":

- OVER_CRITICAL: ~10% of clients
- OVER_WARNING: ~20%
- BALANCED: ~50%
- UNDER_WARNING: ~15%
- UNDER_CRITICAL: ~5%

If the agent's output skews materially differently (e.g., 40% OVER_CRITICAL), the rubric thresholds need tightening (raise the 1.10 threshold to 1.15, etc.). Per-customer thresholds can be tuned in `customer.yaml`.
