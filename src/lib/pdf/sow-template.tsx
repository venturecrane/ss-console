/**
 * SOW (Statement of Work) PDF template using Forme JSX components.
 *
 * Implements the design spec from docs/templates/sow-template.md.
 *
 * Business rules enforced:
 * - No hourly rates or per-item pricing visible (Decision #16)
 * - 5 hard exclusions from Decision #10
 * - "We" voice throughout (Decision #20)
 * - 2-part or 3-milestone payment terms (Decision #14)
 * - Max 8 deliverable items (errors above 8)
 *
 * @see docs/templates/sow-template.md — full design specification
 */

import { Document, Page, View, Text } from '@formepdf/react'

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
  smd: {
    signerName: string
    signerTitle: string
  }
}

// ---------------------------------------------------------------------------
// Exclusions list (Decision #10 — 5 hard exclusions)
// ---------------------------------------------------------------------------

export const EXCLUSIONS = [
  'Bookkeeping remediation or catch-up',
  'Data migration from legacy systems',
  'Custom software or application development',
  'Ongoing support beyond the handoff session',
  'Multi-location or franchise scope',
]

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
}

const fonts = {
  heading: 'Plus Jakarta Sans',
  body: 'Inter',
}

const sectionHeadingStyle = {
  fontFamily: fonts.heading,
  fontWeight: 700 as const,
  fontSize: 12,
  color: colors.primary,
  textTransform: 'uppercase' as const,
  marginBottom: 12,
  paddingLeft: 8,
  borderLeft: `3px solid ${colors.primary}`,
}

const bodyTextStyle = {
  fontFamily: fonts.body,
  fontWeight: 400 as const,
  fontSize: 10,
  color: colors.textBody,
  lineHeight: 1.4,
}

const labelStyle = {
  fontFamily: fonts.body,
  fontWeight: 500 as const,
  fontSize: 8,
  color: colors.textMuted,
}

const finePrintStyle = {
  fontFamily: fonts.body,
  fontWeight: 400 as const,
  fontSize: 8,
  color: colors.textMuted,
}

const pageMargins = {
  top: 54, // 0.75in
  bottom: 54,
  left: 72, // 1in
  right: 72,
}

// ---------------------------------------------------------------------------
// Template component
// ---------------------------------------------------------------------------

