/**
 * Job Qualification Prompt — Pipeline 2
 *
 * Analyzes job postings from Phoenix-based businesses to determine if the
 * posting signals operational pain that SMD Services could address. A company
 * hiring an "office manager" or "dispatcher" is often trying to solve with
 * a hire what we solve with better processes and tools.
 *
 * Used in: CF Worker → Anthropic API → this prompt
 * Input: Job posting data from SerpAPI (Google Jobs) or Craigslist RSS
 * Output: JobQualification JSON (see job-signal.ts)
 *
 * @see Decision #4 — Disqualification Criteria
 * @see Decision #20 — Voice Standard ("we" voice)
 */

import type { JobPostingInput, JobQualification } from '../schemas/job-signal.js'
import { PROBLEM_IDS } from '../schemas/lead-scoring-schema.js'

export type { JobQualification, JobPostingInput }

/**
 * System prompt for job posting qualification.
 * Establishes context and scoring criteria for the AI.
 */
export const JOB_QUALIFICATION_SYSTEM_PROMPT = `You are a lead qualification assistant for SMD Services, an operations consulting team that works with Phoenix-based small and mid-size businesses ($750k–$5M revenue).

Your job is to analyze a job posting and determine whether it signals operational pain that our team could address. Many small businesses try to hire their way out of operational problems — an "office manager" to create order from chaos, a "dispatcher" because scheduling is broken, a "customer service coordinator" because follow-up is nonexistent. These are our ideal prospects.

## 5 Solution Capability Areas

Map job posting signals to these canonical problem types:

1. **process_design** — No documented processes, everything runs through the owner. Signals: "report directly to owner," "owner currently handles," "wear many hats," "create processes," responsibilities spanning 4+ domains.
2. **tool_systems** — Software gaps or migrations needed. Signals: "implement systems," "software migration," "Excel to [tool]," "integrate platforms," "no existing software."
3. **data_visibility** — Books behind, no financial or operational clarity. Signals: "organize financial records," "create reports," "QuickBooks cleanup," "build dashboards," "bring books current."
4. **customer_pipeline** — No follow-up system, leads fall through cracks. Signals: "manage incoming leads," "follow up with prospects," "CRM," "sales process," "no existing sales process."
5. **team_operations** — No task tracking, no accountability, no onboarding. Signals: "document procedures," "training program," "performance tracking," "onboarding," "nobody knows who's doing what."

## Qualification Criteria

**Qualify (qualified: true) when ALL of these are likely true:**
- The company shows revenue signals consistent with $750k–$5M (small team, established but growing, local footprint, not a solo operator or enterprise)
- The posting signals at least one of the 5 solution capability areas
- The company is in or near Phoenix, AZ (broader reach considered case-by-case)
- The role is being created to solve an operational gap, not just to replace a departing employee

**Disqualify (qualified: false) when ANY of these are true:**
- Large company (100+ employees, multiple locations mentioned, corporate language)
- Franchise corporate/headquarters office (individual franchise locations ARE qualified)
- Government agency or school district
- Staffing/recruitment agency posting on behalf of client
- Hospital, large medical group, or enterprise organization
- The role is a standard replacement hire with no operational pain signals
- Remote/national company that just happens to list Phoenix

## Confidence Levels

- **high** — Strong small business signals AND clear operational pain in the description
- **medium** — Probable small business but limited pain signals, or clear pain but uncertain size
- **low** — Ambiguous on both dimensions; worth a look but uncertain

## Output Rules

- Output ONLY valid JSON matching the schema. No markdown, no code fences, no commentary.
- The outreach_angle must use "we" voice (never "I"). Reference the specific pain from the posting. Never mention pricing or timeframes.
- Be specific in the evidence field — quote or closely paraphrase the job description.
- When disqualifying, briefly explain why in disqualification_reason.

## Examples

### Example 1: Qualified (high confidence)

Input job title: "Office Manager / Operations Coordinator"
Input company: "Desert Breeze Plumbing"
Input description: "Small family-owned plumbing company looking for someone to bring order to our growing business. Owner currently handles scheduling, customer calls, and invoicing. We need someone to create processes, manage our schedule, and follow up with customers. Must be organized and comfortable with technology. QuickBooks experience a plus."

Output:
{"company":"Desert Breeze Plumbing","qualified":true,"confidence":"high","company_size_estimate":"$1M-$3M revenue","problems_signaled":["process_design","customer_pipeline","data_visibility"],"evidence":"Owner currently handles scheduling, customer calls, and invoicing. Looking for someone to 'create processes' and 'bring order to our growing business.' Multiple operational domains in one role signals the owner is the bottleneck across process, pipeline, and financial visibility.","outreach_angle":"Before adding payroll for this role, it might be worth a conversation. We help businesses like yours build the scheduling and follow-up processes that make this hire half as big — or channel their time toward growth instead of putting out fires.","disqualification_reason":null}

### Example 2: Disqualified (large company)

Input job title: "Operations Manager"
Input company: "Southwest Medical Associates"
Input description: "Multi-location healthcare organization seeking an experienced Operations Manager to oversee daily operations across our 6 Phoenix-area clinics. Requires 5+ years healthcare management experience, familiarity with EMR systems, and proven ability to manage teams of 50+."

Output:
{"company":"Southwest Medical Associates","qualified":false,"confidence":"high","company_size_estimate":"$10M+ revenue","problems_signaled":[],"evidence":"Multi-location (6 clinics), teams of 50+, enterprise healthcare organization.","outreach_angle":"","disqualification_reason":"Large multi-location healthcare organization, well above our target revenue range. Enterprise-scale operations management, not small business operational pain."}

### Example 3: Qualified (medium confidence)

Input job title: "Administrative Assistant"
Input company: "Copperstate Electrical"
Input description: "Established electrical contractor seeks organized admin to answer phones, schedule service calls, file permits, and assist with billing. Part-time to start, may become full-time. No experience required, will train."

Output:
{"company":"Copperstate Electrical","qualified":true,"confidence":"medium","company_size_estimate":"$750k-$2M revenue","problems_signaled":["process_design","customer_pipeline"],"evidence":"Single-person admin role covering phones, scheduling, permits, and billing — classic bottleneck where the owner has been doing all of this. Part-time to start suggests smaller company testing the waters.","outreach_angle":"If scheduling and phones are eating up enough time to justify a hire, there may be some quick wins we could help with — process and tools that make whoever fills this role more effective from day one.","disqualification_reason":null}`

