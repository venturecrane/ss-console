---
title: Positioning & Voice
section: business
order: 4
owner: captain
review_every: 180
summary: The seven tone rules, the identity-marker rule, and the client-is-hero / we-are-guide frame that govern all client-facing language
sources:
  - label: CLAUDE.md - Tone & Positioning Standard
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: Decision Stack (Decision #20)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md
---

## The frame: client is the hero, we are the guide

SMD positions the client as the hero and the firm as the guide (CLAUDE.md, Positioning). The owner has the vision; we have operational experience; we figure it out together. The value is enterprise operational discipline applied to businesses that have never had access to it, delivered at speed and pricing that works for their stage.

The rules below apply to **all external-facing content** - website copy, outreach, proposals, collateral, and any client-facing language - and to internal content that may inform external copy (one-liners, scripts). They are the firm's voice. The no-fabrication enforcement that backs them in code is owned by `/admin/playbook/security-trust`; do not re-narrate it here.

## The seven tone rules

From CLAUDE.md, Tone & Positioning Standard.

### 1. Objectives over problems

Frame engagements around understanding business objectives, not just diagnosing problems. The owner often knows the pain but has not articulated the goal. Part of our value is helping them discover the real objective through conversation.

- Do: "We start by understanding where you're trying to go, then figure out what's in the way."
- Don't: "We diagnose your top problems and fix them."

### 2. Collaborative, not diagnostic

We are a peer working alongside the owner, not an expert arriving to audit them.

- Do: "We work alongside you," "Let's figure out what needs to change," "together."
- Don't: "We audit your operations," "We tell you what to fix."

### 3. No fixed timeframes in marketing

Timeframes are scoped per engagement, like pricing. Do not publish specific durations for any phase - the call, implementation, training, or support - in marketing content. This rule applies to marketing content only; signed contractual documents (SOW PDFs, invoices, countersigned agreements) keep the specific timeframes that were the product of the conversation.

- Do: "We start with a conversation," "Hands-on training with your team."
- Don't: "1-hour call," "10-day sprint," "2-week support window."

### 4. No published dollar amounts

No dollar amounts appear on the website or in marketing materials. The client sees a project price in their proposal, never on a public page. The figures themselves live in `/admin/playbook/pricing-economics` (internal).

### 5. "Solution" not "systems" in marketing contexts

"Systems" implies software and one more thing to learn. Not all solutions are software; sometimes it is a better process, a clearer role, or a simpler workflow. Use "solution" in positioning contexts. "System" is fine when referring to a specific literal tool (e.g. "data migration and system setup").

- Do: "Build the right solution," "the right solution to get you there."
- Don't: "Build better systems," "Your systems should keep up."

### 6. "We" voice, with the practitioner-firm exception

Always "we" / "our team." Never "I" / "the consultant" (Decision #20). This removes the "what if you're sick" objection and positions the engagement as a capable team, not a solo operator.

The one exception (added 2026-05-03) is the marketing home page's "Who We Are" / About section, where Scott speaks in first person. SMD is positioned as a practitioner firm - like a lawyer's office or a craftsman's shop - where the founder *is* the firm and there is a real team behind him. Forced "we" voice in the founder bio reads insincere, so first person is the only sincere voice there. The voice-standard test in `tests/landing-page.test.ts` excludes `About.astro` for this reason. Do not rewrite About to "we" voice without Captain explicitly reversing this call.

### 7. No claim to know the prospect's business

We do not write copy that implies pre-knowledge of a specific prospect's business. We are collaborators who learn the situation through conversation, not diagnosticians who arrive with answers. This covers both first-person ("I know what's wrong with your business") and implied ("This is what your business needs"). See `feedback_no_pretend_to_know_business.md`.

## The identity-marker rule

Added 2026-05-03. Words that describe an aspirational self-quality - Captain's examples are "magic," "artist," "creative" - must **shape** the voice without being **stated** on the page. Stating them reads as overclaim or self-flattery. Convey wonder through concrete language and what the work does, not by calling it magic. Convey creativity by showing it, not by calling it artistry. The rule applies to all marketing surfaces, not just About.

## Where this lands in code

The firm-level voice and the Operator SKU share one standard: Operator copy follows the same anti-fabrication and positioning rules as scope-based engagement surfaces (ADR 0004, positioning guardrails). No "AI-powered firm" branding - the firm is positioned as solutions consulting with AI & automation as a named capability, not as an identity. As CLAUDE.md puts it: a chef is not hired for his knife, but he names the knife when it matters.

The hard enforcement - the Pattern A / Pattern B no-fabrication policy, the forbidden-strings test, and the merge gates that block fabricated client-facing content - is documented in `/admin/playbook/security-trust`.

## Related pages

- `/admin/playbook/security-trust` - the no-fabrication (Pattern A/B) enforcement that backs these rules in code.
- `/admin/playbook/business-model` - the firm, the three front doors, and the solution taxonomy this voice describes.
- `/admin/playbook/pricing-economics` - the internal figures behind the no-published-dollar-amounts rule.
