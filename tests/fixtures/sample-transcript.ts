/**
 * Sample assessment call transcript for testing the extraction prompt.
 *
 * This is a realistic MacWhisper speaker-separated transcript of an
 * assessment call with a fictional Phoenix-area HVAC company owner.
 * The transcript exercises all major extraction fields:
 * - Business profile (vertical, employee count, tools)
 * - Multiple problems from the 6 universal problems
 * - Owner quotes suitable for proposals
 * - Complexity/scope signals
 * - Champion candidate identification
 * - Budget signals
 * - ROI anchor math verbalized by the owner
 * - No disqualification flags triggered
 */

export const SAMPLE_TRANSCRIPT = `[Speaker 1 - SMD Services]: Thanks for taking the time today, Mike. I know you're busy. Before we dig in, can you just walk me through what a typical day looks like for you right now?

[Speaker 2 - Mike Reeves, Owner]: Yeah, sure. So I get in around six thirty, and the first thing I do is check my phone. I've usually got a bunch of texts from the night before — customers asking about appointments, guys on the crew asking me questions about jobs. I spend probably the first hour just responding to all of that before I can even think about the actual schedule for the day.

[Speaker 1 - SMD Services]: So all of that communication is coming through your personal phone? Text messages?

[Speaker 2 - Mike Reeves, Owner]: Yeah, everything goes through my cell. Customers text me directly, my guys text me. Sometimes people call the office line but that just rings to my cell too. My wife used to answer it but she went back to work last year so now it's just me. I probably get 40 or 50 texts a day just about work stuff.

[Speaker 1 - SMD Services]: Got it. And what does the rest of the morning look like?

[Speaker 2 - Mike Reeves, Owner]: So after I deal with the texts, I've got to figure out who's going where. We've got 14 guys right now — three crews of four and then two guys who handle the smaller maintenance calls. I used to just keep the schedule in my head but we got too big for that about a year ago. We use Google Calendar now but honestly it's a mess. Half the time the guys don't check it and I end up calling them anyway to tell them where to go.

[Speaker 1 - SMD Services]: When you say it's a mess, what does that actually look like? Can you give me an example from this week?

[Speaker 2 - Mike Reeves, Owner]: Oh man, Monday we had a double-booking. Two crews showed up at the same commercial job because someone — I think it was me honestly — put it on two calendars. Customer was not happy. And then the residential job that one of those crews was supposed to do, nobody showed up. I had to drive over there myself and apologize. That's a $400 service call we almost lost and probably a Google review I'm going to have to deal with.

[Speaker 1 - SMD Services]: How often does something like that happen? The double-bookings or missed appointments?

[Speaker 2 - Mike Reeves, Owner]: The double-booking thing, maybe once or twice a month. Missed appointments, honestly, probably three or four a month. Sometimes it's the calendar, sometimes the crew just doesn't see the update. I don't know. It's gotten worse as we've added people.

[Speaker 1 - SMD Services]: If you think about those missed appointments — three or four a month — what does a typical service call bring in?

[Speaker 2 - Mike Reeves, Owner]: Average residential call is around $350, maybe $400. Commercial jobs are more like $800 to $1,200 depending on the scope. So yeah, we're probably leaving $1,500, maybe $2,000 on the table every month just from scheduling screw-ups. That doesn't even count the customers who don't call back.

[Speaker 1 - SMD Services]: Right. And new leads — how do those come in?

[Speaker 2 - Mike Reeves, Owner]: Mostly Google, some word of mouth. We get maybe 15 to 20 new calls or form submissions a week during busy season. The problem is I don't have a system for tracking them. Someone calls, I write it on a sticky note or put it in my phone, and then maybe I follow up the next day. But if I'm on a job site or dealing with a crew issue, that lead just sits there. I know for a fact we're losing people because they call, don't hear back in an hour, and call the next company.

[Speaker 1 - SMD Services]: How many leads do you think you're losing in a month from that?

[Speaker 2 - Mike Reeves, Owner]: Honestly, I'd guess five or six. Maybe more. My wife looked at the call log once and counted eight calls in a week that we never called back. At our average job value, that's... what, $2,000 or $3,000 a month in jobs that just went somewhere else. It makes me sick to think about it.

[Speaker 1 - SMD Services]: Yeah, that adds up fast. Let me ask about the team side. You mentioned 14 people — how do you track what they're doing day to day? Like quality checks, job completion, that sort of thing.

[Speaker 2 - Mike Reeves, Owner]: I don't, really. That's the honest answer. I trust my crew leads — Derek, especially. He's been with me since we were a three-man operation six years ago. But the other two leads, they've only been here about a year. I find out about problems when the customer calls to complain or when I happen to drive by a job site and see something wrong.

[Speaker 1 - SMD Services]: Tell me about Derek. If you brought in a new system or process, is he the kind of person who'd run with it?

[Speaker 2 - Mike Reeves, Owner]: Oh, absolutely. Derek's the only reason I can leave a job site without worrying. He already keeps his own little notebook — writes down what they did, any issues, parts they used. If I told him "here's how we're doing it now," he'd have it down in a day and he'd make sure his crew did it too. The other guys follow his lead on most things.

[Speaker 1 - SMD Services]: That's great. Let me shift gears a bit. What tools or software are you using right now beyond Google Calendar?

[Speaker 2 - Mike Reeves, Owner]: QuickBooks Online for bookkeeping — my bookkeeper handles that, she comes in once a week. It's pretty up to date, maybe a week or two behind at most. We use Google Workspace — email, calendar, the basics. I've got a spreadsheet for estimates and proposals but it's just a template I copy and fill in. No CRM or anything like that. Oh, and we use Podium for reviews but I barely touch it.

[Speaker 1 - SMD Services]: Is QuickBooks working well for you, or is there frustration there?

[Speaker 2 - Mike Reeves, Owner]: No, QuickBooks is fine. My bookkeeper knows it, I can pull reports when I need to. The problem isn't the books — it's everything else. I can see the numbers but I can't tell you why we had a bad month until I dig through my texts and try to reconstruct what happened. There's no connection between the schedule, the jobs, and the money.

[Speaker 1 - SMD Services]: Makes sense. How long have you been in business?

[Speaker 2 - Mike Reeves, Owner]: Coming up on eight years this September. Started as just me and a truck, now we're doing about $1.8 million — sorry, we're doing decent volume. We're not a startup, let's put it that way. We've outgrown how we operate but I haven't had time to fix it because I'm too busy putting out fires.

[Speaker 1 - SMD Services]: That's really common at your size. One more question — you mentioned your guys text you directly for everything. What kinds of things are they asking?

[Speaker 2 - Mike Reeves, Owner]: Everything. "What's the address for the 2 o'clock?" "Do we have this part in the warehouse?" "The customer wants to add a zone, should I quote it?" "I'm going to be late." Like I said, 40 or 50 texts a day. I'm basically the dispatcher, the sales manager, and the operations manual all rolled into one. I can't take a day off. Last time I tried, I came back to three customer complaints and a crew that went to the wrong job.

[Speaker 1 - SMD Services]: Based on what you've shared today, I think we can help with a few of these — specifically the scheduling issues, the lead follow-up, and getting your team some structure so they're not all going through you. We'll put together a scope and pricing and send it over today. Does that work?

[Speaker 2 - Mike Reeves, Owner]: Yeah, that'd be great. I've been meaning to fix this stuff for two years. I just never have time to figure out what to buy or how to set it up. If you guys can just tell me what to do and set it up, that's exactly what I need.`

