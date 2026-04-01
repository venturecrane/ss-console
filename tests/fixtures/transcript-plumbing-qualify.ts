/**
 * Sample assessment call transcript — Qualifying plumbing company.
 *
 * A realistic MacWhisper speaker-separated transcript of an assessment call
 * with a fictional Phoenix-area residential plumbing company owner.
 *
 * Exercises:
 * - Business profile: home_services vertical, 18 employees, 5 years
 * - Problems: scheduling_chaos (high), lead_leakage (high), manual_communication (medium)
 * - Champion candidate: office manager "Maria" (moderate confidence)
 * - No disqualification flags triggered
 * - Medium complexity
 * - ROI anchors: owner verbalizes lost revenue from missed callbacks
 *
 * @see tests/fixtures/sample-transcript.ts — existing HVAC fixture (different problem combo)
 */

export const PLUMBING_TRANSCRIPT = `[Speaker 1 - SMD Services]: Thanks for sitting down with us, Lisa. We appreciate you making the time. Can you start by walking us through what a typical day looks like for you?

[Speaker 2 - Lisa Medina, Owner]: Sure. So I'm usually up at five and checking my phone before I even get out of bed. My guys start at seven, so by six I need to know who's going where. I've got 18 people now — four service crews and then Maria in the office handling phones and billing. But honestly, I'm still the one making most of the decisions about who goes where.

[Speaker 1 - SMD Services]: How do you manage the schedule? What does that process look like?

[Speaker 2 - Lisa Medina, Owner]: We use a whiteboard in the office. Seriously. Maria writes up the schedule the night before based on what I tell her, and then the crews check it in the morning when they pick up their trucks. If something changes — and something always changes — I call or text them. The problem is half the time they've already left by the time I know about the change. We tried a shared Google Sheet once but nobody looked at it.

[Speaker 1 - SMD Services]: What happens when something changes mid-day? Like a job runs long or a customer cancels?

[Speaker 2 - Lisa Medina, Owner]: Chaos, basically. Last Thursday we had a main line replacement that was supposed to be a four-hour job. Turned into eight hours because they hit a root system nobody expected. So the afternoon jobs for that crew — two service calls — just didn't happen. I was on the phone for an hour trying to reschedule those customers and shuffle another crew's schedule to cover an emergency call that came in. One of the customers we bumped, she was furious. Left us a one-star review. That's the kind of thing that keeps me up at night.

[Speaker 1 - SMD Services]: How often does that kind of cascading schedule problem happen?

[Speaker 2 - Lisa Medina, Owner]: Weekly. At least once a week something blows up like that. And then maybe two or three other times a week we have smaller issues — a crew shows up and the customer isn't home because we gave the wrong time, or two crews get sent to the same commercial job because I told Maria one thing and then texted the crew lead something different. It's embarrassing. We're not a small operation anymore but we're running like one.

[Speaker 1 - SMD Services]: When jobs get bumped or customers get rescheduled, what's the revenue impact?

[Speaker 2 - Lisa Medina, Owner]: Our average service call is around $300. Main line and repiping jobs are $2,000 to $5,000. When we bump a service call, we might lose the customer entirely — they just call someone else. I'd say we lose two or three service calls a month from rescheduling, so that's almost a thousand right there. And then there's the reviews. Every bad review costs us, I just don't know how to put a number on it.

[Speaker 1 - SMD Services]: Let's talk about new business. How do leads come in and what happens when they do?

[Speaker 2 - Lisa Medina, Owner]: Most of our calls come from Google — we've got a good Google Business profile and we show up in the map pack for most plumbing searches in Gilbert and Chandler. We probably get 25 to 30 calls and form fills a week. The problem is Maria can't answer every call. She's also doing invoicing and dealing with vendors and handling permit paperwork. When she misses a call, it goes to voicemail. And I'll be honest with you — we're not great about calling people back quickly.

[Speaker 1 - SMD Services]: How quickly do you typically return calls?

[Speaker 2 - Lisa Medina, Owner]: Same day, usually. Sometimes the next morning. But I've read that people call three or four plumbers at once and go with whoever calls back first. So by the time we call back in four hours, they've already booked someone else. Maria pulled our call logs last month and counted 11 missed calls that we never returned at all. Eleven! At $300 average, that's over $3,000 in one month that just walked away.

[Speaker 1 - SMD Services]: Is there a system for tracking which leads turn into jobs and which ones you lose?

[Speaker 2 - Lisa Medina, Owner]: No. I mean, we know the ones that book because they go on the whiteboard. But the ones that don't book — they just disappear. We have no idea how many people call, don't get through, and never call back. It could be way more than 11. That's just the ones Maria saw in the log and remembered.

[Speaker 1 - SMD Services]: Let's shift to communication. You mentioned texting crews directly. How does communication work day-to-day?

[Speaker 2 - Lisa Medina, Owner]: Everything is text messages. My guys text me, I text them, Maria texts them, sometimes customers text me directly because they have my cell from last time. I'm getting maybe 60 texts a day about work. And it's not just scheduling — it's "do we have this fitting in the warehouse," "can I add a water heater replacement to this job," "the customer is asking about financing." I'm the answer to every question because there's no other place for them to look.

[Speaker 1 - SMD Services]: Are there things your crews ask you repeatedly that could be answered some other way?

[Speaker 2 - Lisa Medina, Owner]: Oh absolutely. Pricing questions, for one. "What do we charge for a garbage disposal install?" We've got a price sheet but it's on paper in the office. Nobody has it in the field. Parts inventory — they text me asking if we have something in the warehouse, and I text Maria, and she goes and checks. It's like a game of telephone. And job procedures — my newer guys especially, they'll text me photos asking if something looks right. I end up doing quality checks over text message.

[Speaker 1 - SMD Services]: Tell me about Maria. She seems like she handles a lot. If you brought in new tools or processes, is she someone who'd take ownership of that?

[Speaker 2 - Lisa Medina, Owner]: Maria is my rock. She's been with me for three years. She's smart, she picks things up quickly, and she actually wants better systems. She's the one who suggested the Google Sheet for scheduling — that was her idea. It just didn't work because the crews didn't buy in. But if we had the right tool and someone showed her how to run it, she'd absolutely own it. She's already keeping us afloat with duct tape and spreadsheets. She'd be thrilled to have something better.

[Speaker 1 - SMD Services]: What about the rest of the team? If you rolled out a new scheduling system or way of communicating, would the crews use it?

[Speaker 2 - Lisa Medina, Owner]: My crew leads would. Especially Manny — he's been pushing for an app for job tracking for a year. The newer guys follow whatever the leads do. As long as it's on their phone and not complicated, they'll use it. They're all on their phones all day anyway.

[Speaker 1 - SMD Services]: What tools are you using right now for the business?

[Speaker 2 - Lisa Medina, Owner]: QuickBooks for bookkeeping — my bookkeeper comes in twice a month and she keeps it pretty tight. Maybe two weeks behind at worst. We use Google Workspace for email. I've got a paper price sheet and a binder of procedures that's about two years out of date. No CRM. No scheduling software. We tried Jobber for about a month last year but nobody set it up properly and it just became one more thing to check, so we dropped it.

[Speaker 1 - SMD Services]: Is QuickBooks working well for you?

[Speaker 2 - Lisa Medina, Owner]: Yeah, QuickBooks is solid. No complaints there. My bookkeeper knows it inside and out. The financial side is fine — I can see my numbers. It's the operational side that's a mess.

[Speaker 1 - SMD Services]: How long have you been in business?

[Speaker 2 - Lisa Medina, Owner]: Five years this July. Started with just me and one helper doing residential service calls out of my garage. Now we're running $1.5 million — I mean, we're doing real volume. We've grown faster than our processes, and I know that. I just haven't had the bandwidth to fix it while we're this busy.

[Speaker 1 - SMD Services]: That makes sense — it's a common challenge at your size. Based on what you've shared, I think we can help with the scheduling, the lead follow-up process, and streamlining how your team communicates so everything doesn't flow through you. We'll put together a scope and send it over. Sound good?

[Speaker 2 - Lisa Medina, Owner]: Absolutely. Honestly, just talking through all of this out loud is making me realize how bad it's gotten. I need help. If you guys can come in and just tell me what to use and set it up so it works, that's exactly what I need. Maria and I are ready.`

