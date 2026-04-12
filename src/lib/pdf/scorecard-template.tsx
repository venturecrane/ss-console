/**
 * Scorecard PDF report template using Forme JSX components.
 *
 * 2-page report:
 *   Page 1: Overall score, dimension breakdown bars, top opportunities
 *   Page 2: Per-dimension detail with descriptions and next steps
 *
 * @see docs/design/operations-health-scorecard.md — Section 7
 */

import React from 'react'
import { Document, Page, View, Text } from '@formepdf/react'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ScorecardDimensionResult {
  label: string
  scaled: number
  displayLabel: string
  color: string
  description: string
}

export interface ScorecardOpportunity {
  label: string
  description: string
}

export interface ScorecardReportProps {
  firstName: string
  businessName: string
  vertical: string
  overallScore: number
  overallDisplayLabel: string
  overallColor: string
  dimensions: ScorecardDimensionResult[]
  opportunities: ScorecardOpportunity[]
  completedAt: string
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const colors = {
  primary: '#1e40af',
  textPrimary: '#1e293b',
  textBody: '#334155',
  textMuted: '#64748b',
  border: '#e2e8f0',
  surfaceLight: '#f8fafc',
  white: '#ffffff',
  barBg: '#f1f5f9',
}

const fonts = {
  heading: 'Plus Jakarta Sans',
  body: 'Inter',
}

const pageMargins = { top: 54, bottom: 54, left: 72, right: 72 }

const bodyText = {
  fontFamily: fonts.body,
  fontWeight: 400 as const,
  fontSize: 10,
  color: colors.textBody,
  lineHeight: 1.5,
}

const mutedText = {
  fontFamily: fonts.body,
  fontWeight: 400 as const,
  fontSize: 8,
  color: colors.textMuted,
}

const sectionHeading = {
  fontFamily: fonts.heading,
  fontWeight: 700 as const,
  fontSize: 12,
  color: colors.primary,
  textTransform: 'uppercase' as const,
  marginBottom: 12,
  paddingLeft: 8,
  borderLeft: `3px solid ${colors.primary}`,
}

// ---------------------------------------------------------------------------
// Template
// ---------------------------------------------------------------------------

export function ScorecardReportTemplate(props: ScorecardReportProps) {
  const {
    firstName,
    businessName,
    overallScore,
    overallDisplayLabel,
    overallColor,
    dimensions,
    opportunities,
    completedAt,
  } = props

  // Sort dimensions by score ascending for display
  const sortedDimensions = [...dimensions].sort((a, b) => a.scaled - b.scaled)

  return (
    <Document>
      {/* ===== PAGE 1: Score Overview ===== */}
      <Page size="Letter" margin={pageMargins}>
        {/* Header */}
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 24 }}>
          <View>
            <Text
              style={{
                fontFamily: fonts.heading,
                fontWeight: 800,
                fontSize: 20,
                color: colors.primary,
              }}
            >
              SMD Services
            </Text>
            <Text style={{ ...mutedText, marginTop: 2 }}>smd.services</Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text
              style={{
                fontFamily: fonts.heading,
                fontWeight: 700,
                fontSize: 12,
                color: colors.primary,
              }}
            >
              OPERATIONS HEALTH REPORT
            </Text>
            <Text style={{ ...mutedText, marginTop: 2 }}>
              Prepared for {firstName} at {businessName}
            </Text>
            <Text style={{ ...mutedText, marginTop: 2 }}>{completedAt}</Text>
          </View>
        </View>

        {/* Divider */}
        <View style={{ height: 1, backgroundColor: colors.border, marginBottom: 24 }} />

