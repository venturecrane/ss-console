/**
 * Sample assessment call transcript — Accounting firm with soft disqualifiers.
 *
 * A realistic MacWhisper speaker-separated transcript of an assessment call
 * with a fictional Scottsdale accounting firm owner.
 *
 * Exercises:
 * - Business profile: professional_services vertical, 12 employees, 10 years
 * - Problems: owner_bottleneck (high), financial_blindness (medium), team_invisibility (medium)
 * - No champion candidate identified
 * - Soft disqualifiers: books_behind, no_champion, no_willingness_to_change
 * - High complexity (resistance to change, no champion, books behind)
 * - No ROI anchors verbalized
 *
 * Writing notes:
 * - Owner explicitly resists change ("tried that before", "my team won't learn")
 * - team_invisibility symptoms: no task tracking, no accountability, no quality checks
 *   (NOT employee turnover/retention — schema uses team_invisibility, not employee_retention)
 * - financial_blindness is ironic for an accountant — their CLIENT books are fine,
 *   but their OWN firm's books are 60+ days behind
 *
 * @see tests/fixtures/sample-transcript.ts — existing HVAC fixture
 * @see tests/fixtures/transcript-plumbing-qualify.ts — qualifying path fixture
 */

export const ACCOUNTING_TRANSCRIPT = `[Speaker 1 - SMD Services]: Thanks for meeting with us, Robert. Can you tell us a bit about your firm and what your day-to-day looks like?

[Speaker 2 - Robert Haas, Owner]: Sure. So we're a full-service CPA firm here in Scottsdale. Been in business ten years. We do tax prep, bookkeeping for about 40 small business clients, payroll, and some advisory work. I've got 12 people — three CPAs including myself, four staff accountants, two bookkeepers, an admin, a part-time IT person, and a tax season temp we just brought on.

[Speaker 1 - SMD Services]: That's a solid team. Walk me through what a typical day looks like for you.

[Speaker 2 - Robert Haas, Owner]: Honestly, it's mostly putting out fires. I get in around seven and the first thing I do is check what fell through the cracks yesterday. We're in tax season right now, so everyone's heads down on returns. But the bookkeeping clients still need their monthlies done, and the staff accountants are supposed to handle those. The problem is I'm the one fielding every question. "Robert, how do I categorize this?" "Robert, the client sent their bank statements but they're missing December." "Robert, should we bill this client for the extra work?" Everything comes to me.

[Speaker 1 - SMD Services]: It sounds like you're the decision point for a lot of things that could potentially be handled by your staff. How much of your day would you say goes to those kinds of interruptions?

[Speaker 2 - Robert Haas, Owner]: Easily half my day. And the thing is, I've been doing this for 25 years — 15 before I started the firm. I know the answer to every question they ask me. But I've never written any of it down. The procedures, the client preferences, the billing rules — it's all up here. I know I need to document it. I've been saying that for five years. But when am I going to do it? I'm billing 50 hours a week during tax season.

[Speaker 1 - SMD Services]: Let me ask about how you track work across the team. If I asked you right now which client engagements each person is working on and where they stand, could you tell me?

[Speaker 2 - Robert Haas, Owner]: No. Not precisely. I have a general idea — like I know Sarah is doing the Henderson Group return because I assigned it to her on Monday. But is she halfway done? Is she stuck waiting for documents? I have no idea unless I walk over and ask her. We don't have a task tracking system. We used to have a spreadsheet that my admin maintained, but she stopped updating it about six months ago and nobody noticed for two months. That tells you how well it was working.

[Speaker 1 - SMD Services]: What about quality control? When a return or a set of monthlies is done, how does it get reviewed?

[Speaker 2 - Robert Haas, Owner]: I review everything. Every return, every set of financials. Personally. The other two CPAs could do reviews, but the clients expect me to sign off. And honestly, I've caught enough mistakes that I'm not comfortable delegating it. Last month one of the staff accountants miscategorized $40,000 in expenses on a client's books. If I hadn't caught it, that's a huge problem at tax time. There's no checklist, no review process — they finish the work, put it on my desk, and I go through it line by line.

[Speaker 1 - SMD Services]: That's a lot riding on you personally. Let me shift to your firm's own finances. How current are your internal books?

[Speaker 2 - Robert Haas, Owner]: [laughs] You know, that's the cobbler's shoes situation. My bookkeepers do great work for our clients — those books are current, reconciled, everything up to date. But our own firm's books? I'd say we're about two months behind. Maybe more. My admin was handling it but she got overwhelmed with other things and I just haven't gotten to it. I know roughly where we stand because I watch the bank balance, but I couldn't pull a real P&L right now if I needed to. It's embarrassing for a CPA firm, honestly.

[Speaker 1 - SMD Services]: That's more common than you'd think. Let me ask — is there someone on your team who you could see taking ownership of new processes or tools if you brought them in? Someone who'd be the go-to person for making sure the team uses them?

[Speaker 2 - Robert Haas, Owner]: That's a good question. Honestly... not really. Sarah is my best CPA but she's purely technical — she doesn't want to manage anything. My admin, Janet, she's organized but she's already stretched thin. The bookkeepers are good at their jobs but they're not going to drive change. I'd probably have to do it myself, and I know that's not a great answer.

[Speaker 1 - SMD Services]: Have you tried implementing new tools or systems before?

[Speaker 2 - Robert Haas, Owner]: We've tried. We tried Asana about two years ago for task management. Paid for it, I set up all the projects, spent a whole weekend building it out. By the second week, nobody was updating it. People would mark things done when they weren't, or not update it at all. I stopped nagging them after a month and we just quietly let it die. Before that we tried a client portal — TaxDome — for document collection. Same story. My team didn't want to learn it, half the clients didn't want to use it. We went back to email.

[Speaker 1 - SMD Services]: What do you think happened in those cases? Why didn't they stick?

[Speaker 2 - Robert Haas, Owner]: Look, I think my team is set in their ways. They've been doing things a certain way for years and they don't see why they need to change. And honestly, part of me agrees with them. We're profitable, clients are happy, nobody's quitting. The problems are real but they're not killing us. I don't think another tool is the answer — we've proven that twice now. Every time we try something new, it just becomes one more thing people ignore and then I'm the one cleaning up the mess. I'm done forcing change on people who don't want it. If there's a way to fix this without asking my team to learn something new, I'm all ears. But I'm not doing another Asana.

[Speaker 1 - SMD Services]: I hear you. Let me ask about something different — how do you handle billing? Do you know your effective rate per client?

[Speaker 2 - Robert Haas, Owner]: We bill hourly for most things, fixed fee for tax returns. But honestly, I don't track realization rates. I know some clients we're losing money on because the work takes way longer than it should, but I couldn't tell you which ones specifically because, again, our own books are behind and I don't have a time tracking system anyone actually uses. We have one — Harvest — but compliance is maybe 60 percent. People forget, or they batch their hours at the end of the week from memory, which is basically useless.

[Speaker 1 - SMD Services]: What does your tech stack look like overall?

[Speaker 2 - Robert Haas, Owner]: We're on QuickBooks Online for our own books and most of our clients. Drake for tax prep. Microsoft 365 for email and documents. We've got a shared drive with client folders but the naming convention is all over the place. Harvest for time tracking, but like I said, nobody uses it consistently. And then email is the main communication channel for everything — clients, internal, all of it.

[Speaker 1 - SMD Services]: Based on what you've shared, there are a few areas where we could help — the owner bottleneck with everything routing through you, getting visibility into what your team is working on, and getting your firm's financials current so you can make decisions from real numbers. We'll scope it out and send over a proposal. Does that work for you?

[Speaker 2 - Robert Haas, Owner]: You can send it over, I'll look at it. But I want to be straight with you — I'm skeptical. We've spent money on this kind of thing before and ended up right back where we started. My team doesn't want to change, and I'm tired of being the one pushing a rock uphill. If your proposal says "implement new software," I'm probably going to pass. I need to see something that works with reality, not some ideal version of my firm that doesn't exist.`

