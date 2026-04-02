import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('analytics: query layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/analytics.ts'), 'utf-8')

  it('analytics.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/analytics.ts'))).toBe(true)
  })

  it('exports getPipelineConversion function', () => {
    expect(source()).toContain('export async function getPipelineConversion')
  })

  it('exports getQuoteAccuracy function', () => {
    expect(source()).toContain('export async function getQuoteAccuracy')
  })

  it('exports getRevenueReport function', () => {
    expect(source()).toContain('export async function getRevenueReport')
  })

  it('exports getEngagementHealth function', () => {
    expect(source()).toContain('export async function getEngagementHealth')
  })

  it('exports getFollowUpCompliance function', () => {
    expect(source()).toContain('export async function getFollowUpCompliance')
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // Should not use template literals in SQL strings
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('scopes getPipelineConversion to org_id', () => {
    const code = source()
    expect(code).toContain('org_id = ?')
  })

  it('scopes getQuoteAccuracy to org_id', () => {
    const code = source()
    // Both the engagements and clients tables should be scoped
    const quoteAccuracySection = code.slice(
      code.indexOf('getQuoteAccuracy'),
      code.indexOf('getRevenueReport')
    )
    expect(quoteAccuracySection).toContain('org_id')
  })

  it('scopes getRevenueReport to org_id', () => {
    const code = source()
    const revenueSection = code.slice(
      code.indexOf('getRevenueReport'),
      code.indexOf('getEngagementHealth')
    )
    expect(revenueSection).toContain('org_id = ?')
  })

  it('scopes getEngagementHealth to org_id', () => {
    const code = source()
    const healthSection = code.slice(
      code.indexOf('getEngagementHealth'),
      code.indexOf('getFollowUpCompliance')
    )
    expect(healthSection).toContain('org_id = ?')
  })

  it('scopes getFollowUpCompliance to org_id', () => {
    const code = source()
    const complianceSection = code.slice(code.indexOf('getFollowUpCompliance'))
    expect(complianceSection).toContain('org_id = ?')
  })

  it('getPipelineConversion counts by entity stage', () => {
    const code = source()
    expect(code).toContain('GROUP BY stage')
    expect(code).toContain('FROM entities')
  })

  it('getQuoteAccuracy joins engagements with entities', () => {
    const code = source()
    expect(code).toContain('FROM engagements')
    expect(code).toContain('JOIN entities')
  })

  it('getQuoteAccuracy only includes completed engagements', () => {
    const code = source()
    const section = code.slice(code.indexOf('getQuoteAccuracy'), code.indexOf('getRevenueReport'))
    expect(section).toContain("status = 'completed'")
  })

  it('getQuoteAccuracy calculates accuracy percentage', () => {
    expect(source()).toContain('accuracy_pct')
  })

  it('getRevenueReport aggregates from invoices table', () => {
    expect(source()).toContain('FROM invoices')
  })

  it('getRevenueReport groups by month using substr of paid_at', () => {
    const code = source()
    expect(code).toContain('substr(paid_at')
    expect(code).toContain('GROUP BY')
  })

  it('getRevenueReport groups by entity vertical', () => {
    const code = source()
    expect(code).toContain('en.vertical')
    expect(code).toContain('JOIN entities')
  })

  it('getEngagementHealth calculates average days to completion', () => {
    const code = source()
    expect(code).toContain('julianday(actual_end)')
    expect(code).toContain('julianday(start_date)')
  })

  it('getEngagementHealth calculates on-time percentage', () => {
    const code = source()
    expect(code).toContain('actual_end <= estimated_end')
  })

  it('getEngagementHealth includes parking lot item average', () => {
    const code = source()
    expect(code).toContain('parking_lot')
    expect(code).toContain('avg_parking_lot_items')
  })

  it('getFollowUpCompliance categorizes on-time, late, and missed', () => {
    const code = source()
    expect(code).toContain('completed_on_time')
    expect(code).toContain('completed_late')
    expect(code).toContain('missed')
  })

  it('getFollowUpCompliance uses completed_at vs scheduled_for for timing', () => {
    const code = source()
    expect(code).toContain('completed_at <= scheduled_for')
    expect(code).toContain('completed_at > scheduled_for')
  })

  it('getFollowUpCompliance detects missed as scheduled and past due', () => {
    const code = source()
    expect(code).toContain("status = 'scheduled'")
    expect(code).toContain("scheduled_for < datetime('now')")
  })

  it('handles division by zero safely', () => {
    const code = source()
    // Multiple guards against division by zero
    expect(code).toContain('> 0')
  })

  it('exports result type interfaces', () => {
    const code = source()
    expect(code).toContain('export interface PipelineConversion')
    expect(code).toContain('export interface QuoteAccuracyRow')
    expect(code).toContain('export interface RevenueReport')
    expect(code).toContain('export interface EngagementHealth')
    expect(code).toContain('export interface FollowUpCompliance')
  })
})

