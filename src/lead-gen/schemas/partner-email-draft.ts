/**
 * Partner Email Draft Schema — Pipeline 5 Output Types
 *
 * Defines the structured output contract for the referral partner nurture prompt.
 * When Claude drafts a personalized check-in email for a bookkeeper/CPA referral
 * partner, it produces a PartnerEmailDraft result matching this schema.
 *
 * @see docs/collateral/lead-automation-blueprint.md — Pipeline 5 architecture
 * @see src/lead-gen/prompts/partner-nurture-prompt.ts — The prompt that produces this output
 * @see Decision #20 — Voice Standard ("we" voice)
 * @see Decision #22 — Bookkeeper/CPA Referral Channel
 */

// ---------------------------------------------------------------------------
// Email draft output
// ---------------------------------------------------------------------------

/** Tone variant for the drafted email, driven by relationship stage. */
export type EmailTone = 'warm_checkin' | 'gentle_followup' | 'initial_outreach'

/** Preferred send day for B2B email timing. */
export type SendDay = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday'

/** Result of AI-drafted check-in email for a referral partner. */
export interface PartnerEmailDraft {
  /** Email subject line — short (5-8 words), professional, not clickbaity. */
  subject: string

  /** Email body — clean prose, plain text, suitable for Buttondown transactional email. */
  body: string

  /** Tone variant used for this email. */
  tone: EmailTone

  /** Suggested day of the week to send (Tuesday-Thursday preferred for B2B). */
  suggested_send_day: SendDay

  /** Internal notes for the human reviewer — not included in the email. */
  notes: string
}

// ---------------------------------------------------------------------------
// Input data for the prompt
// ---------------------------------------------------------------------------

/** Partner tier indicating referral potential and relationship priority. */
export type PartnerTier = 1 | 2 | 3

/** Stage of the referral relationship lifecycle. */
export type RelationshipStage =
  | 'prospect'
  | 'intro_sent'
  | 'intro_call_done'
  | 'active_partner'
  | 'dormant'

/** Input data about a referral partner, sourced from the partner prospect list. */
export interface PartnerInput {
  /** Name of the bookkeeper/CPA firm. */
  firm_name: string

  /** Primary contact name at the firm. */
  contact_name: string | null

  /** Phoenix metro sub-area (e.g., "Scottsdale", "Chandler", "Central Phoenix"). */
  area: string

  /** Phone number, if available. */
  phone: string | null

  /** Email address, if available. */
  email: string | null

  /** Firm website URL, if available. */
  website: string | null

  /** Partner tier: 1 (highest priority), 2, or 3 (lowest priority). */
  tier: PartnerTier

  /** What verticals/services the firm specializes in — from the prospect list. */
  focus_areas: string

  /** Current stage of the referral relationship. */
  relationship_stage: RelationshipStage

  /** Date of most recent contact (ISO 8601), or null if no prior contact. */
  last_contact_date: string | null

  /** Number of referrals received from this partner to us. */
  referrals_received: number

  /** Number of referrals sent from us to this partner. */
  referrals_sent: number

  /** Any relevant context or history notes. */
  notes: string | null
}
