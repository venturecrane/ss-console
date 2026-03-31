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