describe('analytics: dashboard page', () => {
  const source = () => readFileSync(resolve('src/pages/admin/analytics/index.astro'), 'utf-8')

  it('dashboard page exists', () => {
    expect(existsSync(resolve('src/pages/admin/analytics/index.astro'))).toBe(true)
  })

  it('imports all 5 analytics functions', () => {
    const code = source()
    expect(code).toContain('getPipelineConversion')
    expect(code).toContain('getQuoteAccuracy')
    expect(code).toContain('getRevenueReport')
    expect(code).toContain('getEngagementHealth')
    expect(code).toContain('getFollowUpCompliance')
  })

  it('does not import any JavaScript charting library', () => {
    const code = source()
    expect(code).not.toContain('chart.js')
    expect(code).not.toContain('d3')
    expect(code).not.toContain('recharts')
    expect(code).not.toContain('plotly')
    expect(code).not.toContain('apexcharts')
    expect(code).not.toContain('echarts')
  })

  it('renders pipeline funnel section', () => {
    const code = source()
    expect(code).toContain('Pipeline Funnel')
  })

  it('renders quote accuracy table', () => {
    const code = source()
    expect(code).toContain('Quote Accuracy')
    expect(code).toContain('Estimated')
    expect(code).toContain('Actual')
    expect(code).toContain('Variance')
    expect(code).toContain('Accuracy')
  })

  it('renders revenue summary section', () => {
    const code = source()
    expect(code).toContain('Revenue Summary')
    expect(code).toContain('Total invoiced')
    expect(code).toContain('Total paid')
    expect(code).toContain('Outstanding')
  })

  it('renders engagement health section', () => {
    const code = source()
    expect(code).toContain('Engagement Health')
    expect(code).toContain('Avg days to completion')
    expect(code).toContain('Avg parking lot items')
  })

  it('renders follow-up compliance section', () => {
    const code = source()
    expect(code).toContain('Follow-up Compliance')
    expect(code).toContain('Compliance rate')
    expect(code).toContain('On time')
    expect(code).toContain('Late')
    expect(code).toContain('Missed')
  })

  it('has empty state for no clients', () => {
    expect(source()).toContain('No clients yet')
  })

  it('has empty state for no completed engagements', () => {
    expect(source()).toContain('No completed engagements yet')
  })

  it('has empty state for no invoices', () => {
    expect(source()).toContain('No invoices yet')
  })

  it('has empty state for no follow-ups', () => {
    expect(source()).toContain('No follow-ups yet')
  })

  it('has breadcrumb navigation', () => {
    const code = source()
    expect(code).toContain('/admin')
    expect(code).toContain('Dashboard')
    expect(code).toContain('Analytics')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })

  it('uses responsive grid layout', () => {
    const code = source()
    expect(code).toContain('grid-cols-1')
    expect(code).toContain('md:grid-cols-2')
  })

  it('color-codes quote accuracy within 20%, 20-40%, and >40%', () => {
    const code = source()
    expect(code).toContain('text-green-700')
    expect(code).toContain('text-amber-700')
    expect(code).toContain('text-red-700')
  })

  it('color-codes pipeline stages', () => {
    const code = source()
    expect(code).toContain('bg-slate-400') // prospect
    expect(code).toContain('bg-blue-500') // assessed
    expect(code).toContain('bg-amber-500') // quoted
    expect(code).toContain('bg-green-500') // active
    expect(code).toContain('bg-emerald-600') // completed
    expect(code).toContain('bg-red-500') // dead
  })
})

describe('analytics: admin dashboard integration', () => {
  const source = () => readFileSync(resolve('src/pages/admin/index.astro'), 'utf-8')

  it('admin dashboard imports getPipelineConversion', () => {
    expect(source()).toContain('getPipelineConversion')
  })

  it('admin dashboard shows Analytics card', () => {
    const code = source()
    expect(code).toContain('Analytics')
    expect(code).toContain('/admin/analytics')
  })

  it('admin dashboard shows total clients metric', () => {
    expect(source()).toContain('totalClients')
  })

  it('admin dashboard shows active engagements metric', () => {
    expect(source()).toContain('activeEngagements')
  })

  it('admin dashboard shows pipeline conversion rate', () => {
    expect(source()).toContain('pipelineConversionRate')
  })
})