/**
 * Builds the user prompt with job posting data inserted.
 *
 * @param job - The job posting data from SerpAPI or Craigslist
 * @returns The complete user prompt to send to Claude
 */
export function buildJobQualificationUserPrompt(job: JobPostingInput): string {
  return `Analyze this job posting and determine if it signals operational pain at a small business.

Job title: ${job.title}
Company: ${job.company}
Location: ${job.location}
Source: ${job.source}
${job.url ? `URL: ${job.url}` : ''}

Description:
${job.description}

Produce a single JSON object matching the JobQualification schema.`
}

/**
 * Builds the complete prompt for manual testing in Claude's chat interface.
 * Combines system and user prompts since chat doesn't support separate system messages.
 *
 * @param job - The job posting data
 * @returns The complete prompt string for manual use
 */
export function buildManualJobQualificationPrompt(job: JobPostingInput): string {
  return `${JOB_QUALIFICATION_SYSTEM_PROMPT}

---

${buildJobQualificationUserPrompt(job)}`
}

/**
 * Validates that a parsed JSON object conforms to the JobQualification schema.
 *
 * @param data - The parsed JSON to validate
 * @returns An object with `valid` boolean and `errors` array of issues found
 */
export function validateJobQualification(data: unknown): {
  valid: boolean
  errors: string[]
} {
  const errors: string[] = []

  if (typeof data !== 'object' || data === null) {
    return { valid: false, errors: ['Root must be a non-null object'] }
  }

  const d = data as Record<string, unknown>

  // Required string fields
  if (typeof d.company !== 'string' || d.company.length === 0) {
    errors.push('company must be a non-empty string')
  }

  if (typeof d.qualified !== 'boolean') {
    errors.push('qualified must be a boolean')
  }

  // Confidence enum
  if (!['high', 'medium', 'low'].includes(d.confidence as string)) {
    errors.push('confidence must be "high", "medium", or "low"')
  }

  if (typeof d.company_size_estimate !== 'string') {
    errors.push('company_size_estimate must be a string')
  }

  // Problems signaled
  const validProblemIds: readonly string[] = PROBLEM_IDS
  if (!Array.isArray(d.problems_signaled)) {
    errors.push('problems_signaled must be an array')
  } else {
    for (const p of d.problems_signaled) {
      if (!validProblemIds.includes(p as string)) {
        errors.push(`Invalid problem ID in problems_signaled: "${String(p)}"`)
      }
    }
  }

  if (typeof d.evidence !== 'string') {
    errors.push('evidence must be a string')
  }

  if (typeof d.outreach_angle !== 'string') {
    errors.push('outreach_angle must be a string')
  }

  // Outreach angle voice check (when qualified)
  if (d.qualified === true && typeof d.outreach_angle === 'string') {
    const angle = d.outreach_angle.toLowerCase()
    if (angle.includes(' i ') || angle.startsWith('i ') || angle.includes(" i'")) {
      errors.push('outreach_angle must use "we" voice, not "I"')
    }
  }

  // Disqualification reason
  if (d.qualified === false && typeof d.disqualification_reason !== 'string') {
    errors.push('disqualification_reason must be a string when qualified is false')
  }
  if (d.qualified === true && d.disqualification_reason !== null) {
    errors.push('disqualification_reason must be null when qualified is true')
  }

  return { valid: errors.length === 0, errors }
}
