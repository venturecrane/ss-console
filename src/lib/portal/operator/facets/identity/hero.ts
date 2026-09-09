/**
 * Operator hero — the shared identity + status view model (ADR 0069 Slice 2).
 *
 * The "meet your operator" lead: who the operator is (persona identity, from the
 * config projection) and whether it is alive and in-bounds (the aliveness
 * signal). One shared resolver + one shared component
 * (`components/portal/operator/facets/OperatorHero.astro`), mounted in both
 * portals per Lock 4 — the facet registry points `identity` and `status` here.
 *
 * Identity is authored config (Tier 1, real from `customer_configs`); nothing is
 * fabricated. A missing persona or missing signal renders the honest empty
 * branch, per docs/style/empty-state-pattern.md.
 */

import { personaSendAsAddress, type CustomerConfigRow } from '../../../customer-config'
import type { AlivenessSignal } from '../../aliveness'

export interface OperatorHeroModel {
  /** Persona display name, e.g. "Crane". null when no active persona. */
  name: string | null
  /** Persona title / role, e.g. "AI Case Coordinator". */
  title: string | null
  /**
   * The active persona's authored tone descriptors (customer.yaml
   * persona.tone), humanized for display ("warm-but-professional" → "warm but
   * professional"). Empty when unauthored — the line simply doesn't render.
   * Closes the voice-posture half of the blueprint §4 coverage gap; the voice
   * LIBRARY (samples) stays a later slice.
   */
  tone: string[]
  /**
   * The mailbox identity the active persona sends as (persona.send_as), or
   * null when unauthored. Authored config, rendered verbatim.
   */
  sendAs: string | null
  /**
   * Every OTHER authored persona (ADR 0011 multi-persona) — the identities
   * this operator can also operate as. Empty for single-persona seats; the
   * component renders the line only when non-empty.
   */
  alsoOperatesAs: { name: string; title: string | null }[]
  /** The live aliveness signal, or null when the customer has no fleet_status
   *  row yet (the component renders the silent empty state — never a fabricated
   *  "healthy" chip). */
  aliveness: AlivenessSignal | null
}

/** "warm-but-professional" → "warm but professional" (display only). */
function humanizeTone(t: string): string {
  return t.replace(/-/g, ' ')
}

/**
 * The landing's PERSONA block lines — the operator's own authored identity
 * detail: tone descriptors, mailbox identity, other authored identities.
 * (Captain vocabulary call, 2026-07-15: "voice" is reserved for the CLIENT's
 * voice — the ADR 0028 voice_library / sample-driven fidelity subsystem —
 * while these fields are the operator's persona. Configuration, not status:
 * they render in their own block, not on the health hero.) Each line is
 * present only when authored; an all-null result means the block is absent
 * entirely (empty-chapter rule).
 */
export function personaLines(model: OperatorHeroModel): {
  name: string | null
  role: string | null
  tone: string | null
  writesFrom: string | null
  alsoOperatesAs: string | null
} {
  return {
    name: model.name,
    role: model.title,
    // Composed as a sentence, not raw descriptor tokens (Captain, 2026-07-15:
    // "plainspoken · warm but professional · concise" read like a database
    // field). The descriptors themselves stay authored data verbatim.
    tone: toneSentence(model.tone),
    writesFrom: model.sendAs,
    alsoOperatesAs:
      model.alsoOperatesAs.length > 0
        ? model.alsoOperatesAs.map((p) => (p.title ? `${p.name} (${p.title})` : p.name)).join(', ')
        : null,
  }
}

/** "plainspoken, warm but professional, concise" → "Plainspoken, warm but professional, and concise." */
function toneSentence(tone: string[]): string | null {
  if (tone.length === 0) return null
  const joined =
    tone.length === 1
      ? tone[0]
      : tone.length === 2
        ? `${tone[0]} and ${tone[1]}`
        : `${tone.slice(0, -1).join(', ')}, and ${tone[tone.length - 1]}`
  return `${joined.charAt(0).toUpperCase()}${joined.slice(1)}.`
}

/**
 * Compose the hero view model from the config projection + the resolved
 * aliveness signal. Pure and total: a null config or no-active-persona yields
 * null identity fields; the caller passes the already-resolved signal.
 */
export function resolveOperatorHero(
  config: CustomerConfigRow | null,
  aliveness: AlivenessSignal | null
): OperatorHeroModel {
  // Select the active persona from the already-projected, typed config — no
  // second DB read, no reparse of personas_json (the projection already parsed
  // it). The rule is "the persona whose status is active", the same one the
  // skills facet applies.
  const persona = config?.personas.find((p) => p.status === 'active') ?? null
  const others = (config?.personas ?? []).filter((p) => p.status === 'active' && p !== persona)
  return {
    name: persona?.name ?? null,
    title: persona?.title ?? null,
    tone: (persona?.tone ?? []).map(humanizeTone),
    sendAs: personaSendAsAddress(persona?.send_as),
    alsoOperatesAs: others.map((p) => ({ name: p.name, title: p.title })),
    aliveness,
  }
}
