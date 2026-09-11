/**
 * SignWell API client using raw fetch.
 *
 * No SDK — uses direct HTTP calls to the SignWell REST API v1.
 * All methods require a valid API key and return typed responses.
 *
 * API base: https://www.signwell.com/api/v1/
 */

import type { SignWellCreateDocumentRequest, SignWellDocument } from './types'

const SIGNWELL_API_BASE = 'https://www.signwell.com/api/v1'

/**
 * Create a signature request by sending a document to SignWell.
 *
 * Uploads the PDF (base64 or URL), sets up signer fields, and
 * configures the webhook callback for completion notifications.
 *
 * @param apiKey - SignWell API key
 * @param params - Document creation parameters
 * @returns The created SignWell document
 */
export async function createSignatureRequest(
  apiKey: string,
  params: SignWellCreateDocumentRequest
): Promise<SignWellDocument> {
  const response = await fetch(`${SIGNWELL_API_BASE}/documents`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Api-Key': apiKey,
    },
    body: JSON.stringify(params),
  })

  if (!response.ok) {
    const errorBody = await response.text()
    throw new Error(`SignWell createSignatureRequest failed (${response.status}): ${errorBody}`)
  }

  return response.json()
}

/**
 * Download the completed (signed) PDF for a document.
 *
 * Only available after all signers have completed signing.
 *
 * @param apiKey - SignWell API key
 * @param docId - SignWell document ID
 * @returns The signed PDF as a Uint8Array
 */
export async function getSignedPdf(apiKey: string, docId: string): Promise<Uint8Array> {
  const response = await fetch(`${SIGNWELL_API_BASE}/documents/${docId}/completed_pdf`, {
    method: 'GET',
    headers: {
      'X-Api-Key': apiKey,
    },
  })

  if (!response.ok) {
    const errorBody = await response.text()
    throw new Error(`SignWell getSignedPdf failed (${response.status}): ${errorBody}`)
  }

  const buffer = await response.arrayBuffer()
  return new Uint8Array(buffer)
}
