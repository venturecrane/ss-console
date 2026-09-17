/**
 * The ONE client-language map for the routine autonomy levels.
 *
 * Before 2026-09-17 three surfaces carried three copies: the one-pager's
 * Duties said "Handles it", Settings and the Activity feed said "Handles it end
 * to end", so the same routine read two ways on two pages. This module is the
 * only place the words live; every portal surface imports it.
 *
 * The wording is the one the Captain locked for the Duties grid on 2026-07-14.
 * The reader is a colleague, not a technician: the internal tokens
 * (flag-only / prepare-and-route / auto-handle) never reach a client page.
 */

import type { RoutineTier } from '../../operator/routine-grid'

export const CLIENT_TIER_SENTENCE: Readonly<Record<RoutineTier, string>> = {
  'flag-only': 'Surfaces it',
  'prepare-and-route': 'Prepares it for you',
  'auto-handle': 'Handles it',
}

/**
 * The agreement's own names for the three levels, as Schedule A-1 writes a
 * plain starting setting. A row whose `start_verbatim` is exactly one of these
 * adds nothing beyond the level sentence, so the Duties row does not repeat it;
 * any other starting setting ("On request (a run builds it, ...)") is the
 * routine's definition and renders verbatim.
 */
export const AGREEMENT_TIER_NAME: Readonly<Record<RoutineTier, string>> = {
  'flag-only': 'Flag-only',
  'prepare-and-route': 'Prepare-and-route',
  'auto-handle': 'Auto-handle',
}
