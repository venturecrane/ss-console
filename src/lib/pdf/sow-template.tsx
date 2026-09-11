/**
 * SOW (Statement of Work) PDF template using Forme JSX components.
 *
 * Implements the design spec from docs/templates/sow-template.md.
 *
 * Business rules enforced:
 * - No hourly rates or per-item pricing visible (Decision #16)
 * - 4 hard exclusions from Decision #10 (evolved)
 * - "We" voice throughout (Decision #20)
 * - 2-part or 3-milestone payment terms (Decision #14)
 * - Max 8 deliverable items (errors above 8)
 *
 * @see docs/templates/sow-template.md — full design specification
 */

import React from 'react'
import { Document, Page, View, Text } from '@formepdf/react'
import { BRAND_NAME } from '../config/brand'
import {
  SOWFooter,
  bodyTextStyle,
  colors,
  finePrintStyle,
  fonts,
  labelStyle,
  pageMargins,
  sectionHeadingStyle,
  type SOWTemplateProps,
} from './sow-template-base'
import { SOWPage2, SOWPage3 } from './sow-template-pages'

export type { SOWTemplateProps } from './sow-template-base'

// ---------------------------------------------------------------------------
// SignWell text-tag field placement
//
// The client signature and client date fields are placed by SignWell via
// text tags embedded in the PDF — not by hardcoded coordinates. SignWell
// scans the document for these literal strings, places fields over their
// bounding boxes, and auto-fills them at signing time. Tags are rendered
// in white on white so they are invisible on the printed/unsigned PDF.
//
// Benefits over coordinate-based placement:
//   - Template edits cannot drift from field position (they move together)
//   - Zero coordinate math / DPI conversion at the provider boundary
//   - Adding fields (initials, checkboxes) is a one-line template edit
//
// Tag format: {{<type>:<signer>}} — short form, signer 1 is the client.
// Ref: https://developers.signwell.com/reference/adding-text-tags
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SOWHeader({
  client,
  doc,
}: {
  client: SOWTemplateProps['client']
  doc: SOWTemplateProps['document']
}) {
  return (
    <>
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
            {BRAND_NAME}
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
      <View style={{ marginBottom: 16 }}>
        {(
          [
            ['Prepared for:', client.businessName],
            ['Attn:', client.contactName],
            ['Date:', doc.date],
            ['Valid through:', doc.expirationDate],
            ['SOW #:', doc.sowNumber],
          ] as [string, string][]
        ).map(([label, value]) => (
          <View style={{ flexDirection: 'row', marginBottom: 4 }}>
            <Text style={{ ...labelStyle, width: 100 }}>{label}</Text>
            <Text style={bodyTextStyle}>{value}</Text>
          </View>
        ))}
      </View>
      <View style={{ height: 1, backgroundColor: colors.border, marginBottom: 16 }} />
    </>
  )
}

const scopeColStyle = (w: number | 'flex', weight: number, color: string) => ({
  fontFamily: fonts.body,
  fontWeight: weight,
  fontSize: 9,
  color,
  ...(w === 'flex' ? { flex: 1 } : { width: w }),
})

function SOWScopeTableHeader({ rowPadding }: { rowPadding: number }) {
  return (
    <View
      style={{
        flexDirection: 'row',
        backgroundColor: colors.surfaceLight,
        borderBottom: `1px solid ${colors.border}`,
        padding: `${rowPadding}pt 8pt`,
      }}
    >
      <Text style={scopeColStyle(30, 600, colors.textPrimary)}>#</Text>
      <Text style={scopeColStyle(160, 600, colors.textPrimary)}>Deliverable</Text>
      <Text style={scopeColStyle('flex', 600, colors.textPrimary)}>Description</Text>
    </View>
  )
}

function SOWScopeTableRow({
  item,
  index,
  isLast,
  rowPadding,
}: {
  item: SOWTemplateProps['items'][number]
  index: number
  isLast: boolean
  rowPadding: number
}) {
  return (
    <View
      style={{
        flexDirection: 'row',
        backgroundColor: index % 2 === 0 ? colors.white : colors.surfaceLight,
        borderBottom: isLast ? undefined : `1px solid ${colors.border}`,
        padding: `${rowPadding}pt 8pt`,
      }}
    >
      <Text style={scopeColStyle(30, 400, colors.textBody)}>{index + 1}</Text>
      <Text style={scopeColStyle(160, 400, colors.textBody)}>{item.name}</Text>
      <Text style={scopeColStyle('flex', 400, colors.textBody)}>{item.description}</Text>
    </View>
  )
}

