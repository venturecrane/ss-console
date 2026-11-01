---
title: Positioning & Voice
section: business
order: 4
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: How SMD talks to clients and prospects - the frame, the seven tone rules, the house style, and what the build enforces automatically.
sources:
  - label: CLAUDE.md - Tone & Positioning Standard
    href: CLAUDE.md
  - label: Positioning spine (marketing source of truth)
    href: docs/marketing/positioning-spine.md
  - label: Decision Stack (Decision #20)
    href: docs/adr/decision-stack.md
  - label: ADR 0004 - positioning guardrails
    href: docs/adr/0004-productized-operator-offering.md
  - label: ADR 0040 - Operator positioning (law-first)
    href: docs/adr/0040-operator-positioning-and-why-us.md
  - label: Voice and positioning guard tests
    href: tests/landing-page.test.ts
  - label: Forbidden-strings guard test
    href: tests/forbidden-strings.test.ts
---

Every word SMD sends a client or prospect follows the rules on this page: the client is the hero, we are the guide, and we never claim more than we know. Use it when you approve a client email, review an agent's draft, or ask an agent to write anything public.

## The frame: the client is the hero, we are the guide

The owner has the vision; we bring operational experience; we work it out together (`CLAUDE.md`, About This Venture). The value is enterprise operational discipline for businesses that have never had it, at a speed and price that fits their stage.

On the website the firm is "a software and AI solutions firm for small businesses," with the Operator as its flagship (`docs/marketing/positioning-spine.md`). AI is named as a capability, never as the firm's identity: no "AI-powered firm" branding (ADR 0004, positioning guardrails). As `CLAUDE.md` puts it, a chef is not hired for his knife, but he names the knife when it matters.

These rules apply to all external content (website, outreach, proposals, collateral, client emails) and to internal content that may become external copy.

## The seven tone rules

From `CLAUDE.md`, Tone & Positioning Standard.

**1. Objectives over problems.** Start from where the business is trying to go, not only what hurts. Do: "We start by understanding where you're trying to go, then figure out what's in the way." Don't: "We diagnose your top problems and fix them."

**2. Collaborative, not diagnostic.** We work alongside the owner, not audit them. Do: "We work alongside you." Don't: "We audit your operations," "We tell you what to fix."

**3. No fixed timeframes in marketing.** No published durations for the call, the build, training or support. Signed documents (an SOW, an invoice, an agreement) keep the timeframes that were agreed. Do: "We start with a conversation." Don't: "1-hour call," "2-week support window."

**4. No published dollar amounts.** The client sees a price in their proposal, never on a public page. The one exception is the self-serve Operator price on `/agent`, a deliberate, page-only exemption (ADR 0067, Decision #51).

**5. "Solution," not "systems," when positioning.** "Systems" sounds like more software to learn. "System" is fine for a specific tool ("data migration and system setup").

**6. "We," with one exception.** Always "we" and "our team," never "I" or "the consultant" (Decision #20). The one exception is the "Who We Are" section on the home page (`About.astro`), where Scott speaks in first person because SMD is a practitioner firm and the founder is the firm (added 2026-05-03). `tests/landing-page.test.ts` excludes that file from the "we" check for this reason. Only the Captain can reverse it.

**7. Never claim to know the prospect's business.** No copy that implies we already know what is wrong with a specific business, in first person ("I know what's wrong") or implied ("this is what your business needs"). We learn it in conversation (`CLAUDE.md`, rule 7).

## Further rules

- **The identity-marker rule (2026-05-03).** Words that describe a quality we aspire to ("magic," "artist," "creative") shape the voice but are never stated on the page. Show the work instead.
- **Never disparage people or software.** Both are capable; the problem is the gap between them. No "off the shelf," no AI-versus-human comparison (`docs/marketing/positioning-spine.md`, voice laws).
- **For the Operator with law firms:** it does paralegal and coordinator work, never independent legal judgment; never say "we don't do legal work"; do not open with "AI employee" (ADR 0040).
- **House style:** no em dashes; no "coming soon" on prospect or client pages (`CLAUDE.md`).
- **Nothing invented.** Any timeline, price, deliverable, name or promise shown to a client must come from something authored for that client. If it is missing, show nothing or "TBD in SOW." This is the no-fabrication policy, a P0 rule; see [Security & Trust](/admin/playbook/security-trust).

## What the build checks for you

Some of these rules are enforced automatically, so an agent's change that breaks them fails before it ships: `tests/forbidden-strings.test.ts` (banned phrases, em dashes, placeholder copy) and `tests/landing-page.test.ts` (no dollar amounts except `/agent`, the "we" voice, the locked home-page structure). The checks cover the website and the code. They do not read the emails you approve, so the rules above are yours to apply there.

## Related pages

- [Security & Trust](/admin/playbook/security-trust): the no-fabrication policy in full.
- [Business Model](/admin/playbook/business-model): the firm and its three offers.
- [Pricing & Economics](/admin/playbook/pricing-economics): the internal figures behind rule 4.
