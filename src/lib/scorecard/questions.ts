/**
 * Operations Health Scorecard — Question bank and constants.
 *
 * Single source of truth for all scorecard content. Imported server-side
 * by the API endpoint and the Astro page. Scoring constants are serialized
 * into the page for client-side use.
 *
 * @see docs/design/operations-health-scorecard.md — full spec
 */

import type { ProblemId } from '../../portal/assessments/extraction-schema.js'

// ---------------------------------------------------------------------------
// Dimensions — maps to canonical problem IDs in extraction-schema.ts
// ---------------------------------------------------------------------------

export type DimensionId = ProblemId

export interface Dimension {
  id: DimensionId
  label: string
  icon: string
  sectionHeader: string
}

export const DIMENSIONS: Dimension[] = [
  {
    id: 'process_design',
    label: 'Process Maturity',
    icon: 'account_tree',
    sectionHeader: 'How your business runs day-to-day',
  },
  {
    id: 'tool_systems',
    label: 'Tool Effectiveness',
    icon: 'build',
    sectionHeader: 'The tools you use',
  },
  {
    id: 'data_visibility',
    label: 'Data & Visibility',
    icon: 'monitoring',
    sectionHeader: 'Seeing the numbers',
  },
  {
    id: 'customer_pipeline',
    label: 'Customer Pipeline',
    icon: 'leaderboard',
    sectionHeader: 'How customers find and stay with you',
  },
  {
    id: 'team_operations',
    label: 'Team Operations',
    icon: 'groups',
    sectionHeader: 'Your team',
  },
]

// ---------------------------------------------------------------------------
// Context questions (3 questions before the scored walkthrough)
// ---------------------------------------------------------------------------

export interface ContextOption {
  value: string
  label: string
}

export interface ContextQuestion {
  id: string
  label: string
  options: ContextOption[]
}

export const CONTEXT_QUESTIONS: ContextQuestion[] = [
  {
    id: 'vertical',
    label: 'What type of business do you run?',
    options: [
      { value: 'home_services', label: 'Home Services (plumber, HVAC, electrician, etc.)' },
      {
        value: 'professional_services',
        label: 'Professional Services (accountant, attorney, agency, etc.)',
      },
      { value: 'contractor_trades', label: 'Contractor / Trades' },
      { value: 'retail_salon', label: 'Retail / Salon / Spa' },
      { value: 'restaurant_food', label: 'Restaurant / Food Service' },
      { value: 'healthcare', label: 'Healthcare (dental, chiropractic, therapy, etc.)' },
      { value: 'technology', label: 'Technology / IT Services' },
      { value: 'manufacturing', label: 'Manufacturing / Production' },
      { value: 'other', label: 'Other' },
    ],
  },
  {
    id: 'employee_range',
    label: 'How many people are on your team?',
    options: [
      { value: '1-5', label: '1-5' },
      { value: '6-10', label: '6-10' },
      { value: '11-25', label: '11-25' },
      { value: '26-50', label: '26-50' },
      { value: '50+', label: '50+' },
    ],
  },
  {
    id: 'revenue_range',
    label: 'What is your approximate annual revenue?',
    options: [
      { value: 'under_500k', label: 'Under $500k' },
      { value: '500k_1m', label: '$500k - $1M' },
      { value: '1m_3m', label: '$1M - $3M' },
      { value: '3m_5m', label: '$3M - $5M' },
      { value: '5m_10m', label: '$5M - $10M' },
      { value: 'over_10m', label: 'Over $10M' },
    ],
  },
  {
    id: 'role',
    label: "What's your role?",
    options: [
      { value: 'owner', label: 'Owner' },
      { value: 'coo_vp_ops', label: 'COO / VP Operations' },
      { value: 'office_manager', label: 'Office Manager' },
      { value: 'other', label: 'Other' },
    ],
  },
]

// ---------------------------------------------------------------------------
// Scored questions (18 questions, 3 per dimension)
// ---------------------------------------------------------------------------

export interface QuestionOption {
  key: 'a' | 'b' | 'c' | 'd' | 'skip'
  score: 0 | 1 | 2 | 3 | -1
  text: string
}

export interface ScoredQuestion {
  id: string
  dimension: DimensionId
  text: string
  options: QuestionOption[]
}

