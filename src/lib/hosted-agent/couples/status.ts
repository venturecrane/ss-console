/**
 * Onboarding state of one couples instance, as SMD sees it: who has been
 * invited, signed in, consented, linked Telegram and confirmed the joint
 * section, and what still blocks generating the seat's customer.yaml.
 *
 * SMD-side only (admin view, generator export). Never rendered to a partner:
 * it carries both partners' private answers.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  getCouplesInstance,
  listPeople,
  type CouplesInstanceRow,
  type CouplesPersonRow,
} from '../../db/couples-onboarding'
import { getJointState, type JointState } from '../../db/couples-joint'

export interface CouplesOnboardingStatus {
  instance: CouplesInstanceRow
  people: CouplesPersonRow[]
  joint: JointState | null
  /** Plain-language reasons the generator would refuse. Empty = ready. */
  blockers: string[]
  /** Never blocking: Telegram is optional and email is the default channel. */
  pending: string[]
}

export function onboardingBlockers(
  instance: CouplesInstanceRow,
  people: CouplesPersonRow[],
  joint: JointState | null
): { blockers: string[]; pending: string[] } {
  const blockers: string[] = []
  const pending: string[] = []
  if (!instance.customer_slug) blockers.push('No customer slug assigned.')
  if (!instance.agent_name) blockers.push('No agent name.')
  if (people.length !== 2) blockers.push(`Expected two partners, found ${people.length}.`)
  for (const p of people) {
    if (!p.consent_at) blockers.push(`${p.name} (${p.person_key}) has not consented.`)
    if (!p.telegram_id)
      pending.push(
        `${p.name} (${p.person_key}) has not connected Telegram (optional; email works).`
      )
  }
  if (!joint) blockers.push('No joint settings saved.')
  else if (!joint.final) blockers.push('Joint settings are not confirmed by both partners.')
  if (!instance.telegram_bot_username)
    pending.push('No Telegram bot recorded (optional; Telegram stays off on the seat).')
  if (!instance.shared_telegram_chat) pending.push('No shared Telegram group recorded (optional).')
  return { blockers, pending }
}

export async function getCouplesOnboardingStatus(
  db: D1Database,
  intakeId: string
): Promise<CouplesOnboardingStatus | null> {
  const instance = await getCouplesInstance(db, intakeId)
  if (!instance) return null
  const people = await listPeople(db, intakeId)
  const joint = await getJointState(db, intakeId)
  const { blockers, pending } = onboardingBlockers(instance, people, joint)
  return { instance, people, joint, blockers, pending }
}
