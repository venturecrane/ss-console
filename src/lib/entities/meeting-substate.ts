/**
 * Meeting-stage sub-state classifier. The Meetings list and detail both
 * need one rule for "what stage of the meeting flow is this row in?".
 * Three named buckets:
 *
 *   awaiting-booking            — booking link sent, prospect hasn't booked
 *   upcoming                    — meeting is scheduled in the future
 *   completed-awaiting-proposal — meeting completed, no quote linked yet
 *
 * Returns `null` when the entity has no meetings at all (legacy edge — a
 * meetings-stage entity without a meeting row shouldn't exist post-#467,
 * but the caller should render nothing rather than guess).
 *
 * "Hottest" sub-state (i.e., the one the operator should act on) is
 * `completed-awaiting-proposal` — that's the moment "Draft proposal from
 * meeting" makes sense. The list page sorts on this priority in #543 H13.
 */

import { findDraftableMeeting } from './draftable-meeting'

interface MeetingShape {
  id: string
  status: string
  scheduled_at: string | null
  completed_at: string | null
  created_at: string
}

interface QuoteShape {
  meeting_id: string | null
  assessment_id: string
}

export type MeetingSubstate =
  | 'awaiting-booking'
  | 'upcoming'
  | 'completed-awaiting-proposal'
  | 'past-due'
  | 'other'

export const MEETING_SUBSTATE_LABEL: Record<MeetingSubstate, string> = {
  'awaiting-booking': 'Awaiting booking',
  upcoming: 'Upcoming',
  'completed-awaiting-proposal': 'Awaiting proposal',
  'past-due': 'Past scheduled time',
  other: '',
}

export function getMeetingSubstate<M extends MeetingShape, Q extends QuoteShape>(
  meetings: M[],
  quotes: Q[],
  now: Date = new Date()
): MeetingSubstate | null {
  if (meetings.length === 0) return null

  // A draftable meeting (completed without a quote) wins regardless of
  // any newer scheduled-but-not-yet-happened meeting. The operator's
  // most valuable next action is drafting the proposal from completed
  // notes — they can deal with the next scheduled meeting after.
  if (findDraftableMeeting(meetings, quotes)) {
    return 'completed-awaiting-proposal'
  }

  // Most-recent-first by scheduled_at then created_at. The DAL helper
  // already returns rows in this order, but we don't trust ordering
  // when running against a different caller's array.
  const sorted = [...meetings].sort((a, b) => {
    const aKey = a.scheduled_at ?? a.created_at
    const bKey = b.scheduled_at ?? b.created_at
    return bKey.localeCompare(aKey)
  })
  const head = sorted[0]

  if (head.status === 'scheduled') {
    if (!head.scheduled_at) return 'awaiting-booking'
    return new Date(head.scheduled_at).getTime() < now.getTime() ? 'past-due' : 'upcoming'
  }

  // Completed/cancelled/etc. — nothing actionable left at this stage.
  return 'other'
}

/**
 * Return the next scheduled meeting (status='scheduled', scheduled_at in the
 * future), or null. Used to populate "Next meeting {date}" on a row.
 */
export function findNextScheduledMeeting<M extends MeetingShape>(
  meetings: M[],
  now: Date = new Date()
): M | null {
  const future = meetings
    .filter((m) => m.status === 'scheduled' && m.scheduled_at)
    .filter((m) => new Date(m.scheduled_at as string).getTime() >= now.getTime())
    .sort((a, b) => (a.scheduled_at as string).localeCompare(b.scheduled_at as string))
  return future[0] ?? null
}
