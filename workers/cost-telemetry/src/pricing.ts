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

export interface AnthropicPricing {
  models: Record<string, AnthropicModelPricing>
}

export const anthropicPricing: AnthropicPricing = anthropicJson

export function computeAnthropicCents(
  model: string,
  inputTokens: number,
  outputTokens: number,
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
  const inputCents = Math.floor((inputTokens * entry.input_per_million_cents) / 1_000_000)
  const outputCents = Math.floor((outputTokens * entry.output_per_million_cents) / 1_000_000)
  return { inputCents, outputCents, warning: null }
}
