import { defineConfig, sessionDrivers } from 'astro/config'
import cloudflare from '@astrojs/cloudflare'
import tailwindcss from '@tailwindcss/vite'
import sitemap from '@astrojs/sitemap'

// SS uses its own session layer backed by the SESSIONS KV namespace (see
// src/lib/auth/session.ts); we don't use Astro's built-in session API. Pin
// an in-memory LRU driver so the Cloudflare adapter doesn't auto-wire a
// SESSION KV binding that we wouldn't populate or provision.
// `imageService: 'passthrough'` avoids the Cloudflare Images binding — SS
// doesn't use `astro:assets` components.
export default defineConfig({
  site: 'https://smd.services',
  output: 'server',
  adapter: cloudflare({
    imageService: 'passthrough',
  }),
  session: { driver: sessionDrivers.lruCache() },
  integrations: [sitemap()],
  vite: {
    plugins: [tailwindcss()],
  },
})
