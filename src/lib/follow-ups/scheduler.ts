/**
 * Follow-up cadence scheduler.
 *
 * Schedules follow-up sequences at the right moments:
 * - Proposal cadence: Day 2, Day 5, Day 7 after quote sent (Decision #19)
 * - Engagement cadence: review request, referral ask, safety net, feedback (Decisions #23, #26, #29)
 */

import { bulkCreateFollowUps } from '../db/follow-ups'
import type { CreateFollowUpData } from '../db/follow-ups'

/**
 * Add days to an ISO date string, returning a new ISO date string.
 */
function addDays(isoDate: string, days: number): string {
  const date = new Date(isoDate)
  date.setDate(date.getDate() + days)
  return date.toISOString()
}

/**
 * Schedule the prospect outreach follow-up cadence.
 *
 * Triggered when an entity is promoted from signal → prospect.
 * - Immediate: initial_outreach (captain reviews and sends)
 * - Day 3: follow up if no response
 * - Day 7: final follow up, then auto-demote to lost
 */
export async function scheduleProspectCadence(
  db: D1Database,
  orgId: string,
  entityId: string,
  promotedAt: string
): Promise<void> {
  const followUps: CreateFollowUpData[] = [
    {
      entity_id: entityId,
      type: 'initial_outreach',
      scheduled_for: promotedAt,
    },
    {
      entity_id: entityId,
      type: 'outreach_followup_d3',
      scheduled_for: addDays(promotedAt, 3),
    },
    {
      entity_id: entityId,
      type: 'outreach_followup_d7',
      scheduled_for: addDays(promotedAt, 7),
    },
  ]

  await bulkCreateFollowUps(db, orgId, followUps)
}

/**
 * Schedule the 3-touch proposal follow-up cadence.
 *
 * Per Decision #19:
 * - Day 2: Confirm receipt
 * - Day 5: Value add
 * - Day 7: Soft deadline
 */
export async function scheduleProposalCadence(
  db: D1Database,
  orgId: string,
  quoteId: string,
  entityId: string,
  sentAt: string
): Promise<void> {
  const followUps: CreateFollowUpData[] = [
    {
      entity_id: entityId,
      quote_id: quoteId,
      type: 'proposal_day2',
      scheduled_for: addDays(sentAt, 2),
    },
    {
      entity_id: entityId,
      quote_id: quoteId,
      type: 'proposal_day5',
      scheduled_for: addDays(sentAt, 5),
    },
    {
      entity_id: entityId,
      quote_id: quoteId,
      type: 'proposal_day7',
      scheduled_for: addDays(sentAt, 7),
    },
  ]

  await bulkCreateFollowUps(db, orgId, followUps)
}

/**
 * Schedule the engagement follow-up cadence.
 *
 * Per Decisions #23, #26, #29:
 * - At handoff: referral ask
 * - Handoff + 2 days: review request
 * - Handoff + 7 days: safety net check-in
 * - Handoff + 30 days: feedback survey
 */
export async function scheduleEngagementCadence(
  db: D1Database,
  orgId: string,
  engagementId: string,
  entityId: string,
  handoffDate: string
): Promise<void> {
  const followUps: CreateFollowUpData[] = [
    {
      entity_id: entityId,
      engagement_id: engagementId,
      type: 'referral_ask',
      scheduled_for: handoffDate,
    },
    {
      entity_id: entityId,
      engagement_id: engagementId,
      type: 'review_request',
      scheduled_for: addDays(handoffDate, 2),
    },
    {
      entity_id: entityId,
      engagement_id: engagementId,
      type: 'safety_net_checkin',
      scheduled_for: addDays(handoffDate, 7),
    },
    {
      entity_id: entityId,
      engagement_id: engagementId,
      type: 'feedback_30day',
      scheduled_for: addDays(handoffDate, 30),
    },
  ]

  await bulkCreateFollowUps(db, orgId, followUps)
}
