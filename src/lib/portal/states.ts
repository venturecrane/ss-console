/**
 * Portal surface state resolver.
 *
 * Translates persisted record state + URL query params into a single surface
 * state plus a concrete next-step text. Each portal deep link surface
 * (invoice, proposal) narrows to one of these states before rendering.
 *
 * The resolver is pure and synchronous — it does not read the database.
 * Callers pass in the already-loaded record plus Astro.url.searchParams.
 *
 * Provider error messages (Stripe, SignWell) must NOT be surfaced raw. The
 * query param is a trusted hint from our own API surface; the copy that
 * renders is always written in portal voice.
 */

export type InvoiceSurfaceState = 'default' | 'paid' | 'declined' | 'card_expired' | 'expired'

export type ProposalSurfaceState = 'default' | 'signed' | 'declined' | 'expired' | 'superseded'

export interface InvoiceSurface {
  state: InvoiceSurfaceState
  next: string
}

export interface ProposalSurface {
  state: ProposalSurfaceState
  /** Authored "what happens next" copy. Null when no authored copy is available.
   *  Callers must render nothing (not a fallback) when null. */
  next: string | null
}

/**
 * Minimal invoice shape the resolver needs. Callers may pass the full Invoice
 * row; extra fields are ignored.
 */
interface InvoiceLike {
  paid_at: string | null
  due_date?: string | null
  status?: string | null
}

/**
 * Minimal quote shape the resolver needs.
 */
interface QuoteLike {
  status: string
  accepted_at: string | null
  expires_at: string | null
}

type SearchParamSource = URLSearchParams | { get(name: string): string | null }

function readStateParam(params: SearchParamSource | null | undefined): string | null {
  if (!params) return null
  const raw = params.get('state')
  if (!raw) return null
  return raw.trim().toLowerCase()
}

/**
 * Resolve the surface state for an invoice deep link.
 *
 * Priority order:
 *   1. `invoice.paid_at` → `paid` (server truth always wins)
 *   2. `?state=` hint (declined, card_expired, expired) after a Stripe
 *      redirect — we translate to portal voice, never surface raw provider
 *      messages
 *   3. Server-side expiration (future enhancement)
 *   4. `default` — render the pay CTA
 *
 * The `firstName` arg is the consultant first name used in the next-step
 * copy ("Text {FirstName} for a refreshed link"). When null, the "text
 * consultant" escalation collapses to a generic message.
 */
export function resolveInvoiceState(
  invoice: InvoiceLike,
  params: SearchParamSource | null,
  firstName: string | null
): InvoiceSurface {
  if (invoice.paid_at) {
    return {
      state: 'paid',
      next: `Head back to the engagement dashboard.`,
    }
  }

  const hint = readStateParam(params)

  if (hint === 'declined') {
    return {
      state: 'declined',
      next: firstName
        ? `Your card was declined. Try again, or text ${firstName} if this keeps happening.`
        : `Your card was declined. Try again.`,
    }
  }

  if (hint === 'card_expired') {
    return {
      state: 'card_expired',
      next: `The card on file has expired. Update your payment method to continue.`,
    }
  }

  if (hint === 'expired') {
    return {
      state: 'expired',
      next: firstName
        ? `This payment link has expired. Text ${firstName} for a refreshed link.`
        : `This payment link has expired.`,
    }
  }

  return {
    state: 'default',
    next: 'Secure payment via Stripe.',
  }
}

/**
 * Resolve the surface state for a proposal deep link.
 *
 * Priority:
 *   1. `status === 'accepted'` → `signed` (server truth)
 *   2. `status === 'declined'` → `declined`
 *   3. `status === 'superseded'` → `superseded` (caller supplies the newer
 *      quote ID via a separate lookup)
 *   4. `status === 'expired'` OR server-side `expires_at < now` → `expired`
 *   5. `?state=` hint for signal propagation
 *   6. `default` — render the sign surface
 *
 * nextStepText is the engagement-specific "what happens next" copy, pulled
 * from engagement.scope_summary / next_touchpoint_label by the caller. When
 * missing we fall back to a generic kickoff sentence.
 */
export function resolveProposalState(
  quote: QuoteLike,
  params: SearchParamSource | null,
  firstName: string | null,
  nextStepText: string | null = null
): ProposalSurface {
  const status = (quote.status ?? '').toLowerCase()

  if (status === 'accepted' || quote.accepted_at) {
    const next = nextStepText?.trim() || null
    return { state: 'signed', next }
  }

  if (status === 'declined') {
    return {
      state: 'declined',
      next: firstName
        ? `Text ${firstName} if you'd like to talk through a revision.`
        : `Reach out if you'd like to talk through a revision.`,
    }
  }

  if (status === 'superseded') {
    return {
      state: 'superseded',
      next: `A revised version of this proposal is available.`,
    }
  }

  const serverExpired =
    status === 'expired' ||
    (!!quote.expires_at && new Date(quote.expires_at).getTime() < Date.now())

  if (serverExpired) {
    return {
      state: 'expired',
      next: firstName
        ? `This proposal has expired. Text ${firstName} to pick it back up.`
        : `This proposal has expired.`,
    }
  }

  const hint = readStateParam(params)
  if (hint === 'expired') {
    return {
      state: 'expired',
      next: firstName
        ? `This proposal has expired. Text ${firstName} to pick it back up.`
        : `This proposal has expired.`,
    }
  }

  return {
    state: 'default',
    next: `Review and sign when you're ready.`,
  }
}

/**
 * Mobile vs desktop contact-link resolver.
 *
 * Mobile UAs use `sms:` for direct SMS compose. Desktop UAs use `tel:` so
 * click-to-call via FaceTime / dialers works and users don't hit a dead
 * `sms:` link. When `phone` is null we return null and the caller should
 * hide the contact affordance (or render a mailto fallback if configured).
 *
 * The UA regex is the standard Cloudflare / Astro SSR guidance — imperfect
 * but acceptable for a hint. Clients can always tap the inline phone number.
 */
const MOBILE_UA_RE = /iPhone|iPad|Android|Mobile/i

export interface ContactLink {
  smsHref: string | null
  telHref: string | null
  isMobile: boolean
}

export function resolveContactLink(
  phone: string | null | undefined,
  userAgent: string | null | undefined
): ContactLink {
  if (!phone) {
    return { smsHref: null, telHref: null, isMobile: false }
  }

  const digits = phone.replace(/[^+\d]/g, '')
  const isMobile = !!userAgent && MOBILE_UA_RE.test(userAgent)

  return {
    smsHref: `sms:${digits}`,
    telHref: `tel:${digits}`,
    isMobile,
  }
}
