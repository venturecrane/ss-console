# Engagement Letter Chaser - Nudge Voice

The nudge is a short, warm, low-pressure reminder. Client-facing; whether it sends
or is held for review follows the firm's authored ceiling (SKILL.md, safety
invariant 4).

## Floor-clean by construction (READ THIS - #1878)

A graduated nudge to a rostered client is re-scanned by the content-sensitivity
floor (ADR 0031) before it delivers. The floor's `contract` category matches
`engagement letter`, `agreement`, and `sign` / `signed` / `signature` / `signing`;
`legal` matches `attorney`. A nudge body carrying any of those is HELD as a draft
even under an authored autonomous client-send, so the "auto-handle" commitment
does not deliver (issue #1878).

The nudge is authored to clear the floor without weakening the ask. The letter's
legal weight lives in the letter itself, which the client reads and completes;
the cover message only prompts them to complete and return it. Substitutions the
nudge body keeps (from `_shared-chase-voice.md`, "Floor-clean by construction"):

| Do not write (trips the floor)         | Write instead (floor-clean, same meaning)                            |
| -------------------------------------- | -------------------------------------------------------------------- |
| engagement letter / agreement          | the letter we sent to get started; the letter; the paperwork         |
| sign / signature / signing / sign here | complete and return; add your name and the date where the form shows |
| attorney                               | the team; the office; us                                             |

This governs the OUTBOUND nudge body only. Internal surfaces (memos, decision
lines, status headers) keep the precise words ("unsigned", "signed", "engagement
letter") - the floor scans what leaves the firm, not the matter file.

## The line

The nudge MAY: say the letter the firm sent to get started is ready and waiting to
be completed, point to where to complete and return it, offer to answer questions
**with the team**, and make it easy to ask for help.

The nudge MAY NOT: explain or define any term, fee, or obligation in the letter;
characterize what completing it commits them to; pressure or guilt; imply the
matter is already underway before the letter is completed.

## Hard rules

- No em dashes.
- No "just circling back," "just following up," "touching base."
- No legalese, no "execute the agreement."
- Short, warm, low-pressure. One clear next step.
- Signs in the firm's reviewer voice.

## Examples

**Good:**

> Hi <name>, a quick note that the letter we sent to get started on your matter is
> still waiting for you. When you have a moment, add your name and the date where
> the form shows and return it here: <link>. If anything in it raises a question,
> we are happy to set up a few minutes with the team to walk through it. Thanks.

**Bad - interprets a term (UPL):**

> The indemnification clause in section 4 just means you cover our costs if a third
> party sues, which is standard, so you're fine to sign.

(Explains and characterizes a clause - legal advice. Also carries floor trigger
words; a term explanation can never be floor-clean because the sensitivity is
real, not vocabulary.)

**Bad - implies work has started:**

> We've already started pulling things together on your matter, so we just need the
> signed letter to keep going.

(Implies representation before the engagement is in place; "signed" also trips the
floor.)
