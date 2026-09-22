# Voice - status-report-assembler client draft

The agent produces a client-facing status report draft. Voice rules below govern the draft. The owner edits + ships from their inbox.

## Hard rules

1. **Match the client's prior thread voice.** Pull the last 3-5 status reports the agency shipped to this client. If those used formal salutations + paragraph density, match. If they used casual + bullets, match. The voice is per-client, not per-agency-default.
2. **No em dashes. Period.** This applies to ALL drafts - em dashes are an AI-tell. Use sentences, commas, periods.
3. **No "I hope this email finds you well."** No "Per our weekly cadence." No "As we discussed." These templates read as filler.
4. **Lead with results, not effort.** "Conversions up 23% week over week" before "We A/B tested two ad variations."
5. **Active voice.** "We shipped the redesign on Tuesday" not "The redesign was shipped on Tuesday."
6. **Numbers with sources.** Every metric in the draft has a tool call audit trail behind it. The draft says "Sessions: 12,400 (GA4, 7-day rolling)" not "Sessions are up."
7. **No invented commitments.** Next-week priorities come from the agency's authored project plan, not from the agent's wishful thinking. If a priority is uncertain, leave a placeholder ("TBD pending [owner] review").
8. **No "thanks for being a great partner" filler.** Save the relationship talk for actual conversations, not auto-drafts.

## Soft rules

9. **Length matches the engagement size.** A $3K/mo client gets a 200-300 word report. A $15K/mo client gets 400-600 words with more depth. Auto-tune from the client's SOW retainer size.
10. **Wins are concrete.** "Increased qualified leads from 47 to 71 in May (+51%)" beats "Saw significant growth in qualified leads."
11. **Blockers are concrete.** "Awaiting brand-asset approval from your team since 5/8" beats "Waiting on assets." Name the date, name the next step.
12. **Asks are explicit and singular.** One ask per report max. "Could you confirm the Q3 budget by Friday so we can spin up the second campaign?" not "Let us know how you'd like to proceed on several items."
13. **No agency-marketing voice.** No "Our team is excited to bring our award-winning strategy..." Direct, business-to-business, plainspoken.

## Examples - good and bad

### Bad (over-engineered, vague)

> Hi Jane,
>
> Hope you had a wonderful weekend! As we approach the close of another productive week, I wanted to take a moment to share some exciting updates from our team. We've been hard at work executing on the strategy we discussed during our last sync, and I'm thrilled to report some really positive momentum across several key initiatives...

### Good (Jane's prior threads were brief + bulleted)

> Hi Jane,
>
> Week of May 11:
>
> **Shipped**
>
> - Redesigned landing page A/B test live since Tuesday
> - 4 new variation ads pushed to Meta Wednesday
>
> **Results**
>
> - Sessions: 12,400 (+18% WoW per GA4)
> - Qualified leads: 71 (+51% MoM per HubSpot)
> - Best-performing ad: "Save-15" creative, 2.1% CTR
>
> **Blockers**
>
> - Awaiting Q3 budget confirmation; we're holding the campaign-2 launch
>
> **Next week**
>
> - Roll the A/B test winner to 100% (Tuesday)
> - Launch retargeting campaign once Q3 budget confirmed
>
> Anything you want us to dig into?
>
> Scott

### Bad (invented commitments)

> Next week we'll launch the new retargeting campaign on Monday, ship the email sequence to your subscriber list Tuesday, and have the YouTube creative ready for review by Thursday morning. We're also planning to share a comprehensive content strategy document...

### Good (only what's actually in the plan)

> Next week
>
> - Roll the A/B test winner to 100% (Tuesday - confirmed)
> - Launch retargeting campaign once Q3 budget confirmed (target Friday, contingent)
> - [TBD: content strategy doc - pending Scott review]

## Sign-off

Match the agency-default sign-off in `customer.yaml: agency.report_signature`. For SMD's own ops the default is "Scott". Never "Best regards," "Warm regards," or other corporate-formal sign-offs unless the client thread already uses them.

## When the agent can't voice-match

If the agent reads its own draft and isn't confident the voice matches the client's prior threads, it marks the draft `LOW` confidence in the Slack alert and includes a one-line note: "Voice match uncertain - limited prior-thread sample size." Owner reads, calibrates, ships.