/**
 * Expected extraction output for the accounting firm transcript.
 *
 * This transcript exercises soft disqualification paths:
 * - books_behind: firm's own books 60+ days behind
 * - no_champion: no one identified to own new processes
 * - no_willingness_to_change: explicit resistance, failed tool adoptions, "not sure another tool is the answer"
 */
export const ACCOUNTING_EXPECTED_EXTRACTION: Record<string, unknown> = {
  schema_version: '1.0',
  extracted_at: '2026-03-31T12:00:00Z',

  business_name: 'Haas CPA',
  vertical: 'professional_services',
  business_type: 'CPA / accounting firm',
  years_in_business: 10,
  employee_count: 12,
  geography: 'Scottsdale',

  current_tools: [
    {
      name: 'QuickBooks Online',
      purpose: 'Firm bookkeeping and client bookkeeping',
      status: 'working',
    },
    {
      name: 'Drake',
      purpose: 'Tax preparation',
      status: 'working',
    },
    {
      name: 'Microsoft 365',
      purpose: 'Email, documents, shared drive',
      status: 'underutilized',
    },
    {
      name: 'Harvest',
      purpose: 'Time tracking',
      status: 'failing',
    },
  ],

  identified_problems: [
    {
      problem_id: 'owner_bottleneck',
      severity: 'high',
      summary:
        'Owner is the sole decision point for all staff questions, reviews every deliverable personally, and has never documented procedures — spending half his day on interruptions while billing 50 hours per week.',
      owner_quotes: [
        "Easily half my day. And the thing is, I know the answer to every question they ask me. But I've never written any of it down.",
        'I review everything. Every return, every set of financials. Personally.',
        "I've been saying that for five years. But when am I going to do it?",
      ],
      underlying_cause:
        "No documented procedures, no delegation framework, no review process beyond the owner. Twenty-five years of knowledge lives entirely in the owner's head. Staff have no alternative source of truth, so every question and every deliverable routes to him.",
    },
    {
      problem_id: 'financial_blindness',
      severity: 'medium',
      summary:
        "Firm's own books are 60+ days behind despite being a CPA firm. Owner cannot pull a real P&L and makes financial decisions based on bank balance alone. Time tracking compliance is ~60%, so realization rates and per-client profitability are unknown.",
      owner_quotes: [
        "Our own firm's books? I'd say we're about two months behind. Maybe more.",
        "I couldn't pull a real P&L right now if I needed to. It's embarrassing for a CPA firm.",
        "I don't track realization rates. I know some clients we're losing money on but I couldn't tell you which ones.",
      ],
      underlying_cause:
        'Admin who was handling firm books got overwhelmed and fell behind. No one else assigned to pick it up. Harvest time tracking has poor adoption (~60%), making profitability analysis impossible. Owner relies on bank balance intuition rather than actual financial data for his own firm.',
    },
    {
      problem_id: 'team_invisibility',
      severity: 'medium',
      summary:
        'No task tracking system in use — owner cannot determine the status of any engagement without walking over and asking. Previous spreadsheet tracker was abandoned for months without anyone noticing. No quality checklists exist.',
      owner_quotes: [
        'Is she halfway done? Is she stuck waiting for documents? I have no idea unless I walk over and ask her.',
        'She stopped updating it about six months ago and nobody noticed for two months.',
        "There's no checklist, no review process — they finish the work, put it on my desk, and I go through it line by line.",
      ],
      underlying_cause:
        "No work management system in active use. Previous Asana implementation failed within a month. No standardized review checklists or quality gates. Task status is invisible until work lands on the owner's desk for manual review.",
    },
  ],

  complexity_signals: {
    employee_count: 12,
    location_count: 1,
    tool_migrations: [
      'Fix or replace Harvest time tracking (currently ~60% compliance)',
      'Implement task/work management system (Asana previously failed)',
    ],
    data_volume_notes: [
      '40 small business bookkeeping clients',
      '12 employees across CPA, staff accountant, bookkeeper, and admin roles',
    ],
    integration_needs: [
      'Task management should integrate with existing Drake and QuickBooks workflow',
    ],
    additional_factors: [
      'Two prior tool implementations failed (Asana, TaxDome) — significant tool fatigue and skepticism',
      "Team culture actively resists new processes — owner describes staff as 'set in their ways'",
      'Shared drive naming conventions are inconsistent — document organization is a secondary issue',
    ],
  },

  champion_candidate: null,

  call_participants: ['Robert Haas — Owner', 'SMD Services — Consulting team'],

  disqualification_flags: {
    hard: {
      not_decision_maker: false,
      scope_exceeds_sprint: false,
      no_tech_baseline: false,
    },
    soft: {
      no_champion: true,
      books_behind: true,
      no_willingness_to_change: true,
    },
    notes:
      "Three soft disqualifiers triggered. (1) No champion: owner explicitly stated no one on the team would take ownership of new processes — 'I'd probably have to do it myself.' (2) Books behind: firm's own books are 60+ days behind. (3) Willingness to change: two prior tool implementations failed (Asana, TaxDome), owner says 'I'm not sure throwing another tool at it is the answer' and 'my team is set in their ways.' The owner is self-aware about the problems but skeptical that intervention will work given the team's history of rejecting new tools.",
  },

  budget_signals: {
    employees_on_payroll: true,
    years_in_business_3_plus: true,
    in_crisis: false,
    notes:
      "12 employees, 10 years in business, described as profitable with happy clients. Not in crisis — problems are 'manageable' per the owner. This is a stable firm with chronic operational debt, not an acute situation.",
  },

  quote_drivers: {
    recommended_problems: ['owner_bottleneck', 'team_invisibility', 'financial_blindness'],
    estimated_complexity: 'high',
    upward_pressures: [
      'Two prior failed tool implementations create significant adoption risk',
      'No internal champion to drive day-to-day adoption — owner would need to fill this role',
      "Team culture actively resists process changes — 'set in their ways'",
      'Owner reviews every deliverable personally and is uncomfortable delegating — reducing this will require building trust gradually',
      'Firm books 60+ days behind need catch-up work before operational changes can be measured',
    ],
    downward_pressures: [
      'Tech baseline exists (QuickBooks, Microsoft 365, Drake) — no foundational gaps',
      'Staff accountants and bookkeepers are competent at their technical work',
      'Owner is self-aware and intellectually understands the problems',
    ],
    roi_anchors: [],
  },

  executive_summary:
    "Robert Haas owns a 10-year-old Scottsdale CPA firm with 12 employees that suffers from a severe owner bottleneck (reviews every deliverable, fields every question, 50+ billable hours/week), no visibility into team workload or engagement status, and ironically behind on its own firm's books despite doing bookkeeping for 40 clients. Two prior tool implementations (Asana, TaxDome) failed due to poor team adoption, and the owner expresses skepticism about whether new tools will work. No internal champion was identified — the best candidates (admin, senior CPA) are either stretched thin or uninterested in operational management. Three soft disqualifiers triggered: books behind, no champion, and resistance to change. High complexity engagement if pursued.",

  additional_notes:
    "This is a cautionary assessment. The problems are real and significant, but the delivery risk is high. The owner's closing statement — 'whatever you propose needs to work with my team the way they are, not the way I wish they were' — signals that he understands the team won't change easily. If this engagement proceeds, it should focus on process documentation and lightweight workflow changes rather than new tool adoption. The financial_blindness problem (firm's own books) may need to be the first deliverable to establish credibility and momentum before tackling the harder cultural issues.",
}
