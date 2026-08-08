/**
 * Client-side proof-of-work (hashcash) for the auth forms.
 *
 * The server issues a signed challenge (`GET /auth/pow-challenge`); the client
 * finds a `nonce` such that `SHA-256(challenge + "." + nonce)` has at least
 * `difficulty` leading zero bits. Solved asynchronously with WebCrypto so the
 * UI thread never blocks. The convention mirrors `app.pow` exactly — a wrong
 * separator or endianness here means every submit gets a 403.
 */

export interface PowChallenge {
  challenge: string
  issued_at: number
  signature: string
  difficulty: number
  ttl_seconds: number
}

export interface PowProof {
  challenge: string
  issued_at: number
  signature: string
  nonce: string
}

/** Leading zero bits of a 256-bit digest (big-endian byte order). */
function leadingZeroBits(bytes: Uint8Array): number {
  let zeros = 0
  for (const byte of bytes) {
    if (byte === 0) {
      zeros += 8
      continue
    }
    let v = byte
    while ((v & 0x80) === 0) {
      zeros += 1
      v <<= 1
    }
    break
  }
  return zeros
}

/**
 * Solve a challenge: return a nonce whose digest meets the difficulty.
 * Expected ~2^difficulty iterations; at 16 bits that is a few hundred ms.
 */
export async function solvePow(challenge: string, difficulty: number): Promise<string> {
  const encoder = new TextEncoder()
  const prefix = `${challenge}.`
  let nonce = 0
  for (;;) {
    const digest = await crypto.subtle.digest('SHA-256', encoder.encode(prefix + nonce))
    if (leadingZeroBits(new Uint8Array(digest)) >= difficulty) return String(nonce)
    nonce += 1
  }
}

/** Fetch a challenge and produce a proof (skips the work when disabled). */
export async function makePowProof(
  getChallenge: () => Promise<PowChallenge>,
): Promise<PowProof | null> {
  const challenge = await getChallenge()
  if (challenge.difficulty <= 0) return null
  const nonce = await solvePow(challenge.challenge, challenge.difficulty)
  return {
    challenge: challenge.challenge,
    issued_at: challenge.issued_at,
    signature: challenge.signature,
    nonce,
  }
}
