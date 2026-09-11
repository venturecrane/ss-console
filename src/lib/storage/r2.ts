/**
 * R2 storage helpers for file uploads.
 *
 * Uploaded files are stored under a structured, tenant-scoped key:
 *   {orgId}/assessments/{assessmentId}/transcript/{nameHash}/{filename}
 *   {orgId}/engagements/{engagementId}/docs/{nameHash}/{filename}
 *
 * The `{nameHash}` segment exists because `{filename}` is lossy — see
 * `uploadKeyLeaf` — and two different uploads that sanitized to the same
 * string used to write the same key, the second destroying the first
 * (ss#2315).
 */

/** Sanitize a client-supplied filename for use in a key: alphanumerics, dots,
 * hyphens and underscores survive; everything else becomes an underscore.
 * Many-to-one on purpose — the hash segment is what makes the key unique. */
function sanitizeFileName(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '_')
}

/**
 * The two trailing segments of an upload key: a short digest of the ORIGINAL
 * filename, then the sanitized filename.
 *
 * Three properties, each load-bearing:
 *
 * - **Distinct names never collide.** `Scope (final).pdf` and
 *   `Scope [final].pdf` sanitize identically but hash differently, so neither
 *   overwrites the other. For engagement deliverables — prefix-listed and
 *   rendered on the client portal — a collision silently removed a document
 *   the client could see.
 * - **The same name still replaces.** Hashing the name rather than the bytes
 *   keeps re-uploading a corrected file a replacement, not a second entry the
 *   UI has no way to remove.
 * - **Display is unchanged.** The filename stays the LAST segment, so every
 *   reader's `key.split('/').pop()` shows what it always showed, and the key
 *   stays under its existing prefix, so prefix listing and the portal's
 *   path-traversal checks are unaffected.
 *
 * Keys written before this existed are untouched and stay reachable: readers
 * use the key recorded in D1 or the key returned by `list()`, never a
 * recomputed one. No migration, nothing orphaned.
 */
export async function uploadKeyLeaf(originalName: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(originalName))
  const nameHash = Array.from(new Uint8Array(digest).slice(0, 4))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
  return `${nameHash}/${sanitizeFileName(originalName)}`
}

/**
 * Structured key for an engagement deliverable.
 *
 * @param orgId - Organization ID for tenant scoping
 * @param engagementId - Engagement the document belongs to
 * @param originalName - The uploaded file's name, unsanitized
 */
export async function getEngagementDocumentKey(
  orgId: string,
  engagementId: string,
  originalName: string
): Promise<string> {
  return `${orgId}/engagements/${engagementId}/docs/${await uploadKeyLeaf(originalName)}`
}

/**
 * Structured key for an Operator instance's executed agreement document
 * (ss#2641). Instance-scoped because the portal's Compliance surface is
 * instance-addressed and an entity may hold several operator instances.
 *
 * Keyed by the D1 ROW, not by the filename (A3, claims-2026-09-04). The
 * `uploadKeyLeaf` name-hash convention makes "the same name replaces", which
 * is right for a deliverable and wrong for executed paper: an amendment
 * uploaded under the same filename as the agreement it amends silently
 * overwrote the original's bytes and, through the UNIQUE storage_key, its
 * row. Every executed document is its own row, so the row id is the segment
 * that keeps two documents apart — the same shape as SOW revision keys
 * ({@link getSowRevisionSignedKey}).
 *
 * The `{orgId}/` prefix is load-bearing: the portal download endpoint's
 * traversal check accepts exactly two conventions, and this joins the first.
 * The filename stays the LAST segment (readers show `key.split('/').pop()`).
 * Authorization itself is by D1 row, not by this prefix — see
 * src/lib/portal/agreement-documents.ts.
 *
 * @param orgId - Organization ID for tenant scoping
 * @param instanceSlug - The operator instance's customer_slug
 * @param documentId - The `operator_agreement_documents.id` the object backs
 * @param originalName - The uploaded file's name, unsanitized
 */
export function getOperatorAgreementKey(
  orgId: string,
  instanceSlug: string,
  documentId: string,
  originalName: string
): string {
  return `${orgId}/operator/${instanceSlug}/agreements/${documentId}/${sanitizeFileName(originalName)}`
}

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
  const key = `${orgId}/assessments/${assessmentId}/transcript/${await uploadKeyLeaf(file.name)}`

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
