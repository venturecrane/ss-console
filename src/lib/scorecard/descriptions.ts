/**
 * Operations Health Scorecard — Dimension descriptions per score range.
 *
 * Used by both the results page (client-side) and the PDF report.
 * Written in second person, non-judgmental tone per content standards.
 *
 * @see docs/design/operations-health-scorecard.md — Section 5
 */

import type { DimensionId, ScoreLabel } from './questions.js'

export const SCORE_DESCRIPTIONS: Record<DimensionId, Record<ScoreLabel, string>> = {
  process_design: {
    needs_attention:
      "Most of your processes live in people's heads right now. That makes it hard to delegate, train, or take a step back.",
    room_to_grow:
      "You've started documenting some things, but key decisions still flow through you. Formalizing a few more workflows would free up your time.",
    getting_there:
      'Your team handles most day-to-day work on their own. A few critical areas still depend on you.',
    strong:
      "Your business runs on well-documented processes. The team knows the playbook and you don't get pulled into every decision.",
  },

  tool_systems: {
    needs_attention:
      "You're running on manual effort and workarounds. The right tools, set up well, would save your team hours every week.",
    room_to_grow:
      "You have tools, but you're only using a fraction of what they can do. Closing those gaps would cut down on busywork.",
    getting_there:
      'Your tools mostly work, but information still moves manually in some places. Better connections between them would help.',
    strong:
      'Your tools handle the routine work and share information without someone re-entering it. The team uses them consistently.',
  },

  data_visibility: {
    needs_attention:
      "You're making decisions without a clear picture of the numbers. That's more common than you'd think, but it limits what you can plan for.",
    room_to_grow:
      'You have a general sense of the finances, but the details are fuzzy. Current books and real margin data would sharpen your pricing and planning.',
    getting_there:
      'Your financials are reasonably current. Getting more detail on job-level or service-level profitability would give you an edge.',
    strong:
      'You know your numbers. You price from real data and can see where the money goes without digging.',
  },

  customer_pipeline: {
    needs_attention:
      "Leads come in, but there's nothing catching them consistently. Some are slipping through and you might not know it.",
    room_to_grow:
      'You follow up on leads, but it depends on memory or manual effort. A consistent process would keep more of them from going cold.',
    getting_there:
      'You have a handle on most leads, but there are still gaps in tracking, follow-up, or staying in touch after the sale.',
    strong:
      'Every lead is tracked, follow-up happens on schedule, and you stay in touch with past customers.',
  },

  team_operations: {
    needs_attention:
      "You don't have a clear picture of what your team is doing day-to-day. New hires learn by trial and error, and issues show up late.",
    room_to_grow:
      "You check in with people individually, but there's no consistent way to see the full picture. Onboarding is informal.",
    getting_there:
      'Your team has some structure, but onboarding and performance feedback could be more consistent.',
    strong:
      'New hires get up to speed fast, performance issues surface early, and you can see what the team accomplished without chasing people down.',
  },
}
