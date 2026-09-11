/**
 * AES-GCM encryption for OAuth refresh tokens at rest.
 *
 * Uses the Web Crypto API (available in Cloudflare Workers).
 * Key material comes from BOOKING_ENCRYPTION_KEY (32-byte base64).
 *
 * Ciphertext format: `base64(iv):base64(ciphertext)`
 * IV is 12 bytes, randomly generated per encrypt call.
 */

const IV_BYTES = 12
const ALGORITHM = 'AES-GCM'

/**
 * Import a base64-encoded 32-byte key into a CryptoKey for AES-GCM.
 */
async function importKey(base64Key: string): Promise<CryptoKey> {
  const raw = Uint8Array.from(atob(base64Key), (c) => c.charCodeAt(0))
  return crypto.subtle.importKey('raw', raw, { name: ALGORITHM }, false, ['encrypt', 'decrypt'])
}

/**
 * Encrypt plaintext with AES-GCM.
 * Returns `base64(iv):base64(ciphertext)`.
 */
export async function encrypt(base64Key: string, plaintext: string): Promise<string> {
  const key = await importKey(base64Key)
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES))
  const encoded = new TextEncoder().encode(plaintext)

  const cipherBuffer = await crypto.subtle.encrypt({ name: ALGORITHM, iv }, key, encoded)
  const cipherBytes = new Uint8Array(cipherBuffer)

  const ivB64 = btoa(String.fromCharCode(...iv))
  const ctB64 = btoa(String.fromCharCode(...cipherBytes))

  return `${ivB64}:${ctB64}`
}

/**
 * Decrypt an `iv:ciphertext` string produced by `encrypt`.
 * Returns the original plaintext.
 */
export async function decrypt(base64Key: string, ciphertext: string): Promise<string> {
  const colonIndex = ciphertext.indexOf(':')
  if (colonIndex === -1) throw new Error('Invalid ciphertext format: missing IV separator')

  const ivB64 = ciphertext.slice(0, colonIndex)
  const ctB64 = ciphertext.slice(colonIndex + 1)

  const iv = Uint8Array.from(atob(ivB64), (c) => c.charCodeAt(0))
  const ct = Uint8Array.from(atob(ctB64), (c) => c.charCodeAt(0))

  const key = await importKey(base64Key)
  const plainBuffer = await crypto.subtle.decrypt({ name: ALGORITHM, iv }, key, ct)

  return new TextDecoder().decode(plainBuffer)
}
