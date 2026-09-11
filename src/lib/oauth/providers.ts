/**
 * OAuth provider registry for Operator connector consent flows.
 *
 * Each provider entry knows how to exchange an authorization code for an
 * access + refresh token pair on its issuer. The portal callback at
 * src/pages/portal/products/operator/oauth/[connector]/callback.ts
 * dispatches by provider slug after validating the signed state parameter.
 * (The admin-subdomain callback at /api/oauth/callback was deleted
 * 2026-09-10: nothing issued a state that returned to it.)
 *
 * Provider slugs match the `connectors:` value shape in customer.yaml
 * (see docs/specs/operator/customer-yaml-schema.md and the per-connector
 * oauth_scopes.json files in operator/connectors/). v1 covers the
 * Phase 1 connectors that ship with first customer-zero: Microsoft Graph
 * and Google Workspace. Subsequent providers (Clio, LawPay, QuickBooks,
 * Slack, etc.) layer in by extending PROVIDERS without touching the
 * callback endpoint.
 *
 * Token responses are normalized to the shape described in ADR 0010 §
 * "Storage shape" so the store interface in src/lib/oauth/store.ts has a
 * single contract regardless of which provider issued the token.
 *
 * Client secrets are read from Cloudflare Workers env at exchange time.
 * Secrets are never logged. Error payloads from the issuer are passed to
 * the caller as opaque strings (no token material), so the callback can
 * surface a short error code to the dashboard without exposing details
 * upstream.
 */

import { googleWorkspaceProvider } from './providers/google-workspace.js'
import { microsoftGraphProvider } from './providers/ms-graph.js'

export interface ProviderTokenResponse {
  /** Short-lived access token. */
  access_token: string
  /** Long-lived refresh token, if the provider issued one. */
  refresh_token: string | null
  /** Space-separated scopes the issuer actually granted. */
  scopes: string
  /** ISO 8601 timestamp at which `access_token` expires. */
  expires_at: string
  /** ISO 8601 timestamp at which this token was obtained. */
  obtained_at: string
}

export interface OAuthProvider {
  /** Slug used in customer.yaml `connectors:` and the state parameter. */
  slug: string
  /** Human label for logs and dashboard surfaces. */
  label: string
  /** Issuer token endpoint. */
  token_url: string
  /**
   * Exchange an authorization code for tokens. Implementations call
   * `token_url` with `grant_type=authorization_code`, normalize the
   * response to `ProviderTokenResponse`, and throw on non-2xx.
   */
  exchange_code(args: { code: string; redirect_uri: string }): Promise<ProviderTokenResponse>
}

const PROVIDERS: Record<string, OAuthProvider> = {
  [microsoftGraphProvider.slug]: microsoftGraphProvider,
  [googleWorkspaceProvider.slug]: googleWorkspaceProvider,
}

export function getOAuthProvider(slug: string): OAuthProvider | null {
  return PROVIDERS[slug] ?? null
}
