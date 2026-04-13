/**
 * R2 storage helpers for file uploads.
 *
 * Transcript files are stored with a structured key pattern:
 *   {orgId}/assessments/{assessmentId}/transcript/{filename}
 *
 * This ensures tenant isolation and logical grouping within the R2 bucket.
 */

/**
 * Upload a transcript file to R2.
 *
 * @param r2 - The R2 bucket binding (STORAGE)
 * @param orgId - Organization ID for tenant scoping
 * @param assessmentId - Assessment this transcript belongs to
 * @param file - The File object from form data
 * @returns The R2 key where the file was stored
 */
export async function uploadTranscript(
  r2: R2Bucket,
  orgId: string,
  assessmentId: string,
  file: File
): Promise<string> {
  // Sanitize filename — keep only alphanumeric, dots, hyphens, underscores
  const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, '_')
  const key = `${orgId}/assessments/${assessmentId}/transcript/${safeName}`

  const arrayBuffer = await file.arrayBuffer()

  await r2.put(key, arrayBuffer, {
    httpMetadata: {
      contentType: file.type || 'application/octet-stream',
    },
    customMetadata: {
      originalName: file.name,
      uploadedAt: new Date().toISOString(),
    },
  })

  return key
}

/**
 * Get a transcript URL or key for download.
 *
 * In Phase 1, this returns the R2 key directly. The admin can
 * use an API route to stream the file content.
 *
 * @param key - The R2 key of the stored transcript
 * @returns The key (or presigned URL in future phases)
 */
export function getTranscriptUrl(key: string): string {
  return key
}

/**
 * Retrieve a transcript object from R2.
 *
 * @param r2 - The R2 bucket binding
 * @param key - The R2 key of the stored transcript
 * @returns The R2Object or null if not found
 */
export async function getTranscript(r2: R2Bucket, key: string): Promise<R2ObjectBody | null> {
  return r2.get(key)
}

// ---------------------------------------------------------------------------
// SOW PDF helpers
// ---------------------------------------------------------------------------

/**
 * Retrieve a PDF object from R2.
 *
 * @param r2 - The R2 bucket binding
 * @param key - The R2 key of the stored PDF
 * @returns The R2Object or null if not found
 */
export async function getPdf(r2: R2Bucket, key: string): Promise<R2ObjectBody | null> {
  return r2.get(key)
}

/**
 * Structured key for an immutable unsigned SOW revision artifact.
 */
export function getSowRevisionUnsignedKey(
  orgId: string,
  quoteId: string,
  revisionId: string
): string {
  return `orgs/${orgId}/quotes/${quoteId}/sow/${revisionId}/unsigned.pdf`
}

/**
 * Structured key for the signed artifact produced from an immutable SOW revision.
 */
export function getSowRevisionSignedKey(
  orgId: string,
  quoteId: string,
  revisionId: string
): string {
  return `orgs/${orgId}/quotes/${quoteId}/sow/${revisionId}/signed.pdf`
}

export async function uploadSowRevisionPdf(
  r2: R2Bucket,
  key: string,
  pdf: Uint8Array,
  metadata: Record<string, string>
): Promise<string> {
  await r2.put(key, pdf, {
    httpMetadata: {
      contentType: 'application/pdf',
    },
    customMetadata: metadata,
  })

  return key
}

export async function uploadSignedSowRevisionPdf(
  r2: R2Bucket,
  key: string,
  pdf: Uint8Array,
  metadata: Record<string, string>
): Promise<string> {
  await r2.put(key, pdf, {
    httpMetadata: {
      contentType: 'application/pdf',
    },
    customMetadata: metadata,
  })

  return key
}

// ---------------------------------------------------------------------------
// Document listing and streaming helpers
// ---------------------------------------------------------------------------

/**
 * List all R2 objects under a given prefix.
 *
 * Used to enumerate documents for a client engagement:
 *   {orgId}/engagements/{engId}/docs/*
 *
 * @param r2 - The R2 bucket binding
 * @param prefix - The key prefix to list under
 * @returns Array of R2Object metadata
 */
export async function listDocuments(r2: R2Bucket, prefix: string): Promise<R2Object[]> {
  const listed = await r2.list({ prefix })
  return listed.objects
}

/**
 * Get an R2 object for streaming download.
 *
 * @param r2 - The R2 bucket binding
 * @param key - The R2 key of the document
 * @returns The R2ObjectBody for streaming, or null if not found
 */
export async function streamDocument(r2: R2Bucket, key: string): Promise<R2ObjectBody | null> {
  return r2.get(key)
}