        {/* Overall Score */}
        <View style={{ alignItems: 'center', marginBottom: 32 }}>
          <Text style={{ ...mutedText, fontSize: 9, textTransform: 'uppercase', letterSpacing: 1 }}>
            YOUR OPERATIONS HEALTH SCORE
          </Text>
          <Text
            style={{
              fontFamily: fonts.heading,
              fontWeight: 800,
              fontSize: 48,
              color: overallColor,
              marginTop: 8,
            }}
          >
            {overallScore}
          </Text>
          <View
            style={{
              backgroundColor: `${overallColor}20`,
              paddingLeft: 16,
              paddingRight: 16,
              paddingTop: 4,
              paddingBottom: 4,
              borderRadius: 12,
              marginTop: 4,
            }}
          >
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 11,
                color: overallColor,
              }}
            >
              {overallDisplayLabel}
            </Text>
          </View>
        </View>

        {/* Dimension Breakdown */}
        <Text style={sectionHeading}>HOW YOU SCORED ACROSS 6 AREAS</Text>
        <View style={{ marginBottom: 28 }}>
          {sortedDimensions.map((dim) => (
            <View
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                marginBottom: 10,
              }}
            >
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 500,
                  fontSize: 9,
                  color: colors.textBody,
                  width: 130,
                }}
              >
                {dim.label}
              </Text>
              <View
                style={{
                  flex: 1,
                  height: 10,
                  backgroundColor: colors.barBg,
                  borderRadius: 5,
                  overflow: 'hidden',
                }}
              >
                <View
                  style={{
                    width: `${Math.max(dim.scaled, 3)}%`,
                    height: 10,
                    backgroundColor: dim.color,
                    borderRadius: 5,
                  }}
                />
              </View>
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 600,
                  fontSize: 9,
                  color: colors.textBody,
                  width: 30,
                  textAlign: 'right',
                }}
              >
                {dim.scaled}
              </Text>
            </View>
          ))}
        </View>

        {/* Opportunities */}
        {opportunities.length > 0 && (
          <>
            <Text style={sectionHeading}>WHERE WE&apos;D START</Text>
            <Text style={{ ...bodyText, marginBottom: 12 }}>
              Based on your answers, the areas with the most room for improvement are:
            </Text>
            {opportunities.map((opp) => (
              <View
                style={{
                  backgroundColor: colors.surfaceLight,
                  border: `1px solid ${colors.border}`,
                  borderRadius: 4,
                  padding: 12,
                  marginBottom: 8,
                }}
              >
                <Text
                  style={{
                    fontFamily: fonts.body,
                    fontWeight: 600,
                    fontSize: 10,
                    color: colors.textPrimary,
                    marginBottom: 4,
                  }}
                >
                  {opp.label}
                </Text>
                <Text style={bodyText}>{opp.description}</Text>
              </View>
            ))}
          </>
        )}

        {/* Footer */}
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
            <Text style={mutedText}>SMD Services | smd.services</Text>
            <Text style={mutedText}>Operations Health Report | Page 1 of 2</Text>
          </View>
        </View>
      </Page>

      {/* ===== PAGE 2: Detail + CTA ===== */}
      <Page size="Letter" margin={pageMargins}>
        {/* Per-dimension detail */}
        <Text style={sectionHeading}>DIMENSION DETAIL</Text>
        {sortedDimensions.map((dim) => (
          <View style={{ marginBottom: 14 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 4 }}>
              <View
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 4,
                  backgroundColor: dim.color,
                  marginRight: 6,
                }}
              />
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 600,
                  fontSize: 10,
                  color: colors.textPrimary,
                }}
              >
                {dim.label}
              </Text>
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 500,
                  fontSize: 9,
                  color: colors.textMuted,
                  marginLeft: 8,
                }}
              >
                {dim.scaled}/100 ({dim.displayLabel})
              </Text>
            </View>
            <Text style={{ ...bodyText, marginLeft: 14 }}>{dim.description}</Text>
          </View>
        ))}

        {/* Divider */}
        <View
          style={{ height: 1, backgroundColor: colors.border, marginTop: 8, marginBottom: 24 }}
        />

        {/* CTA */}
        <Text style={sectionHeading}>WANT TO DIG DEEPER?</Text>
        <Text style={{ ...bodyText, marginBottom: 16 }}>
          This scorecard gives you the lay of the land. The real value comes from a conversation. We
          walk through your day together and figure out exactly what to focus on first.
        </Text>
        <View
          style={{
            backgroundColor: colors.primary,
            borderRadius: 6,
            paddingTop: 10,
            paddingBottom: 10,
            paddingLeft: 24,
            paddingRight: 24,
            alignSelf: 'flex-start',
          }}
        >
          <Text
            style={{
              fontFamily: fonts.body,
              fontWeight: 600,
              fontSize: 11,
              color: colors.white,
            }}
          >
            Book an assessment call at smd.services/book
          </Text>
        </View>

        {/* About SMD */}
        <View style={{ marginTop: 32 }}>
          <Text style={sectionHeading}>ABOUT SMD SERVICES</Text>
          <Text style={bodyText}>
            We help growing businesses figure out what needs to change and build the right solution
            together. Our team works alongside you to document processes, choose the right tools,
            and get everything running so your operations keep up with your ambition.
          </Text>
        </View>

        {/* Footer */}
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
            <Text style={mutedText}>SMD Services | smd.services</Text>
            <Text style={mutedText}>Operations Health Report | Page 2 of 2</Text>
          </View>
        </View>
      </Page>
    </Document>
  )
}
