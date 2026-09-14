// Shared frame copy for the Operator vertical pack pages.
//
// These strings are identical-by-design across all 12 packs. Keeping them here
// (rather than re-typing them in each page) prevents drift and keeps the one
// place a reviewer has to check for the shared scaffolding. Per-vertical content
// (the starting templates, the day-one narrative, the line) lives in each pack
// file and is sourced from operator/verticals/<vertical>/vertical.yaml.
//
// This module is scanned by tests/landing-page.test.ts for voice violations, so
// it must stay in firm "we"/"you" voice with no em dashes.

// A direct lander (arriving from search, not from /operator) may not have seen
// the concept yet. This link points them to the full explanation without the
// pack page having to re-run the landing's argument.
export const coldLanderLink = {
  text: 'New to the Operator? See how it works',
  href: '/operator',
}

// The pricing line shown in every pack's closing section.
export const ctaPricingLine =
  'Pricing is a flat monthly subscription, scoped to the seat we build together, and we walk through it before any setup begins.'

// Per-vertical display name + seat phrase, used to build the answer-engine FAQ
// (packFaqs below) uniformly across all 12 packs. The seat phrase is written to
// read mid-sentence (lowercase), and the vertical reads as a plural noun phrase.
// These mirror the labels on /industries so the two never drift.
export interface PackMeta {
  vertical: string
  seat: string
}

export const PACK_META: Record<string, PackMeta> = {
  'law-firm': { vertical: 'law firms', seat: 'the PI caseload, records to settlement' },
  insurance: { vertical: 'insurance agencies', seat: 'the service and renewal desk' },
  accounting: { vertical: 'accounting firms', seat: 'the document chase through busy season' },
  ria: { vertical: 'advisory firms', seat: 'client service and operations' },
  mortgage: { vertical: 'mortgage brokers', seat: 'the pipeline through clear-to-close' },
  veterinary: { vertical: 'veterinary clinics', seat: 'the front desk' },
  dental: { vertical: 'dental practices', seat: 'the front office' },
  'med-spa': { vertical: 'med spas', seat: 'booking, membership, and rebooking' },
  title: { vertical: 'title and escrow companies', seat: 'the closing desk' },
  'property-management': {
    vertical: 'property managers',
    seat: 'resident and owner coordination',
  },
  'marketing-agency': { vertical: 'marketing agencies', seat: 'account coordination' },
  'home-services': { vertical: 'home services businesses', seat: 'answering and booking the call' },
}

// The answer-engine FAQ for a pack. Three decision questions a buyer (or an AI
// answer-engine) asks, answered in answer-shaped form. Built from PACK_META so all
// 12 packs stay uniform and accurate. Emitted as FAQPage JSON-LD + on-page copy by
// PackClosing. Stays in firm "we"/"you" voice with no em dashes.
export function packFaqs(slug: string): { q: string; a: string }[] {
  const meta = PACK_META[slug]
  if (!meta) return []
  const { vertical, seat } = meta
  return [
    {
      q: `Does the Operator work with the tools ${vertical} already use?`,
      a: 'Yes. It works inside the accounts and systems you already run, connecting to your tools rather than replacing them. The more systems you run, the more the Operator holds together.',
    },
    {
      q: `Is the Operator built for ${vertical}?`,
      a: `It starts from the ${vertical} pack, a head start already shaped around ${seat}. We configure it with you, and you customize it to how your business actually runs, so you are not starting from a blank page.`,
    },
    {
      q: 'What stays with your team?',
      a: "You set the line. The Operator handles the coordination and routes anything that needs a person's judgment to a person. It works in your own walled-off environment, every action is logged where you can see it, and the configuration and the off switch stay with you.",
    },
  ]
}

// The industries index shown on /industries and /operator. Display names are
// title-case nouns; the seat phrase is PACK_META's, capitalized, so the tiles,
// the pack FAQ, and the answer-engine copy are one string (2026-09-14; the two
// pages previously carried hand-copied arrays that could drift).
const INDUSTRY_NAMES: Array<{ name: string; slug: string }> = [
  { name: 'Law firms', slug: 'law-firm' },
  { name: 'Insurance agencies', slug: 'insurance' },
  { name: 'Accounting firms', slug: 'accounting' },
  { name: 'Advisory firms', slug: 'ria' },
  { name: 'Mortgage brokers', slug: 'mortgage' },
  { name: 'Veterinary clinics', slug: 'veterinary' },
  { name: 'Dental practices', slug: 'dental' },
  { name: 'Med spas', slug: 'med-spa' },
  { name: 'Title and escrow', slug: 'title' },
  { name: 'Property management', slug: 'property-management' },
  { name: 'Marketing agencies', slug: 'marketing-agency' },
  { name: 'Home services', slug: 'home-services' },
]

export const INDUSTRIES: Array<{ name: string; seat: string; slug: string }> = INDUSTRY_NAMES.map(
  ({ name, slug }) => {
    const seat = PACK_META[slug].seat
    return { name, slug, seat: seat.charAt(0).toUpperCase() + seat.slice(1) }
  }
)
