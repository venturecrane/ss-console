/**
 * Outcome notices for the client hub (`/admin/clients/[id]`).
 *
 * The hub's own POST routes (operator-price, subscription-billing, invoices)
 * redirect back with one query key each. Every value is mapped to a sentence
 * here; an unknown value renders its raw key so a new redirect is never
 * silently swallowed.
 */

/** Outcomes of the executed-agreement recorder (ss#2641). Success and refusal
 * share one query key, because every one of these is the same question to the
 * Captain: did the firm's paper land in its portal or not. */
const AGREEMENT_NOTICES: Record<string, string> = {
  recorded: "Executed document recorded. It is now on the client's Compliance page.",
  removed: 'Executed document removed from the client portal.',
}

const AGREEMENT_REFUSALS: Record<string, string> = {
  no_instance: 'Pick an Operator instance that belongs to this client.',
  no_title: 'Name the document as the client should see it.',
  bad_date:
    'Enter the execution date as YYYY-MM-DD, and not in the future. Only executed documents belong in a client portal.',
  no_file: 'Attach the executed PDF.',
  too_large: 'That file is over 20MB. Attach a smaller PDF.',
  not_found: 'That document no longer exists.',
  failed: 'Recording the document failed. Check the logs.',
}

const BILLING_NOTICES: Record<string, string> = {
  paused: 'Billing paused.',
  resumed: 'Billing resumed.',
  cancelled: 'Billing cancelled.',
}

const ERROR_NOTICES: Record<string, string> = {
  no_billing_contact:
    'Add a contact with an email address first. Stripe keys the customer by email.',
  no_subscription_row: 'No operator subscription row exists for this client.',
  no_billing_attached: 'Billing is not attached yet.',
  bad_billing_action: 'Unknown billing action.',
  billing_server: 'Billing request failed. Check the logs.',
  bad_price: 'Enter a whole-dollar monthly price.',
  invalid_type: 'Unknown invoice type.',
  invalid_amount: 'Enter an invoice amount greater than zero.',
  missing: 'The invoice form was incomplete.',
  missing_line_items: 'Author the line item before presenting or sending an invoice.',
  invalid_transition: 'That invoice action is not valid from its current status.',
  invalid_due_date: 'Enter the new due date as YYYY-MM-DD.',
  stale_stripe_invoice:
    'The invoice was re-issued with the new due date, but the previous Stripe invoice could not be voided. Void it in the Stripe dashboard so the client cannot pay it twice.',
  server: 'Request failed. Check the logs.',
}

export interface ClientHubNotices {
  success: string | null
  error: string | null
}

export function resolveClientHubNotices(params: URLSearchParams): ClientHubNotices {
  const billing = params.get('billing')
  const error = params.get('error')
  const agreement = params.get('agreement')
  let success: string | null = null
  if (params.get('rescheduled')) {
    success = 'Due date changed. The portal and the Stripe invoice both show the new date.'
  } else if (params.get('saved')) success = 'Invoice updated.'
  else if (params.get('priced')) success = 'Monthly price saved.'
  else if (billing) success = BILLING_NOTICES[billing] ?? billing
  else if (agreement && AGREEMENT_NOTICES[agreement]) success = AGREEMENT_NOTICES[agreement]

  let resolvedError: string | null = error ? (ERROR_NOTICES[error] ?? error) : null
  if (!resolvedError && agreement && !AGREEMENT_NOTICES[agreement]) {
    resolvedError = AGREEMENT_REFUSALS[agreement] ?? agreement
  }
  return { success, error: resolvedError }
}
