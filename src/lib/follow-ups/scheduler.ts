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
  clientId: string,
  sentAt: string
): Promise<void> {
  const followUps: CreateFollowUpData[] = [
    {
      client_id: clientId,
      quote_id: quoteId,
      type: 'proposal_day2',
      scheduled_for: addDays(sentAt, 2),
    },
    {
      client_id: clientId,
      quote_id: quoteId,
      type: 'proposal_day5',
      scheduled_for: addDays(sentAt, 5),
    },
    {
      client_id: clientId,
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
  clientId: string,
  handoffDate: string
): Promise<void> {
  const followUps: CreateFollowUpData[] = [
    {
      client_id: clientId,
      engagement_id: engagementId,
      type: 'referral_ask',
      scheduled_for: handoffDate,
    },
    {
      client_id: clientId,
      engagement_id: engagementId,
      type: 'review_request',
      scheduled_for: addDays(handoffDate, 2),
    },
    {
      client_id: clientId,
      engagement_id: engagementId,
      type: 'safety_net_checkin',
      scheduled_for: addDays(handoffDate, 7),
    },
    {
      client_id: clientId,
      engagement_id: engagementId,
      type: 'feedback_30day',
      scheduled_for: addDays(handoffDate, 30),
    },
  ]

  await bulkCreateFollowUps(db, orgId, followUps)
}
