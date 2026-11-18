/**
 * Pricing JSON references for the cost telemetry worker.
 *
 * Source of truth is the JSON files at
 * `operator/adapter/cost_telemetry/`. The Python ingest module
 * loads them at runtime; the TS worker can't read files in a Worker
 * runtime, so the JSON shapes are imported here. Keep these in sync
 * with the JSON files — the unit tests assert structural parity.
 */

import anthropicJson from '../../../operator/adapter/cost_telemetry/anthropic_pricing.json'

export interface AnthropicModelPricing {
  input_per_million_cents: number
  output_per_million_cents: number
  /** Per-model cache-read multiple of the input rate, when it differs from the table-wide one. */
  cache_read_multiplier?: number
}

/** Table-wide multiples applied on top of a model's per-token rates. */
export interface AnthropicMultipliers {
  /** Batch API discount on the whole row. */
  batch: number
  cache_write_5m: number
  cache_write_1h: number
  cache_read: number
}

export interface AnthropicPricing {
  _meta: { multipliers: AnthropicMultipliers }
  models: Record<string, AnthropicModelPricing>
}

export const anthropicPricing: AnthropicPricing = anthropicJson

/** One usage row's tokens, split the way Anthropic bills them. */
export interface TokenUsage {
  uncachedInputTokens: number
  cacheReadTokens: number
  cacheWrite5mTokens: number
  cacheWrite1hTokens: number
  outputTokens: number
  /** True when the row ran on the Batch API (service_tier 'batch'). */
  batch: boolean
}

/**
 * Cents for one usage row, priced the way the invoice prices it: cache
 * reads and cache writes at their own multiples of the input rate, and
 * the Batch API discount on the whole row.
 *
 * Until 2026-11-16 every input token was priced at the full input rate.
 * Most input on a seat is cache reads, so the table recorded $4,702 for
 * October 2026 against $2,864.24 billed. This math reproduces the bill
 * for that month to the dollar (probe against the Admin cost report,
 * 2026-11-16).
 *
 * Returns unrounded cents; the caller rounds once, after summing a day.
 */
export function computeUsageCents(
  model: string,
  usage: TokenUsage,
  pricing: AnthropicPricing = anthropicPricing
): { inputCents: number; outputCents: number; warning: string | null } {
  const entry = pricing.models[model]
  if (!entry) {
    return {
      inputCents: 0,
      outputCents: 0,
      warning: `model ${model} not in anthropic_pricing.json; wrote tokens with amount_cents=0`,
    }
  }
  const m = pricing._meta.multipliers
  const tier = usage.batch ? m.batch : 1
  const weightedInput =
    usage.uncachedInputTokens +
    usage.cacheReadTokens * (entry.cache_read_multiplier ?? m.cache_read) +
    usage.cacheWrite5mTokens * m.cache_write_5m +
    usage.cacheWrite1hTokens * m.cache_write_1h
  return {
    inputCents: (weightedInput * entry.input_per_million_cents * tier) / 1_000_000,
    outputCents: (usage.outputTokens * entry.output_per_million_cents * tier) / 1_000_000,
    warning: null,
  }
}
