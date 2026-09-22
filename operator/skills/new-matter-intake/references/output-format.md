# New Matter Intake - Output Format

Two output shapes. The conflict-check result decides which. Both are internal artifacts the reviewer reads; the acknowledgment inside is the only client-facing text, and it is a draft.

## Shape A - Intake packet (conflict check clear)

```markdown
# New Matter Intake - <prospect name> - YYYY-MM-DD

**Source:** intake email | web-form | manual
**Conflict check:** CLEAR (parties checked: <list>)
**Practice area:** <area> | two: <a/b>, human to confirm | outside authored areas
**Returning contact:** yes (Smokeball contact <id>) | no

## Matter draft (for a human to create in Smokeball)

- **Client:** <name>, <contact>
- **Other parties:** <list or none>
- **Situation (their words):** "<quoted>"
- **Practice area:** <area>
- **Referral source:** <source or omit>

## Internal flags

- <statute-sensitive - verify deadline: ...> | _(none)_

## Acknowledgment (DRAFT - reviewer sends)

> <plain-text acknowledgment body, per voice.md>

## Internal log (create_memo body)

> New inquiry received <date>; matter drafted; conflict check clear; acknowledgment drafted for review.
```

## Shape B - CONFLICT-HOLD (any hit)

```markdown
# ⛔ CONFLICT HOLD - <prospect name> - YYYY-MM-DD

**The consult/engagement chain is halted pending human conflict clearance.**

## Possible conflict

- **Party:** <name>
- **Match:** Smokeball contact <id> | party on matter <id>
- **Why surfaced:** exact match | partial match (<detail>)

## Captured (held, not actioned)

- **Client:** <name>, <contact>
- **Situation (their words):** "<quoted>"
- **All parties checked:** <list>

## Acknowledgment (DRAFT - receipt only, reviewer sends)

> <neutral receipt; confirms the inquiry arrived; promises nothing; per voice.md>

## Routed to

<responsible attorney / human for conflict clearance>
```

## Rules

1. **CONFLICT-HOLD never contains a consult time, an engagement step, or any "we can help" language.** It is a stop.
2. **The acknowledgment is always a blockquote (`>`),** the only client-facing text, always a draft.
3. **No legal characterization anywhere** - not in the matter draft, not in the flags, not in the log.
4. **`create_matter` appears nowhere** - the matter draft is for a human to create.
5. Predictable structure over cleverness; the reviewer scans these daily. The `⛔` glyph is the only emoji, a scan signal for a hold.
6. **Check-unavailable hold.** When the conflict check could not run (the practice-management tool errored - a 401, a timeout, an unconfigured connector), use **Shape B** with the heading `# ⛔ CONFLICT HOLD (CHECK UNAVAILABLE) - <prospect> - YYYY-MM-DD`. In place of "Possible conflict," write a **Conflict check** section: `**Result:** UNAVAILABLE - could not run (<what failed>)` and `**Parties not yet checked:** <list>`. The acknowledgment is the same neutral receipt-only draft. This is a procedural hold, not a party-match hold: a check that did not run is never reported as CLEAR.
