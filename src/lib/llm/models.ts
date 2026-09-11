/**
 * Central model selection for all Anthropic LLM calls in the main app.
 *
 * Single source of truth for model IDs and the shared Anthropic API constants
 * (URL + version). Logical tiers decouple call sites from specific model
 * versions, so a model upgrade — or the Opus 4.8-era refresh — is a one-line
 * change here plus a test update, not a 10-file sweep.
 *
 * Tiers
 * - QUALITY: synthesis / correctness-sensitive work. Assessment extraction,
 *   quote generation, outreach drafting, dossier, deep-website, review
 *   synthesis. Quality matters more than per-call cost.
 * - FAST: high-volume, cost-sensitive analysis. Review analysis, website
 *   analysis, news summary. Throughput and price matter more than depth.
 *
 * Runtime override
 * - `modelFor(tier, env)` lets a deploy pin a different model per tier via the
 *   env vars `LLM_MODEL_QUALITY` / `LLM_MODEL_FAST` without a code change. Call
 *   it inside a request handler where `env` is in scope — never at module load
 *   (no module-level env reads in Workers; see coding-standards.md).
 * - Call sites that have no env handy can use the `QUALITY_MODEL` / `FAST_MODEL`
 *   defaults directly; these are the values `modelFor` returns when no override
 *   is set.
 *
 * Scope
 * - Covers the main Astro app under `src/`. The independent Cloudflare Workers
 *   under `workers/` are separate deploy units (own package.json/tsconfig, not
 *   in the root tsconfig) and keep their own pins — tracked as a follow-on.
 */

export const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
export const ANTHROPIC_VERSION = '2023-06-01'

export type ModelTier = 'QUALITY' | 'FAST'

/**
 * Default model ID per tier. This is the one place a model version lives.
 */
const TIER_DEFAULTS: Record<ModelTier, string> = {
  QUALITY: 'claude-sonnet-4-6',
  FAST: 'claude-haiku-4-5-20251001',
}

/** Env var name that overrides a given tier at runtime. */
function overrideKey(tier: ModelTier): string {
  return `LLM_MODEL_${tier}`
}

/**
 * Resolve the model ID for a tier, honoring an optional per-tier env override.
 * Pass the request-scoped env (or any record of string values). A blank or
 * missing override falls back to the tier default.
 */
export function modelFor(tier: ModelTier, env?: Record<string, string | undefined> | null): string {
  const override = env?.[overrideKey(tier)]
  return override && override.trim().length > 0 ? override.trim() : TIER_DEFAULTS[tier]
}

/** Default QUALITY model ID (no override applied). */
export const QUALITY_MODEL = TIER_DEFAULTS.QUALITY
