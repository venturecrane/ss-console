/**
 * Shared vocabulary of the SOW PDF: the props contract, the four hard
 * exclusions, the type scale and page frame, and the footer every page carries.
 *
 * Split out of sow-template.tsx on 2026-09-11 (review 2026-09-10, Architecture
 * 3: six files parked within six lines of the 500-line ceiling). The template
 * and its page modules both import from here; nothing here imports them, so
 * the dependency runs one way.
 *
 * @see docs/templates/sow-template.md
 */

import React from 'react'
import { View, Text } from '@formepdf/react'
import { BRAND_NAME } from '../config/brand'

export const SIGNATURE_BLOCK_WIDTH = 216
export const CLIENT_SIGNER_INDEX = 1

// ---------------------------------------------------------------------------
// Props interface (matches Section 9.2 of sow-template.md)
// ---------------------------------------------------------------------------

export interface SOWTemplateProps {
  client: {
    businessName: string
    contactName: string
    contactTitle?: string
  }
  document: {
    date: string // pre-formatted: "March 30, 2026"
    expirationDate: string
    sowNumber: string // "SOW-202603-001"
  }
  engagement: {
    overview: string
    startDate: string // pre-formatted
    endDate: string // pre-formatted
  }
  items: Array<{
    name: string
    description: string
  }>
  payment: {
    schedule: 'two_part' | 'three_milestone'
    totalPrice: string // pre-formatted: "$3,500"
    deposit: string
    completion: string
    milestone?: string // only for three_milestone
    milestoneLabel?: string
  }
}

// ---------------------------------------------------------------------------
// Exclusions list (Decision #10 — evolved)
// ---------------------------------------------------------------------------

export const EXCLUSIONS = [
  'Bookkeeping remediation or catch-up',
  'Data migration from legacy systems',
  'Ground-up product development (consumer apps, SaaS products)',
  'Ongoing support beyond the handoff session',
]

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

// prettier-ignore
export const colors = {
  primary: '#1e40af', textPrimary: '#1e293b', textBody: '#334155',
  textMuted: '#64748b', border: '#e2e8f0', surfaceLight: '#f8fafc', white: '#ffffff',
}
export const fonts = { heading: 'Plus Jakarta Sans', body: 'Inter' }
// prettier-ignore
export const sectionHeadingStyle = {
  fontFamily: fonts.heading, fontWeight: 700 as const, fontSize: 12, color: colors.primary,
  textTransform: 'uppercase' as const, marginBottom: 12, paddingLeft: 8, borderLeft: `3px solid ${colors.primary}`,
}
// prettier-ignore
export const bodyTextStyle = { fontFamily: fonts.body, fontWeight: 400 as const, fontSize: 10, color: colors.textBody, lineHeight: 1.4 }
// prettier-ignore
export const labelStyle = { fontFamily: fonts.body, fontWeight: 500 as const, fontSize: 8, color: colors.textMuted }
// prettier-ignore
export const finePrintStyle = { fontFamily: fonts.body, fontWeight: 400 as const, fontSize: 8, color: colors.textMuted }
// prettier-ignore
export const pageMargins = { top: 54, bottom: 54, left: 72, right: 72 }

export function SOWFooter({ sowNumber, pageLabel }: { sowNumber: string; pageLabel: string }) {
  return (
    <View
      style={{
        position: 'absolute',
        bottom: pageMargins.bottom,
        left: pageMargins.left,
        right: pageMargins.right,
      }}
    >
      <View style={{ height: 1, backgroundColor: colors.border, marginBottom: 8 }} />
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <Text style={finePrintStyle}>{BRAND_NAME} | smd.services</Text>
        <Text style={finePrintStyle}>
          {sowNumber} | {pageLabel}
        </Text>
      </View>
    </View>
  )
}
