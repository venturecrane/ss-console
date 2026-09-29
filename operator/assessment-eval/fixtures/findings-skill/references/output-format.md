# Output format - assessment findings draft

The findings draft has one job: present the operation clearly, by domain, every line anchored to the owner's own words, and stop at the X-ray. Structure below.

## The five observation domains

The same observation taxonomy the interview drives toward (`assessment-interview` references/coverage-model.md). You are **capturing observations only** - not mapping them to solutions, that is the human's at the close.

| Domain                | A finding here describes…                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------- |
| **process_design**    | A workflow that lives in someone's head / stalls when one person is unavailable.          |
| **tool_systems**      | A tool missing, or owned-but-underused; manual workarounds; double entry.                 |
| **data_visibility**   | Where the owner can't see the business in real time; numbers found out too late or never. |
| **customer_pipeline** | How leads are captured, followed up, converted - and where they slip.                     |
| **team_operations**   | Hiring, retention, onboarding, feedback - and where people problems go unaddressed.       |

## Document structure

```markdown
# Assessment findings - {Business name}

_Drafted from the assessment conversation on {date}. This is a working draft for the closing call._

## How {Business name} runs today

[2–4 sentences, plain language, reflecting back the shape of the business as the owner described it.
Owner's framing, not ours. No verdict.]

## What we saw, by area

### Process & how work flows (process_design)

- **{The observed reality, specific}.**
  > They said: "{verbatim owner quote}"
- **{Where it strains}.**
  > They said: "{verbatim owner quote}"

### Tools & systems (tool_systems)

[same shape; or:]

- _Not covered in this conversation._

### Visibility into the numbers (data_visibility)

…

### Customers & pipeline (customer_pipeline)

…

### Team & people (team_operations)

…

## Where this points

[A short, honest paragraph that names that there ARE addressable strains here and that the
next conversation is where we prioritize them together and lay out what to do. This is the
itch, not the scratch - it conveys solvability WITHOUT delivering the verdict, the ranking,
or the fix. See discipline.md.]
```

## Rules baked into the format

- **Every bullet carries its quote.** No anchor → cut the bullet, do not keep it on a guess.
- **Un-reached domains say so.** `_Not covered in this conversation._` - never a manufactured finding.
- **"How it runs today" is reflection, not judgment.** It proves we listened; it does not rate them.
- **"Where this points" withholds.** It is the only forward-looking section and it must not rank, prescribe, or dollarize - it names that strains are solvable and that the read happens with the human.

## customer.yaml assessment seed (proposed block)

The draft also seeds a structured record for the portal. The canonical `customer.yaml` schema is owned by the customer.yaml contract (ADR 0012/0019); this is the shape the findings draft proposes - wiring it into the schema is a downstream step, not this skill's to land.

```yaml
assessment:
  drafted_on: 'YYYY-MM-DD'
  source_transcript: '<assessment-session-id>'
  domains:
    process_design: { reached: true, findings: 2 }
    tool_systems: { reached: false, findings: 0 }
    data_visibility: { reached: true, findings: 1 }
    customer_pipeline: { reached: true, findings: 1 }
    team_operations: { reached: true, findings: 2 }
  status: draft_for_review # never auto-published; the human owns the read
```

The seed records **coverage and finding counts**, never the verdict - consistent with the withheld-read line.