function SOWScopeTable({
  items,
  rowPadding,
}: {
  items: SOWTemplateProps['items']
  rowPadding: number
}) {
  return (
    <View style={{ border: `1px solid ${colors.border}`, marginBottom: 16 }}>
      <SOWScopeTableHeader rowPadding={rowPadding} />
      {items.map((item, index) => (
        <SOWScopeTableRow
          key={index}
          item={item}
          index={index}
          isLast={index === items.length - 1}
          rowPadding={rowPadding}
        />
      ))}
    </View>
  )
}

function PayRow({ label, amount, mb = 0 }: { label: string; amount: string; mb?: number }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: mb }}>
      <Text style={bodyTextStyle}>{label}</Text>
      <Text style={bodyTextStyle}>{amount}</Text>
    </View>
  )
}

function SOWPaymentBlock({ payment }: { payment: SOWTemplateProps['payment'] }) {
  const totStyle = {
    fontFamily: fonts.body,
    fontWeight: 700,
    fontSize: 14,
    color: colors.textPrimary,
  }
  return (
    <>
      <View
        style={{
          backgroundColor: colors.surfaceLight,
          border: `1px solid ${colors.border}`,
          borderRadius: 4,
          padding: 12,
          marginBottom: 8,
        }}
      >
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 }}>
          <Text style={totStyle}>Project total</Text>
          <Text style={totStyle}>{payment.totalPrice}</Text>
        </View>
        <View style={{ height: 1, backgroundColor: colors.border, marginBottom: 8 }} />
        {payment.schedule === 'two_part' ? (
          <>
            <PayRow label="Due at signing (50%)" amount={payment.deposit} mb={4} />
            <PayRow label="Due at completion (50%)" amount={payment.completion} />
          </>
        ) : (
          <>
            <PayRow label="Due at signing" amount={payment.deposit} mb={4} />
            <PayRow
              label={`Due at ${payment.milestoneLabel ?? 'mid-engagement milestone'}`}
              amount={payment.milestone ?? ''}
              mb={4}
            />
            <PayRow label="Due at completion" amount={payment.completion} />
          </>
        )}
      </View>
      <Text style={{ ...finePrintStyle, marginBottom: 16 }}>
        Payment is due regardless of scope additions surfaced during the engagement.
      </Text>
    </>
  )
}

interface Page1Props {
  client: SOWTemplateProps['client']
  doc: SOWTemplateProps['document']
  engagement: SOWTemplateProps['engagement']
  items: SOWTemplateProps['items']
  payment: SOWTemplateProps['payment']
  rowPadding: number
}

function SOWPage1({ client, doc, engagement, items, payment, rowPadding }: Page1Props) {
  return (
    <Page size="Letter" margin={pageMargins}>
      <SOWHeader client={client} doc={doc} />
      <Text style={sectionHeadingStyle}>ENGAGEMENT OVERVIEW</Text>
      <Text style={{ ...bodyTextStyle, marginBottom: 16 }}>{engagement.overview}</Text>
      <Text style={sectionHeadingStyle}>SCOPE OF WORK</Text>
      <SOWScopeTable items={items} rowPadding={rowPadding} />
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
      <Text style={sectionHeadingStyle}>PROJECT INVESTMENT</Text>
      <SOWPaymentBlock payment={payment} />
      <SOWFooter sowNumber={doc.sowNumber} pageLabel="Page 1 of 3" />
    </Page>
  )
}

// ---------------------------------------------------------------------------
// Template component
// ---------------------------------------------------------------------------

export function SOWTemplate(props: SOWTemplateProps) {
  const { client, document: doc, engagement, items, payment } = props

  if (items.length > 8) {
    throw new Error(
      `SOW template supports a maximum of 8 deliverable items. Received ${items.length}. ` +
        'Exceeding 8 deliverables likely signals scope that is too broad for one engagement.'
    )
  }

  const rowPadding = items.length > 6 ? 4 : 6

  return (
    <Document>
      <SOWPage1
        client={client}
        doc={doc}
        engagement={engagement}
        items={items}
        payment={payment}
        rowPadding={rowPadding}
      />
      <SOWPage2 sowNumber={doc.sowNumber} />
      <SOWPage3 client={client} sowNumber={doc.sowNumber} />
    </Document>
  )
}
