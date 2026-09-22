# Voice - retainer-hours-reconciler Slack post

The agent's output for this skill is an internal Slack post to the agency owner. Voice rules below govern that post. There is no client-facing output for this skill.

## Hard rules

1. **Tight.** The owner reads this Monday morning between meetings. Whole post should be skimmable in 30 seconds.
2. **Data first, prose second.** Lead with the numbers. Don't open with "Here's your weekly retainer report" - the owner knows what it is.
3. **No corporate fillers.** No "I hope you had a great weekend." No "Per our weekly cadence." Just the data.
4. **Active voice.** "Acme is at 110% projected EOM" not "Acme is projected to reach 110% by end of month."
5. **Short sentences.** Six-word sentences are fine. Eight-word sentences are fine. Twenty-word sentences read like AI.
6. **No emojis except the scan signal.** One emoji per bucket: 🔴 OVER_CRITICAL, 🟡 OVER_WARNING / UNDER_WARNING, ✅ BALANCED (only mentioned in summary), 🔴 UNDER_CRITICAL.
7. **No hedging on data.** "Projected" is fine (it IS a projection). "Maybe over" is not - the math is the math; the owner judges what to do about it.

## Soft rules

8. **Suggest, don't dictate.** "Worth a call." "Worth nudging the AM." Owner decides; agent gives the data + the option.
9. **Reference the SOW when it matters.** "Acme SOW caps at 40hrs/mo; they're at 44 projected." That's actionable. Just "they're over" is not.
10. **Trend-aware.** If Acme has been OVER for 3 weeks running, the post says so. "3rd consecutive week OVER - pattern, not a blip."
11. **No agency-jargon spaghetti.** No "burndown," no "velocity," no "sprint deltas." Plain language. The owner runs a service business, not a software team.

## Examples - good and bad

### Bad (over-engineered)

> Hi Scott! Here's your Q4 Week 19 retainer utilization report. As of Monday morning, the team has identified 3 clients that may require your attention regarding their hours pacing. Acme Co is currently trending toward over-utilization with projected end-of-month at 110% of SOW. Let me know if you'd like to discuss strategies...

### Good

> 🔴 Acme - 88% MTD, projected 110% EOM (SOW: 40hrs/mo)
> 3rd week OVER. Strategy hours leading. Worth a call.

### Bad (under-utilization, fluffy)

> Just wanted to flag that Beta Corp's utilization seems a bit on the lower side this month. They're currently sitting at around 35% of their contracted hours with a week remaining, which may indicate we could proactively engage with them on additional work or check-ins...

### Good

> 🔴 Beta Corp - 35% MTD, projected 38% EOM (SOW: 30hrs/mo)
> Under all month. Renewal risk. Schedule a check-in?

### Bad (over-acknowledging)

> ✅ Gamma Inc is doing great this week, tracking right at 70% of hours which is exactly where we want them to be at this point in the month. Keep up the good work team!

### Good

(omit entirely - BALANCED clients don't get individual lines. They're in the summary count: "12 clients BALANCED.")

## Sign-off

No sign-off. The post is a report, not a conversation.
