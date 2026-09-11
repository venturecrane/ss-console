/**
 * Copy and link tables for the Operator Settings page (the ACT surface):
 * tier vocabulary in the client's own framing, the banner each redirect
 * status resolves to, and the operable sub-surfaces the page links to.
 *
 * Split out of settings/index.astro on 2026-09-11 (review 2026-09-10,
 * Architecture 3). Pure tables and one resolver; no I/O, so the page keeps
 * every read and every write.
 */

// Client language for the tier vocabulary. The internal terms (flag-only /
// prepare-and-route / auto-handle) are ours; the client reads what the level
// DOES, in the letter's own framing.
export const TIER_LABELS: Record<string, string> = {
  'flag-only': 'Surfaces it for you',
  'prepare-and-route': 'Prepares it for someone to send',
  'auto-handle': 'Handles it end to end',
}

export type Banner = { text: string; tone: 'success' | 'error' | 'info' }

// Actionable reason copy for `status=failed`. Unknown reasons collapse to a
// generic retry message rather than leaking a raw reason code to the client.
const FAILED_REASONS: Record<string, string> = {
  missing_refresh_token:
    'The connection completed but did not return offline access. Remove the connection and reconnect, approving offline access when prompted.',
  denied: 'The permission request was declined. Reconnect and approve access to finish.',
  scope_mismatch:
    'The permissions granted did not match what the connection needs. Reconnect and approve the full set.',
}

function resolveBanner(status: string | null, reason: string | null): Banner | null {
  if (!status) return null
  if (status === 'connected') return { text: 'System connected.', tone: 'success' }
  if (status === 'failed') {
    const specific = reason ? FAILED_REASONS[reason] : null
    return {
      text:
        specific ??
        'We could not complete the connection. Try again, or contact us if the problem persists.',
      tone: 'error',
    }
  }
  return null
}

const PAUSE_BANNERS: Record<string, Banner> = {
  paused: { text: 'The Operator is paused. It stays off until an admin resumes it.', tone: 'info' },
  resumed: { text: 'The Operator is resumed and running.', tone: 'success' },
  pause_reason_required: { text: 'A reason is required to pause or resume.', tone: 'error' },
  pause_gate_unreachable: {
    text: 'We could not reach the Operator to change its state. Nothing was changed. Try again, or contact us if the problem persists.',
    tone: 'error',
  },
  pause_not_operable: {
    text: 'Pause and resume are not enabled for your account yet.',
    tone: 'error',
  },
  pause_invalid_action: { text: 'Invalid pause action.', tone: 'error' },
  entitlement_applied: {
    text: 'Done. The Operator runs this routine at the new level from now on; the change is logged below.',
    tone: 'success',
  },
  entitlement_no_change: { text: 'That routine is already set that way.', tone: 'info' },
  entitlement_reason_required: { text: 'A reason is required to change a setting.', tone: 'error' },
  entitlement_not_operable: {
    text: 'Changing autonomy settings is not enabled for your account yet.',
    tone: 'error',
  },
  entitlement_rejected_above_letter_ceiling: {
    text: 'That level is above what this routine is set up to do. Changing it is a scope conversation with us, not a settings change.',
    tone: 'error',
  },
  entitlement_rejected_no_graduation_path: {
    text: 'This routine only surfaces work; it has no drafting or sending step to raise.',
    tone: 'error',
  },
  entitlement_config_unreadable: {
    text: 'We could not read this Operator configuration. Nothing was changed.',
    tone: 'error',
  },
  entitlement_failed: {
    text: 'We could not reach the Operator to change this setting. Nothing was changed. Try again, or contact us if the problem persists.',
    tone: 'error',
  },
}

/**
 * The banner for a `?status=` redirect: pause and entitlement outcomes first,
 * then the connect-flow statuses (with `reason` narrowing the failed copy).
 */
export function resolveSettingsBanner(status: string | null, reason: string | null): Banner | null {
  return (status && PAUSE_BANNERS[status]) || resolveBanner(status, reason)
}

// The operable sub-surfaces. Each links to an existing page; a client (or an
// OAuth redirect) always lands somewhere real.
//
// "Advanced" was removed here by #1966 because its resolver failed by
// construction for every customer and the page rendered a raw configuration
// error — a link to a broken page is worse than no link. Both halves of that
// are fixed (#2089): the resolver validates the editable surface and tolerates
// the locked fields the projection cannot carry (#1965), and the page no
// longer reports a change as applied when nothing was written. It also now
// carries the output-shape authoring form, which is the one control here that
// reaches the running Operator (ADR 0083).
export function settingsLinks(
  operatorBase: string
): ReadonlyArray<{ href: string; label: string; description: string }> {
  return [
    {
      href: `${operatorBase}/connections`,
      label: 'Connections',
      description:
        'The systems your Operator works with. Connect a new one or review what is linked.',
    },
    {
      href: `${operatorBase}/settings/users`,
      label: 'Team access',
      description: 'Who can see and manage this Operator, and what each person can do.',
    },
    {
      href: `${operatorBase}/settings/advanced`,
      label: 'Advanced',
      description:
        'Describe how your Operator should sound and shape its work, and review the rest of its configuration.',
    },
  ]
}