/**
 * Expected extraction output for the sample transcript.
 *
 * This represents the realistic structured output that Claude should produce.
 * Used in tests to validate that the schema is correct and the prompt
 * produces output that passes validation.
 */
export const SAMPLE_EXTRACTION_OUTPUT: Record<string, unknown> = {
  schema_version: '2.0',
  extracted_at: '2026-03-30T12:00:00Z',

  business_name: 'Reeves HVAC',
  vertical: 'home_services',
  business_type: 'Residential and commercial HVAC',
  years_in_business: 8,
  employee_count: 14,
  revenue_range: '1m_3m',
  geography: null,

  current_tools: [
    {
      name: 'Google Calendar',
      purpose: 'Crew scheduling and job assignments',
      status: 'failing',
    },
    {
      name: 'QuickBooks Online',
      purpose: 'Bookkeeping and financial reporting',
      status: 'working',
    },
    {
      name: 'Google Workspace',
      purpose: 'Email and basic productivity',
      status: 'working',
    },
    {
      name: 'Podium',
      purpose: 'Review management',
      status: 'underutilized',
    },
    {
      name: 'Google Sheets',
      purpose: 'Estimates and proposals',
      status: 'underutilized',
    },
  ],

  identified_problems: [
    {
      problem_id: 'tool_systems',
      severity: 'high',
      summary:
        'Double-bookings and missed appointments happening multiple times per month due to a disorganized Google Calendar setup that crews do not consistently check.',
      owner_quotes: [
        'Monday we had a double-booking. Two crews showed up at the same commercial job because someone put it on two calendars.',
        'Missed appointments, honestly, probably three or four a month.',
        "We're probably leaving $1,500, maybe $2,000 on the table every month just from scheduling screw-ups.",
      ],
      underlying_cause:
        'No centralized scheduling system designed for field crews. Google Calendar is a general-purpose tool being forced into a dispatch role with no notifications, no crew assignment logic, and no mobile-first workflow for technicians.',
    },
    {
      problem_id: 'customer_pipeline',
      severity: 'high',
      summary:
        'New leads tracked on sticky notes and phone memory with no follow-up system, resulting in an estimated 5-8 lost leads per month during busy season.',
      owner_quotes: [
        'Someone calls, I write it on a sticky note or put it in my phone, and then maybe I follow up the next day.',
        'My wife looked at the call log once and counted eight calls in a week that we never called back.',
        "That's $2,000 or $3,000 a month in jobs that just went somewhere else. It makes me sick to think about it.",
      ],
      underlying_cause:
        'No CRM or lead intake process. The owner is the sole point of contact for new business, and when he is occupied with operations, leads receive no response. No automated acknowledgment, no assignment, no follow-up cadence.',
    },
    {
      problem_id: 'process_design',
      severity: 'medium',
      summary:
        'Owner serves as dispatcher, sales manager, and operations manual simultaneously, receiving 40-50 work texts daily and unable to take time off without operational failures.',
      owner_quotes: [
        "I'm basically the dispatcher, the sales manager, and the operations manual all rolled into one.",
        "I can't take a day off. Last time I tried, I came back to three customer complaints and a crew that went to the wrong job.",
        "I've been meaning to fix this stuff for two years. I just never have time to figure out what to buy or how to set it up.",
      ],
      underlying_cause:
        'No documented processes, no delegation framework, and no centralized information system. Every question routes to the owner because there is no other source of truth for schedules, customer details, or job specifications.',
    },
  ],

  complexity_signals: {
    employee_count: 14,
    location_count: 1,
    tool_migrations: [
      'Move from Google Calendar to a field-service scheduling platform',
      'Implement a CRM for lead tracking (currently no system)',
    ],
    data_volume_notes: [
      '15-20 new leads per week during busy season',
      '14 employees across 3 full crews + 2 maintenance technicians',
    ],
    integration_needs: [
      'New scheduling/CRM should ideally connect with QuickBooks Online for job costing',
    ],
    additional_factors: [
      'Three crew leads with varying experience levels — two newer leads may need more training',
      "Owner's wife no longer available to answer office phone, removing a previous communication buffer",
    ],
  },

  champion_candidate: {
    name: 'Derek',
    role: 'Senior crew lead',
    evidence:
      'Been with the company since it was a 3-person operation (6 years). Already keeps his own job documentation notebook. Owner says "he\'d have it down in a day and he\'d make sure his crew did it too." Other crew members follow his lead.',
    confidence: 'strong',
  },

  call_participants: ['Mike Reeves — Owner', 'SMD Services — Consulting team'],

  disqualification_flags: {
    hard: {
      not_decision_maker: false,
      scope_exceeds_phase: false,
      no_tech_baseline: false,
      in_crisis: false,
    },
    soft: {
      no_champion: false,
      books_behind: false,
      no_willingness_to_change: false,
      revenue_too_low: false,
      too_many_decision_makers: false,
    },
    notes:
      'No disqualification flags triggered. Owner is the decision maker, scope is appropriate for a single engagement (scheduling + lead tracking + basic crew processes), tech baseline exists (Google Workspace, QuickBooks), champion identified (Derek), books are current within 1-2 weeks, and owner is eager to change.',
  },

  budget_signals: {
    employees_on_payroll: true,
    years_in_business_3_plus: true,
    in_crisis: false,
    revenue_signals: [
      '14 employees across 3 full crews plus 2 maintenance techs',
      '8 years in business',
      'Average residential call $350-$400, commercial $800-$1,200',
    ],
    notes:
      '14 employees, 8 years in business, described as doing significant volume. Business is growing and stable but operationally strained, classic scaling pain, not crisis.',
  },

  quote_drivers: {
    recommended_problems: ['tool_systems', 'customer_pipeline', 'process_design'],
    estimated_complexity: 'medium',
    upward_pressures: [
      '14 employees across 5 sub-teams need to adopt new scheduling workflow',
      'CRM implementation is net-new — no existing system to migrate from, but intake process needs to be designed from scratch',
      'Two newer crew leads will need additional training time beyond the senior lead',
    ],
    downward_pressures: [
      'Strong champion (Derek) will accelerate crew adoption',
      'QuickBooks is already working — no financial system changes needed',
      'Owner is highly motivated and understands the problem clearly',
    ],
    roi_anchors: [
      'Owner estimates $1,500-$2,000/month lost to scheduling errors (3-4 missed appointments at $350-$400 average)',
      'Owner estimates $2,000-$3,000/month in lost leads (5-8 unfollowed leads at average job value)',
    ],
  },

  executive_summary:
    "Mike Reeves owns an 8-year-old Phoenix HVAC company with 14 employees that has outgrown its operational infrastructure. The business runs on the owner's phone, memory, and Google Calendar, resulting in scheduling chaos (double-bookings and 3-4 missed appointments per month) and significant lead leakage (5-8 unfollowed leads per month). The owner is the sole bottleneck for all scheduling, communication, and decision-making. A strong champion candidate (Derek, senior crew lead of 6 years) is already maintaining his own informal documentation. QuickBooks and bookkeeping are current and working. The owner is highly motivated, has been wanting to address these problems for two years, and is looking for someone to make the decisions and set up the solutions. No disqualification flags. Estimated medium complexity due to the team size and net-new CRM implementation.",

  additional_notes:
    'Owner almost disclosed revenue figures but caught himself — business appears healthy and established. Podium is installed but underutilized, which could be leveraged in the engagement as a quick win for review management alongside the core scheduling and CRM work.',
}
