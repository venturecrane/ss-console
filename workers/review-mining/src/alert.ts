/**
 * Failure alerting via Resend email API.
 */

export interface RunSummary {
  queries: number
  discovered: number
  withReviews: number
  newBusinesses: number
  qualified: number
  belowThreshold: number
  written: number
  errors: number
  errorDetails: string[]
}

export async function sendFailureAlert(summary: RunSummary, resendApiKey: string): Promise<void> {
  const body = [
    `Review Mining pipeline run completed with errors.`,
    ``,
    `Discovery queries: ${summary.queries}`,
    `Businesses discovered: ${summary.discovered}`,
    `With recent reviews: ${summary.withReviews}`,
    `New (not deduped): ${summary.newBusinesses}`,
    `Qualified (pain >= 7): ${summary.qualified}`,
    `Below threshold: ${summary.belowThreshold}`,
    `Written to D1: ${summary.written}`,
    `Errors: ${summary.errors}`,
    ``,
    `Error details:`,
    ...summary.errorDetails.map((e) => `  - ${e}`),
  ].join('\n')

  const response = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${resendApiKey}`,
    },
    body: JSON.stringify({
      from: 'SMD Services <noreply@smd.services>',
      to: ['scott@smd.services'],
      subject: `[Review Mining] Pipeline run failed — ${summary.errors} errors, ${summary.written} signals`,
      text: body,
    }),
  })

  if (!response.ok) {
    console.error(`Resend alert failed: ${response.status}`)
  }
}
