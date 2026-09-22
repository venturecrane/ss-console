# Output format - status-report-assembler

The agent produces one report draft per client per run, written to `customer_notes/drafts/{client_slug}/status-{YYYY-MM-DD}.md`, plus a single summary Slack post to the agency's `client-status-drafts` channel listing all drafts produced.

## Per-client draft structure

The draft uses the client's preferred template if present at `customers/<smd-slug>/client-templates/<client-slug>.md`. Otherwise the default template below.

### Default template

```markdown
# {Client Name} - Week of {Mon DATE}

Hi {Client primary contact first name},

Week of {Mon DATE - 6 days} – {Mon DATE}:

## What shipped this week

{Per-PM-tool task summaries grouped by epic/category. Bullets, not paragraphs. Past-tense, active voice. Each item links to its source PM ticket via the resolver.}

## Results

{Per-channel metrics with WoW deltas. GA4 metrics, paid-platform metrics, CRM pipeline metrics - only the channels relevant to this client per their SOW. Format: "Metric: value (delta % WoW) [source]".}

## Blockers

{Items waiting on the client. Each: what's blocked + since when + specific next step requested. Omit section if no blockers.}

## Next week

{Concrete priorities from the agency's project plan. Items with confirmed start dates get parens with dates. Items pending owner approval get [TBD: description].}

## One thing

{Singular ask. Omit if no ask this week.}

{Sign-off - agency default or client-thread-matched}
```

## Per-section rules

### What shipped

- Group by epic/category, not chronologically. Owner scanning wants to see "what got moved" not a Tuesday-Wednesday-Thursday timeline.
- 2-6 bullets typical. If the team shipped 20 things, group them.
- Each bullet references the PM tool item - the rendered draft has hyperlinks the owner can verify.
- Past-tense, active voice. "Shipped the landing page redesign" not "Landing page was shipped."

### Results

- Only metrics the client cares about. Marketing-agency clients usually care about: sessions, conversions/leads, top performing content/ad, CPL/CPA if running paid.
- Every metric has source attribution in brackets: `(GA4, 7-day rolling)` or `(HubSpot, MTD)`.
- WoW deltas appear when reasonable (the agency has been running long enough to have a comparison week). Format: `+18%` or `-12%`.
- If a metric dropped, surface honestly with one line of context: "Sessions: 8,400 (-22% WoW per GA4) - week included Memorial Day, holiday-normalized drop."
- No invented attribution. If the agent can't explain a result, it doesn't.

### Blockers

- Each: artifact + waiting-since-date + next-step-ask. Format:
  > Awaiting Q3 budget confirmation since 5/8. We're holding campaign-2 launch.
- If the agency itself is the blocker (something the team owes), that does NOT go in the client-facing draft. It surfaces in the internal Slack alert instead - the client doesn't see the agency's internal misses.
- Omit the section entirely if there are no client-side blockers.

### Next week

- Each priority has provenance: "(Tuesday - confirmed)" if the agency's plan has a confirmed date, "(target Friday, contingent on Q3 budget)" if there's a dependency, "[TBD: content strategy doc - pending Scott review]" if the owner hasn't authored it yet.
- 3-5 items typical. Too many = doesn't read; too few = client wonders what they're paying for.
- No invented commitments. If the agent doesn't have evidence of the priority in the agency's plan, leave a `[TBD]` placeholder for the owner.

### One thing

- Singular ask. The client takes one action per report; otherwise it's noise.
- The ask is specific: "Confirm Q3 budget by Friday so we can launch campaign-2" not "Let us know your thoughts on next steps."
- Omit if no ask this week.

## Summary Slack post (one per run, internal)

```
*Status drafts ready - Week of {Mon DATE}*

Drafts written:
- {Client Name} - drafts/{client-slug}/status-{YYYY-MM-DD}.md ({word count})
- ...

Flagged for review:
- {Client Name}: voice-match uncertain - limited prior-thread history
- {Client Name}: result anomaly - sessions down 22% WoW, surfaced in draft

_Run finished {ISO timestamp} · skill version {hash}_
```

## What "no surprises" looks like

- Every metric is sourceable. The audit log shows the GA4 / HubSpot / Meta tool call that produced each number.
- Every shipped item is sourceable to a closed-state PM ticket. No "we worked on" - only "we shipped."
- Every blocker has a date attached. "Awaiting input" with no date reads as agency inattention.
- Every next-week item is either confirmed-plan, contingent-with-named-dependency, or `[TBD]` placeholder. Nothing in between.

## Per-client voice override

Per-client voice overrides at `customer.yaml: clients.{slug}.report_voice` allow specifying:

- Formality level (`formal` | `business-casual` | `casual`)
- Salutation pattern (`first-name` | `formal` | `none`)
- Sign-off override (per-client requested sign-off)

The agent honors overrides over defaults, then voice-matches against prior threads on top of that.
