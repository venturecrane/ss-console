import { defineConfig } from 'astro/config'
import cloudflare from '@astrojs/cloudflare'
import tailwindcss from '@tailwindcss/vite'
import sitemap from '@astrojs/sitemap'

export default defineConfig({
  site: 'https://smd.services',
  output: 'server',
  adapter: cloudflare({
    platformProxy: {
      enabled: true,
    },
  }),
  integrations: [sitemap()],
  vite: {
    plugins: [tailwindcss()],
  },
})
