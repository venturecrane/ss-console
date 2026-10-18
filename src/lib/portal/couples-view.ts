/**
 * The one shape every couples portal page renders from (ADR 0091 R7: a
 * partner sees their own items and shared items, never the other partner's
 * private ones).
 *
 * Pages do not read partner rows themselves. They call
 * {@link loadCouplesView}, which takes the caller's OWN person row and the
 * instance, and returns:
 *
 *   me       the caller's private answers (email, consent, Telegram binding)
 *   partner  the other partner's NAME only, plus whether they confirmed the
 *            current joint version (a shared fact: the joint section is
 *            shared, and "waiting on your partner" is the reason it is not final)
 *   joint    the shared joint section
 *
 * The other partner's email, consent and Telegram state are never loaded into
 * this object, so no page can render them by mistake.
 * tests/couples-onboarding.test.ts serializes the view and asserts the other
 * partner's private values are absent.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  partnerPublicView,
  type CouplesInstanceRow,
  type CouplesPersonRow,
} from '../db/couples-onboarding'
import { getJointState } from '../db/couples-joint'
import type { JointSettings, PersonKey } from '../hosted-agent/couples/joint-settings'

export interface CouplesView {
  agentName: string | null
  /** Set by SMD after BotFather; the Telegram step is closed until then. */
  botUsername: string | null
  me: {
    personId: string
    personKey: PersonKey
    name: string
    email: string
    consented: boolean
    consentAt: string | null
    telegramBound: boolean
    telegramBoundAt: string | null
  }
  partner: { personKey: PersonKey; name: string; confirmedCurrent: boolean } | null
  joint: {
    version: number
    settings: JointSettings
    confirmedByMe: boolean
    final: boolean
  } | null
}

export async function loadCouplesView(
  db: D1Database,
  instance: CouplesInstanceRow,
  me: CouplesPersonRow
): Promise<CouplesView> {
  const partner = await partnerPublicView(db, instance.id, me.id)
  const joint = await getJointState(db, instance.id)
  const partnerConfirmed =
    joint !== null && partner !== null ? joint.confirmedBy.some((id) => id !== me.id) : false
  return {
    agentName: instance.agent_name,
    botUsername: instance.telegram_bot_username,
    me: {
      personId: me.id,
      personKey: me.person_key,
      name: me.name,
      email: me.email,
      consented: me.consent_at !== null,
      consentAt: me.consent_at,
      telegramBound: me.telegram_id !== null && me.telegram_id !== '',
      telegramBoundAt: me.telegram_bound_at,
    },
    partner: partner
      ? { personKey: partner.person_key, name: partner.name, confirmedCurrent: partnerConfirmed }
      : null,
    joint: joint
      ? {
          version: joint.version,
          settings: joint.settings,
          confirmedByMe: joint.confirmedBy.includes(me.id),
          final: joint.final,
        }
      : null,
  }
}
