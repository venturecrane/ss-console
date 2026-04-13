/**
 * SignWell webhook handler for SOW completion.
 *
 * Wave 1 uses the new SOW lifecycle:
 * 1. Look up signature_request by provider_request_id
 * 2. Mark request completed_pending_artifact
 * 3. Download and persist signed PDF
 * 4. Finalize quote acceptance and downstream operational records atomically
 * 5. Process outbox jobs best-effort after commit
 */

import type { SignWellWebhookPayload } from '../signwell/types'
import { finalizeCompletedSOWSignature } from '../sow/service'

export async function handleDocumentCompleted(
  db: D1Database,
  storage: R2Bucket,
  apiKey: string,
  resendApiKey: string | undefined,
  stripeApiKey: string | undefined,
  payload: SignWellWebhookPayload
): Promise<Response> {
  return finalizeCompletedSOWSignature({
    db,
    storage,
    apiKey,
    resendApiKey,
    stripeApiKey,
    payload,
  })
}
