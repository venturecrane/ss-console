/**
 * Failure alerting via Resend email API.
 */

export interface RunSummary {
  sources: number
  totalPermits: number
  newPermits: number
  qualified: number
  disqualified: number
  belowThreshold: number
  written: number
  errors: number
  errorDetails: string[]
}

export async function sendFailureAlert(summary: RunSummary, resendApiKey: string): Promise<void> {
  const body = [
    `New Business pipeline run completed with errors.`,
    ``,
    `Sources checked: ${summary.sources}`,
    `Total permits: ${summary.totalPermits}`,
    `New (not deduped): ${summary.newPermits}`,
    `Qualified: ${summary.qualified}`,
    `Disqualified: ${summary.disqualified}`,
    `Below pain threshold: ${summary.belowThreshold}`,
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
      to: ['team@smd.services'],
      subject: `[New Business] Pipeline run failed — ${summary.errors} errors, ${summary.written} signals`,
      text: body,
    }),
  })

  if (!response.ok) {
    console.error(`Resend alert failed: ${response.status}`)
  }
}
