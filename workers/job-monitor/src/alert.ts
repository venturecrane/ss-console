/**
 * Failure alerting via Resend email API.
 */

export interface RunSummary {
  queries: number
  totalResults: number
  newJobs: number
  qualified: number
  disqualified: number
  belowThreshold: number
  written: number
  errors: number
  errorDetails: string[]
  existingAppended: number
}

/**
 * Send an alert email when a pipeline run fails (errors > 0, written === 0).
 */
export async function sendFailureAlert(summary: RunSummary, resendApiKey: string): Promise<void> {
  const body = [
    `Job Monitor pipeline run completed with errors.`,
    ``,
    `Queries run: ${summary.queries}`,
    `Total results: ${summary.totalResults}`,
    `New jobs (not deduped): ${summary.newJobs}`,
    `Existing entities seen again: ${summary.existingAppended}`,
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
      subject: `[Job Monitor] Pipeline run failed — ${summary.errors} errors, ${summary.written} signals`,
      text: body,
    }),
  })

  if (!response.ok) {
    console.error(`Resend alert failed: ${response.status}`)
  }
}