/**
 * Expected extraction output for the plumbing qualifying transcript.
 *
 * Represents the realistic structured output that Claude should produce.
 * Used in tests to validate schema correctness and prompt behavior.
 */
export const PLUMBING_EXPECTED_EXTRACTION: Record<string, unknown> = {
  schema_version: '1.0',
  extracted_at: '2026-03-31T12:00:00Z',

  business_name: 'Medina Plumbing',
  vertical: 'home_services',
  business_type: 'Residential plumbing',
  years_in_business: 5,
  employee_count: 18,
  geography: 'Gilbert',

  current_tools: [
    {
      name: 'Whiteboard',
      purpose: 'Daily crew scheduling and job assignments',
      status: 'failing',
    },
    {
      name: 'QuickBooks',
      purpose: 'Bookkeeping and financial reporting',
      status: 'working',
    },
    {
      name: 'Google Workspace',
      purpose: 'Email',
      status: 'working',
    },
    {
      name: 'Paper price sheet',
      purpose: 'Service pricing reference',
      status: 'failing',
    },
  ],

  identified_problems: [
    {
      problem_id: 'scheduling_chaos',
      severity: 'high',
      summary:
        'Weekly cascading schedule failures from a whiteboard-based system with no real-time updates, causing missed appointments, double-bookings, and angry customer reviews.',
      owner_quotes: [
        "Last Thursday we had a main line replacement that was supposed to be a four-hour job. Turned into eight hours. So the afternoon jobs for that crew just didn't happen.",
        'At least once a week something blows up like that.',
        "We're not a small operation anymore but we're running like one.",
      ],
      underlying_cause:
        'No centralized digital scheduling system. Whiteboard in the office has no real-time sync to the field. Changes propagate via phone calls and texts, which lag behind reality. No automated rescheduling or crew notification.',
    },
    {
      problem_id: 'lead_leakage',
      severity: 'high',
      summary:
        'Office manager cannot answer every inbound call while handling billing and admin, resulting in 11+ unreturned calls per month and estimated $3,000+ in lost revenue.',
      owner_quotes: [
        'Maria pulled our call logs last month and counted 11 missed calls that we never returned at all.',
        "By the time we call back in four hours, they've already booked someone else.",
        "The ones that don't book — they just disappear. We have no idea how many people call, don't get through, and never call back.",
      ],
      underlying_cause:
        'No CRM, no automated call-back or lead intake system. Maria is the sole phone handler while also doing invoicing, vendor management, and permits. Leads that go to voicemail have no follow-up process.',
    },
    {
      problem_id: 'manual_communication',
      severity: 'medium',
      summary:
        'Owner receives 60+ daily work texts serving as dispatcher, pricing reference, inventory checker, and quality control — all because no centralized information system exists for the field crews.',
      owner_quotes: [
        "I'm getting maybe 60 texts a day about work.",
        "It's like a game of telephone.",
        'I end up doing quality checks over text message.',
      ],
      underlying_cause:
        'No mobile-accessible price sheets, no digital inventory lookup, no standardized job procedures available to crews. Every question routes to the owner or office manager because there is no self-serve information source.',
    },
  ],

  complexity_signals: {
    employee_count: 18,
    location_count: 1,
    tool_migrations: [
      'Move from whiteboard to a field-service scheduling platform',
      'Implement a CRM for lead tracking and follow-up (currently no system)',
      'Digitize price sheets and procedures for mobile field access',
    ],
    data_volume_notes: [
      '25-30 new leads per week via Google Business profile',
      '18 employees across 4 service crews plus office manager',
    ],
    integration_needs: ['New scheduling/CRM should connect with QuickBooks for invoicing'],
    additional_factors: [
      'Previous Jobber implementation failed due to poor setup — team may have tool fatigue',
      'Price sheet and procedures binder are 2 years out of date and need to be rebuilt',
    ],
  },

  champion_candidate: {
    name: 'Maria',
    role: 'Office manager',
    evidence:
      'Been with the company 3 years. Already proactively suggested the Google Sheet scheduling attempt. Owner says she "picks things up quickly" and "wants better systems." Currently holding operations together with spreadsheets. Would "absolutely own" a new system.',
    confidence: 'moderate',
  },

  call_participants: ['Lisa Medina — Owner', 'SMD Services — Consulting team'],

  disqualification_flags: {
    hard: {
      not_decision_maker: false,
      scope_exceeds_sprint: false,
      no_tech_baseline: false,
    },
    soft: {
      no_champion: false,
      books_behind: false,
      no_willingness_to_change: false,
    },
    notes:
      'No disqualification flags triggered. Owner is the decision maker and check-writer. Scope is appropriate for a single engagement (scheduling + lead tracking + communication). Tech baseline exists (Google Workspace, QuickBooks, smartphones). Champion identified (Maria, office manager). Books are current within 2 weeks. Owner and champion are both eager for change.',
  },

  budget_signals: {
    employees_on_payroll: true,
    years_in_business_3_plus: true,
    in_crisis: false,
    notes:
      '18 employees, 5 years in business, doing significant revenue volume. Growing faster than processes can support — classic scaling pain, not financial distress.',
  },

  quote_drivers: {
    recommended_problems: ['scheduling_chaos', 'lead_leakage', 'manual_communication'],
    estimated_complexity: 'medium',
    upward_pressures: [
      '18 employees across 4 crews need to adopt new scheduling workflow',
      'Previous Jobber failure means the team may be skeptical of new tools — need careful change management',
      'Price sheet and procedures need to be rebuilt from scratch before they can be digitized',
    ],
    downward_pressures: [
      'Champion (Maria) is eager and capable — will accelerate adoption',
      'Crew lead Manny has been actively requesting better tools — internal demand exists',
      'QuickBooks is already working — no financial system changes needed',
      'Owner is highly motivated and self-aware about the problems',
    ],
    roi_anchors: [
      'Owner estimates $3,000+/month in lost leads (11+ unreturned calls at $300 average service call)',
      'Owner estimates ~$1,000/month in lost revenue from bumped/rescheduled service calls',
    ],
  },

  executive_summary:
    "Lisa Medina owns a 5-year-old Gilbert plumbing company with 18 employees that has outgrown its whiteboard scheduling and paper-based operations. The business experiences weekly cascading schedule failures, loses an estimated $3,000+ per month in unreturned leads, and routes all field communication through the owner's personal phone (60+ texts per day). A capable champion candidate (Maria, office manager of 3 years) is eager for better systems, and at least one crew lead is actively requesting digital tools. QuickBooks and bookkeeping are current. A previous Jobber implementation failed due to poor setup, so change management will be important. No disqualification flags. Estimated medium complexity.",

  additional_notes:
    'Owner almost disclosed revenue figures but caught herself. Previous Jobber failure is a yellow flag for implementation — the tool itself was fine but setup and crew buy-in were not managed. This should inform our implementation approach: structured rollout with crew training rather than just configuring the tool and handing it over.',
}
