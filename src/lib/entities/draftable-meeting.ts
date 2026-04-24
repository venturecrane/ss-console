/**
 * "Draftable meeting" rule: a completed meeting whose notes haven't yet been
 * turned into a quote. Surfacing one of these to the operator (in the entity
 * list row, in the detail toolbar, in the meetings panel) is the primary
 * forward-action signal at the meetings stage.
 *
 * The detail page and the entity list both need this rule. Keeping it as a
 * pure helper means the SQL stays dumb (just `listMeetings` + `listQuotes`)
 * and both surfaces agree on what counts. If a single meeting maps to a
 * single quote (current invariant), `meeting.id` matching either
 * `quote.meeting_id` or the legacy `quote.assessment_id` is sufficient.
 *
 * Returns the most recent draftable meeting, or `null` if none exist.
 * Callers that want all draftables can use `findDraftableMeetings` instead.
 */

interface MeetingShape {
  id: string
  status: string
  completed_at: string | null
  created_at: string
}

interface QuoteShape {
  meeting_id: string | null
  assessment_id: string
}

export function findDraftableMeetings<M extends MeetingShape, Q extends QuoteShape>(
  meetings: M[],
  quotes: Q[]
): M[] {
  const meetingIdsWithQuotes = new Set(quotes.map((q) => q.meeting_id ?? q.assessment_id))
  return meetings
    .filter((m) => m.status === 'completed' && !meetingIdsWithQuotes.has(m.id))
    .sort((a, b) => {
      // Completed-most-recently first. Fall back to created_at when
      // completed_at is missing — `completed` status without a completed_at
      // is legacy data, but we still want stable ordering.
      const aKey = a.completed_at ?? a.created_at
      const bKey = b.completed_at ?? b.created_at
      return bKey.localeCompare(aKey)
    })
}

export function findDraftableMeeting<M extends MeetingShape, Q extends QuoteShape>(
  meetings: M[],
  quotes: Q[]
): M | null {
  return findDraftableMeetings(meetings, quotes)[0] ?? null
}
