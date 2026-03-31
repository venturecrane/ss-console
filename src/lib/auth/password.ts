/**
 * Password hashing using Web Crypto API (PBKDF2).
 *
 * Compatible with Cloudflare Workers — does not depend on Node.js crypto,
 * bcrypt, or argon2. Uses PBKDF2 with SHA-256, 100k iterations, and a
 * 16-byte random salt.
 *
 * Storage format: `pbkdf2:100000:<base64-salt>:<base64-hash>`
 */

const ALGORITHM = 'PBKDF2'
const HASH_FUNCTION = 'SHA-256'
const ITERATIONS = 100_000
const KEY_LENGTH_BITS = 256
const SALT_LENGTH_BYTES = 16

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}

function base64ToBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

async function deriveKey(password: string, salt: ArrayBuffer): Promise<ArrayBuffer> {
  const encoder = new TextEncoder()
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    encoder.encode(password),
    { name: ALGORITHM },
    false,
    ['deriveBits']
  )

  return crypto.subtle.deriveBits(
    {
      name: ALGORITHM,
      salt,
      iterations: ITERATIONS,
      hash: HASH_FUNCTION,
    },
    keyMaterial,
    KEY_LENGTH_BITS
  )
}

/**
 * Hash a plaintext password for storage.
 * Returns a string in the format `pbkdf2:100000:<base64-salt>:<base64-hash>`.
 */
export async function hashPassword(password: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_LENGTH_BYTES))
  const hash = await deriveKey(password, salt.buffer)

  const saltB64 = bufferToBase64(salt.buffer)
  const hashB64 = bufferToBase64(hash)

  return `pbkdf2:${ITERATIONS}:${saltB64}:${hashB64}`
}

/**
 * Verify a plaintext password against a stored hash.
 * Returns true if the password matches.
 */
export async function verifyPassword(password: string, storedHash: string): Promise<boolean> {
  const parts = storedHash.split(':')
  if (parts.length !== 4 || parts[0] !== 'pbkdf2') {
    return false
  }

  const iterations = parseInt(parts[1], 10)
  if (iterations !== ITERATIONS) {
    return false
  }

  const salt = base64ToBuffer(parts[2])
  const expectedHash = base64ToBuffer(parts[3])
  const actualHash = await deriveKey(password, salt)

  // Constant-time comparison
  const expected = new Uint8Array(expectedHash)
  const actual = new Uint8Array(actualHash)

  if (expected.byteLength !== actual.byteLength) {
    return false
  }

  let diff = 0
  for (let i = 0; i < expected.byteLength; i++) {
    diff |= expected[i] ^ actual[i]
  }

  return diff === 0
}