export function SOWTemplate(props: SOWTemplateProps) {
  const { client, document: doc, engagement, items, payment, smd } = props

  if (items.length > 8) {
    throw new Error(
      `SOW template supports a maximum of 8 deliverable items. Received ${items.length}. ` +
        'Exceeding 8 deliverables likely signals scope that is too broad for one engagement.'
    )
  }

  // Reduce row padding for 7-8 items to maintain page 1 fit
  const rowPadding = items.length > 6 ? 4 : 6

  return (
    <Document>
      {/* ===== PAGE 1 ===== */}
      <Page
        size="Letter"
        margin={{
          top: pageMargins.top,
          bottom: pageMargins.bottom,
          left: pageMargins.left,
          right: pageMargins.right,
        }}
      >
        {/* Header */}
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 16 }}>
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
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 400,
                fontSize: 9,
                color: colors.textMuted,
                marginTop: 2,
              }}
            >
              smd.services
            </Text>
          </View>
          <Text
            style={{
              fontFamily: fonts.heading,
              fontWeight: 700,
              fontSize: 14,
              color: colors.primary,
            }}
          >
            STATEMENT OF WORK
          </Text>
        </View>

        {/* Client details */}
        <View style={{ marginBottom: 16 }}>
          <View style={{ flexDirection: 'row', marginBottom: 4 }}>
            <Text style={{ ...labelStyle, width: 100 }}>Prepared for:</Text>
            <Text style={bodyTextStyle}>{client.businessName}</Text>
          </View>
          <View style={{ flexDirection: 'row', marginBottom: 4 }}>
            <Text style={{ ...labelStyle, width: 100 }}>Attn:</Text>
            <Text style={bodyTextStyle}>{client.contactName}</Text>
          </View>
          <View style={{ flexDirection: 'row', marginBottom: 4 }}>
            <Text style={{ ...labelStyle, width: 100 }}>Date:</Text>
            <Text style={bodyTextStyle}>{doc.date}</Text>
          </View>
          <View style={{ flexDirection: 'row', marginBottom: 4 }}>
            <Text style={{ ...labelStyle, width: 100 }}>Valid through:</Text>
            <Text style={bodyTextStyle}>{doc.expirationDate}</Text>
          </View>
          <View style={{ flexDirection: 'row', marginBottom: 4 }}>
            <Text style={{ ...labelStyle, width: 100 }}>SOW #:</Text>
            <Text style={bodyTextStyle}>{doc.sowNumber}</Text>
          </View>
        </View>

        {/* Divider */}
        <View style={{ height: 1, backgroundColor: colors.border, marginBottom: 16 }} />

        {/* Engagement Overview */}
        <Text style={sectionHeadingStyle}>ENGAGEMENT OVERVIEW</Text>
        <Text style={{ ...bodyTextStyle, marginBottom: 16 }}>{engagement.overview}</Text>

        {/* Scope of Work */}
        <Text style={sectionHeadingStyle}>SCOPE OF WORK</Text>
        <View style={{ border: `1px solid ${colors.border}`, marginBottom: 16 }}>
          {/* Table header */}
          <View
            style={{
              flexDirection: 'row',
              backgroundColor: colors.surfaceLight,
              borderBottom: `1px solid ${colors.border}`,
              padding: `${rowPadding}pt 8pt`,
            }}
          >
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 9,
                color: colors.textPrimary,
                width: 30,
              }}
            >
              #
            </Text>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 9,
                color: colors.textPrimary,
                width: 160,
              }}
            >
              Deliverable
            </Text>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 9,
                color: colors.textPrimary,
                flex: 1,
              }}
            >
              Description
            </Text>
          </View>
          {/* Table rows */}
          {items.map((item, index) => (
            <View
              style={{
                flexDirection: 'row',
                backgroundColor: index % 2 === 0 ? colors.white : colors.surfaceLight,
                borderBottom: index < items.length - 1 ? `1px solid ${colors.border}` : undefined,
                padding: `${rowPadding}pt 8pt`,
              }}
            >
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 400,
                  fontSize: 9,
                  color: colors.textBody,
                  width: 30,
                }}
              >
                {index + 1}
              </Text>
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 400,
                  fontSize: 9,
                  color: colors.textBody,
                  width: 160,
                }}
              >
                {item.name}
              </Text>
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 400,
                  fontSize: 9,
                  color: colors.textBody,
                  flex: 1,
                }}
              >
                {item.description}
              </Text>
            </View>
          ))}
        </View>

        {/* Timeline */}
        <Text style={sectionHeadingStyle}>TIMELINE</Text>
        <View style={{ flexDirection: 'row', gap: 40, marginBottom: 16 }}>
          <View>
            <Text style={labelStyle}>Estimated start</Text>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 10,
                color: colors.textBody,
                marginTop: 2,
              }}
            >
              {engagement.startDate}
            </Text>
          </View>
          <View>
            <Text style={labelStyle}>Estimated completion</Text>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 10,
                color: colors.textBody,
                marginTop: 2,
              }}
            >
              {engagement.endDate}
            </Text>
          </View>
        </View>

        {/* Project Investment */}
        <Text style={sectionHeadingStyle}>PROJECT INVESTMENT</Text>
        <View
          style={{
            backgroundColor: colors.surfaceLight,
            border: `1px solid ${colors.border}`,
            borderRadius: 4,
            padding: 12,
            marginBottom: 8,
          }}
        >
          {/* Total */}
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 }}>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 700,
                fontSize: 14,
                color: colors.textPrimary,
              }}
            >
              Project total
            </Text>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 700,
                fontSize: 14,
                color: colors.textPrimary,
              }}
            >
              {payment.totalPrice}
            </Text>
          </View>
          {/* Divider */}
          <View style={{ height: 1, backgroundColor: colors.border, marginBottom: 8 }} />
          {/* Payment schedule */}
          {payment.schedule === 'two_part' ? (
            <>
              <View
                style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}
              >
                <Text style={bodyTextStyle}>Due at signing (50%)</Text>
                <Text style={bodyTextStyle}>{payment.deposit}</Text>
              </View>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Text style={bodyTextStyle}>Due at completion (50%)</Text>
                <Text style={bodyTextStyle}>{payment.completion}</Text>
              </View>
            </>
          ) : (
            <>
              <View
                style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}
              >
                <Text style={bodyTextStyle}>Due at signing</Text>
                <Text style={bodyTextStyle}>{payment.deposit}</Text>
              </View>
              <View
                style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}
              >
                <Text style={bodyTextStyle}>
                  Due at {payment.milestoneLabel ?? 'mid-engagement milestone'}
                </Text>
                <Text style={bodyTextStyle}>{payment.milestone}</Text>
              </View>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Text style={bodyTextStyle}>Due at completion</Text>
                <Text style={bodyTextStyle}>{payment.completion}</Text>
              </View>
            </>
          )}
        </View>
        <Text style={{ ...finePrintStyle, marginBottom: 16 }}>
          Payment is due regardless of scope additions surfaced during the engagement.
        </Text>

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
            <Text style={finePrintStyle}>SMD Services | smd.services</Text>
            <Text style={finePrintStyle}>{doc.sowNumber} | Page 1 of 2</Text>
          </View>
        </View>
      </Page>

      {/* ===== PAGE 2 ===== */}
      <Page
        size="Letter"
        margin={{
          top: pageMargins.top,
          bottom: pageMargins.bottom,
          left: pageMargins.left,
          right: pageMargins.right,
        }}
      >
        {/* What's Included */}
        <Text style={sectionHeadingStyle}>WHAT&apos;S INCLUDED</Text>
        <Text style={{ ...bodyTextStyle, marginBottom: 16 }}>
          This engagement includes problem diagnosis, process documentation, tool configuration, one
          handoff training session with your team, and a written handoff document. Scope is limited
          to the deliverables listed on page 1.
        </Text>

        {/* Exclusions */}
        <Text style={sectionHeadingStyle}>EXCLUSIONS</Text>
        <Text style={{ ...bodyTextStyle, marginBottom: 8 }}>
          The following are outside the scope of this engagement:
        </Text>
        {EXCLUSIONS.map((exclusion, index) => (
          <Text
            style={{
              fontFamily: fonts.body,
              fontWeight: 400,
              fontSize: 9,
              color: colors.textBody,
              marginLeft: 16,
              marginBottom: 4,
            }}
          >
            {index + 1}. {exclusion}
          </Text>
        ))}
        <Text
          style={{
            fontFamily: fonts.body,
            fontWeight: 400,
            fontSize: 9,
            color: colors.textMuted,
            marginTop: 12,
            marginBottom: 16,
          }}
        >
          Work discovered during the engagement that falls outside the agreed scope will be logged
          and reviewed together before the final handoff. If additional work is warranted,
          we&apos;ll propose a separate scope and estimate.
        </Text>

        {/* Terms */}
        <Text style={sectionHeadingStyle}>TERMS</Text>
        <View style={{ marginBottom: 16 }}>
          <Text
            style={{
              fontFamily: fonts.body,
              fontWeight: 400,
              fontSize: 9,
              color: colors.textBody,
              marginBottom: 8,
            }}
          >
            1. This SOW is valid for 5 business days from the date above. After expiration, scope
            and pricing may be revised.
          </Text>
          <Text
            style={{
              fontFamily: fonts.body,
              fontWeight: 400,
              fontSize: 9,
              color: colors.textBody,
              marginBottom: 8,
            }}
          >
            2. The engagement start date is tentative until the deposit is received. We will confirm
            the start date within 1 business day of receiving the deposit.
          </Text>
          <Text
            style={{
              fontFamily: fonts.body,
              fontWeight: 400,
              fontSize: 9,
              color: colors.textBody,
              marginBottom: 8,
            }}
          >
            3. A 2-week stabilization period follows the final handoff. During this period, we will
            address questions and minor adjustments related to the work delivered. New scope
            requires a separate engagement.
          </Text>
          <Text
            style={{ fontFamily: fonts.body, fontWeight: 400, fontSize: 9, color: colors.textBody }}
          >
            4. Either party may terminate this agreement with 3 business days&apos; written notice.
            Work completed to date will be delivered and invoiced proportionally.
          </Text>
        </View>

        {/* Signature Block */}
        <Text style={sectionHeadingStyle}>AGREEMENT</Text>
        <Text style={{ ...bodyTextStyle, marginBottom: 16 }}>
          By signing below, both parties agree to the scope, timeline, and terms described in this
          document.
        </Text>
        <View style={{ flexDirection: 'row', gap: 36 }}>
          {/* Client side */}
          <View style={{ flex: 1 }}>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 9,
                color: colors.textPrimary,
                marginBottom: 60,
              }}
            >
              CLIENT
            </Text>
            <View style={{ height: 1, backgroundColor: colors.textBody, marginBottom: 4 }} />
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 400,
                fontSize: 9,
                color: colors.textBody,
              }}
            >
              {client.contactName}
            </Text>
            {client.contactTitle && (
              <Text
                style={{
                  fontFamily: fonts.body,
                  fontWeight: 400,
                  fontSize: 9,
                  color: colors.textBody,
                }}
              >
                {client.contactTitle}
              </Text>
            )}
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 400,
                fontSize: 8,
                color: colors.textMuted,
                marginTop: 4,
              }}
            >
              Date: _______________
            </Text>
          </View>
          {/* SMD Services side */}
          <View style={{ flex: 1 }}>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 600,
                fontSize: 9,
                color: colors.textPrimary,
                marginBottom: 60,
              }}
            >
              SMD SERVICES
            </Text>
            <View style={{ height: 1, backgroundColor: colors.textBody, marginBottom: 4 }} />
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 400,
                fontSize: 9,
                color: colors.textBody,
              }}
            >
              {smd.signerName}
            </Text>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 400,
                fontSize: 9,
                color: colors.textBody,
              }}
            >
              {smd.signerTitle}
            </Text>
            <Text
              style={{
                fontFamily: fonts.body,
                fontWeight: 400,
                fontSize: 8,
                color: colors.textMuted,
                marginTop: 4,
              }}
            >
              Date: _______________
            </Text>
          </View>
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
            <Text style={finePrintStyle}>SMD Services | smd.services</Text>
            <Text style={finePrintStyle}>{doc.sowNumber} | Page 2 of 2</Text>
          </View>
        </View>
      </Page>
    </Document>
  )
}
