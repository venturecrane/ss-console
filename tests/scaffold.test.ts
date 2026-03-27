import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

describe('scaffold', () => {
  it('astro config exists and references smd.services', () => {
    const config = readFileSync(resolve('astro.config.mjs'), 'utf-8')
    expect(config).toContain('smd.services')
  })

  it('global CSS imports tailwindcss', () => {
    const css = readFileSync(resolve('src/styles/global.css'), 'utf-8')
    expect(css).toContain('tailwindcss')
  })
})