export const QUESTIONS: ScoredQuestion[] = [
  // --- Process Maturity (process_design) ---
  {
    id: 'q1',
    dimension: 'process_design',
    text: 'How are your core business processes documented?',
    options: [
      { key: 'a', score: 0, text: "They're not. It's all in people's heads." },
      { key: 'b', score: 1, text: "Some things are written down, but it's scattered or outdated" },
      {
        key: 'c',
        score: 2,
        text: "We have docs for the important stuff, but they're not always followed",
      },
      { key: 'd', score: 3, text: 'Key workflows are documented and the team actually uses them' },
    ],
  },
  {
    id: 'q2',
    dimension: 'process_design',
    text: 'If you had to take an unplanned week off, what would happen?',
    options: [
      { key: 'a', score: 0, text: "Things would stall. I'm involved in almost everything." },
      {
        key: 'b',
        score: 1,
        text: 'The basics would keep going, but nothing new would move forward',
      },
      { key: 'c', score: 2, text: 'Most things would be fine, but a few key areas still need me' },
      { key: 'd', score: 3, text: 'The team would handle it. They know the playbook.' },
    ],
  },
  {
    id: 'q3',
    dimension: 'process_design',
    text: "When a team member hits a decision that's outside their normal routine, what happens?",
    options: [
      { key: 'a', score: 0, text: 'They wait for me. I make most of the calls.' },
      { key: 'b', score: 1, text: 'They call or text me and I handle it when I can' },
      { key: 'c', score: 2, text: 'A few people can handle it, but the big stuff comes to me' },
      {
        key: 'd',
        score: 3,
        text: 'We have guidelines for who decides what. I rarely get pulled in.',
      },
    ],
  },

  // --- Tool Effectiveness (tool_systems) ---
  {
    id: 'q4',
    dimension: 'tool_systems',
    text: 'Think about the tools your business uses every day. How well are they working for you?',
    options: [
      {
        key: 'a',
        score: 0,
        text: "We don't really have tools. Mostly spreadsheets, texts, and paper.",
      },
      {
        key: 'b',
        score: 1,
        text: "We have some, but we're only using a fraction of what they can do",
      },
      { key: 'c', score: 2, text: "They mostly work, but we've got workarounds for the gaps" },
      {
        key: 'd',
        score: 3,
        text: 'Our tools handle what we need. The team uses them consistently.',
      },
    ],
  },
  {
    id: 'q5',
    dimension: 'tool_systems',
    text: 'How does information move between the different tools and people in your business?',
    options: [
      { key: 'a', score: 0, text: 'Manually. Someone copies info from one place to another.' },
      { key: 'b', score: 1, text: "Mostly manual, with a few shortcuts we've figured out" },
      { key: 'c', score: 2, text: 'Some things sync automatically, but plenty of gaps remain' },
      {
        key: 'd',
        score: 3,
        text: 'Our tools talk to each other. Data flows without someone re-entering it.',
      },
    ],
  },
  {
    id: 'q6',
    dimension: 'tool_systems',
    text: 'When a schedule changes or a customer needs rescheduling, how does everyone find out?',
    options: [
      { key: 'a', score: 0, text: 'Whoever knows calls or texts whoever needs to know' },
      { key: 'b', score: 1, text: 'I usually handle the communication myself' },
      { key: 'c', score: 2, text: 'We update a calendar, but sometimes people miss the change' },
      { key: 'd', score: 3, text: 'Changes sync automatically and notify the right people' },
    ],
  },

  // --- Data & Visibility (data_visibility) ---
  {
    id: 'q7',
    dimension: 'data_visibility',
    text: 'How current are your books right now?',
    options: [
      {
        key: 'a',
        score: 0,
        text: "I'm not sure. My bookkeeper handles it and I don't check often.",
      },
      { key: 'b', score: 1, text: 'A few months behind, but I check the bank account' },
      { key: 'c', score: 2, text: 'Mostly current, within a couple of weeks' },
      { key: 'd', score: 3, text: 'Up to date. I can see where we stand anytime.' },
    ],
  },
  {
    id: 'q8',
    dimension: 'data_visibility',
    text: 'When you price a job or project, how do you decide what to charge?',
    options: [
      { key: 'a', score: 0, text: 'Gut feel based on experience' },
      {
        key: 'b',
        score: 1,
        text: "I know my rough costs and add a margin, but I don't track if I was right",
      },
      {
        key: 'c',
        score: 2,
        text: "I have a pricing framework, but I don't always check profitability after",
      },
      { key: 'd', score: 3, text: 'I price from real cost data and review margins after each job' },
    ],
  },
  {
    id: 'q9',
    dimension: 'data_visibility',
    text: 'If you needed to know your top revenue source this quarter, how quickly could you get that answer?',
    options: [
      { key: 'a', score: 0, text: "I couldn't. I don't have that data in one place." },
      { key: 'b', score: 1, text: "I'd have to dig through spreadsheets or ask my bookkeeper" },
      { key: 'c', score: 2, text: 'I could piece it together, but it would take a while' },
      { key: 'd', score: 3, text: 'I can pull that up in a few minutes' },
    ],
  },

  // --- Customer Pipeline (customer_pipeline) ---
  {
    id: 'q10',
    dimension: 'customer_pipeline',
    text: 'When a new lead comes in (call, form, referral), what happens next?',
    options: [
      { key: 'a', score: 0, text: 'Whoever answers deals with it. No set process.' },
      { key: 'b', score: 1, text: 'I usually handle it personally when I get a chance' },
      { key: 'c', score: 2, text: "Someone follows up, but there's no tool tracking it" },
      { key: 'd', score: 3, text: 'Every lead goes into a pipeline with assigned follow-up steps' },
    ],
  },
  {
    id: 'q11',
    dimension: 'customer_pipeline',
    text: "What happens to a lead that doesn't buy right away?",
    options: [
      { key: 'a', score: 0, text: 'We probably lose track of them' },
      { key: 'b', score: 1, text: "I might follow up if I remember, but there's no process" },
      { key: 'c', score: 2, text: "We try to follow up, but it's inconsistent" },
      { key: 'd', score: 3, text: 'They stay in our pipeline and we follow up on a schedule' },
    ],
  },
  {
    id: 'q12',
    dimension: 'customer_pipeline',
    text: 'How do your customers hear from you after the job is done?',
    options: [
      { key: 'a', score: 0, text: "They don't, unless they call us" },
      { key: 'b', score: 1, text: "I'll reach out sometimes, but it depends on the customer" },
      { key: 'c', score: 2, text: 'We send occasional updates, but nothing consistent' },
      {
        key: 'd',
        score: 3,
        text: 'We stay in touch through regular check-ins or automated follow-ups',
      },
    ],
  },

  // --- Team Operations (team_operations) ---
  {
    id: 'q13',
    dimension: 'team_operations',
    text: 'When you onboard a new hire, how do they learn the job?',
    options: [
      { key: 'a', score: 0, text: 'They shadow someone and figure it out' },
      { key: 'b', score: 1, text: 'I train them myself, which takes me away from everything else' },
      { key: 'c', score: 2, text: "We have some training materials, but it's mostly hands-on" },
      { key: 'd', score: 3, text: "There's a structured onboarding process with docs they follow" },
    ],
  },
  {
    id: 'q14',
    dimension: 'team_operations',
    text: "How do you handle it when someone on the team isn't performing?",
    options: [
      {
        key: 'a',
        score: 0,
        text: "I usually don't notice until something breaks or a customer complains",
      },
      { key: 'b', score: 1, text: "I address it when I see it, but there's no regular process" },
      { key: 'c', score: 2, text: 'We do occasional check-ins, but nothing formal' },
      {
        key: 'd',
        score: 3,
        text: 'We have clear expectations and regular reviews. Issues surface early.',
      },
    ],
  },
  {
    id: 'q15',
    dimension: 'team_operations',
    text: 'How do you know what your team accomplished today?',
    options: [
      { key: 'a', score: 0, text: "I don't, unless I was there watching or they told me" },
      { key: 'b', score: 1, text: 'I check in with people individually throughout the day' },
      { key: 'c', score: 2, text: "We have a loose check-in process, but it's hit or miss" },
      {
        key: 'd',
        score: 3,
        text: "Everyone logs their work. I can see the team's output anytime.",
      },
    ],
  },
]

// ---------------------------------------------------------------------------
// Scoring constants (embedded in page JSON for client-side use)
// ---------------------------------------------------------------------------

/** Raw dimension score (0-9) → scaled display score (0-100) */
export const SCALED_SCORES = [0, 11, 22, 33, 44, 56, 67, 78, 89, 100] as const

export type ScoreLabel = 'needs_attention' | 'room_to_grow' | 'getting_there' | 'strong'

export interface ScoreThreshold {
  min: number
  max: number
  label: ScoreLabel
  displayLabel: string
  color: string
}

export const SCORE_THRESHOLDS: ScoreThreshold[] = [
  { min: 0, max: 22, label: 'needs_attention', displayLabel: 'Needs attention', color: '#dc2626' },
  { min: 23, max: 44, label: 'room_to_grow', displayLabel: 'Room to grow', color: '#d97706' },
  { min: 45, max: 67, label: 'getting_there', displayLabel: 'Getting there', color: '#2563eb' },
  { min: 68, max: 100, label: 'strong', displayLabel: 'Strong', color: '#16a34a' },
]

/** Total number of steps: 4 context + 15 scored */
export const TOTAL_QUESTIONS = CONTEXT_QUESTIONS.length + QUESTIONS.length
