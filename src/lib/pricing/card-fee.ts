/**
 * The card-processing fee rule (Operator Service Agreement §3.8).
 *
 * ACH carries no fee. Card payment adds this share of the amount paid by
 * card, and the fee appears to the client as its own authored line on
 * checkout and on every monthly invoice.
 *
 * This sits below both the invoice data layer and the Stripe layer on
 * purpose: before 2026-09-10 the rate and the line text lived in
 * `src/lib/db/invoices.ts` and `src/lib/stripe/subscriptions.ts` imported
 * them, so the Stripe layer depended on the data layer for a pricing fact
 * that belongs to neither (code review 2026-09-10, Architecture 3). A
 * reader looking for "where is the card fee defined" now has one place.
 */

export const CARD_PROCESSING_FEE_RATE = 0.03

/**
 * The authored line the client reads when an invoice is payable by card.
 * It is also how the issue route knows an invoice is a card invoice: there
 * is no separate flag column, the fee line IS the fact, and no other path
 * writes line items with this text.
 */
export const CARD_FEE_LINE_DESCRIPTION = 'Card processing fee (3%)'

export function cardProcessingFeeCents(amountCents: number): number {
  return Math.round(amountCents * CARD_PROCESSING_FEE_RATE)
}
