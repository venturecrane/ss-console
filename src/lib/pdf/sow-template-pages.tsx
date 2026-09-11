/**
 * Pages 2 and 3 of the SOW PDF: what is included, what is excluded, the
 * terms, and the signature block. Page 1 (parties, scope table, payment) lives
 * with the template in sow-template.tsx.
 *
 * Split out on 2026-09-11 (review 2026-09-10, Architecture 3). Business rules
 * enforced here are the ones the page copy states: the four hard exclusions
 * (Decision #10, evolved), "we" voice (Decision #20).
 */

import React from 'react'
import { Page, View, Text } from '@formepdf/react'
import { BRAND_NAME } from '../config/brand'
import {
  CLIENT_SIGNER_INDEX,
  EXCLUSIONS,
  SIGNATURE_BLOCK_WIDTH,
  SOWFooter,
  bodyTextStyle,
  colors,
  finePrintStyle,
  fonts,
  pageMargins,
  sectionHeadingStyle,
  type SOWTemplateProps,
} from './sow-template-base'

export function SOWPage2({ sowNumber }: { sowNumber: string }) {
  return (
    <Page size="Letter" margin={pageMargins}>
      <Text style={sectionHeadingStyle}>WHAT&apos;S INCLUDED</Text>
      <Text style={{ ...bodyTextStyle, marginBottom: 16 }}>
        This engagement includes problem diagnosis, process documentation, tool configuration, one
        handoff training session with your team, and a written handoff document. Scope is limited to
        the deliverables listed on page 1.
      </Text>
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
        Work discovered during the engagement that falls outside the agreed scope will be logged and
        reviewed together before the final handoff. If additional work is warranted, we&apos;ll
        propose a separate scope and estimate.
      </Text>
      {/*
        Terms are authored template language describing standard engagement
        mechanics (quote validity, start-date confirmation workflow, the
        existence of a stabilization period, termination notice). The SOW
        is a signed contractual document, so this is NOT Pattern A/B
        fabrication under CLAUDE.md's "no fabricated client-facing content"
        policy — parallel to CLAUDE.md Rule 3's explicit exemption for
        signed contracts. See docs/templates/sow-template.md for the full
        rationale and #398 for the audit that confirmed this read.

        What still matters: no fixed durations (the 2-week stabilization
        phrasing and the "within 1 business day" SLAs were removed). Per-
        engagement specifics (scope, pricing, milestones) remain authored.
      */}
      <Text style={sectionHeadingStyle}>TERMS</Text>
      <View style={{ marginBottom: 16 }}>
        {[
          '1. This SOW is valid for 5 business days from the date above. After expiration, scope and pricing may be revised.',
          '2. The engagement start date is tentative until the deposit is received. We will confirm the start date after the deposit clears.',
          '3. A stabilization period follows the final handoff. During this period, we will address questions and minor adjustments related to the work delivered. New scope requires a separate engagement.',
          '4. Either party may terminate this agreement with 3 business days’ written notice. Work completed to date will be delivered and invoiced proportionally.',
        ].map((term, i) => (
          <Text
            style={{
              fontFamily: fonts.body,
              fontWeight: 400,
              fontSize: 9,
              color: colors.textBody,
              marginBottom: i < 3 ? 8 : 0,
            }}
          >
            {term}
          </Text>
        ))}
      </View>
      <SOWFooter sowNumber={sowNumber} pageLabel="Page 2 of 3" />
    </Page>
  )
}

export function SOWPage3({
  client,
  sowNumber,
}: {
  client: SOWTemplateProps['client']
  sowNumber: string
}) {
  return (
    <Page size="Letter" margin={pageMargins}>
      <Text style={sectionHeadingStyle}>NEXT STEPS</Text>
      <Text style={{ ...bodyTextStyle, marginBottom: 24 }}>
        Once you sign below, we will send a deposit invoice. Work begins after the deposit is
        received. We will confirm the kickoff date after the deposit clears.
      </Text>
      <Text style={sectionHeadingStyle}>AGREEMENT</Text>
      <Text style={{ ...bodyTextStyle, marginBottom: 16 }}>
        By signing below, the client agrees to the scope, timeline, pricing, and terms described in
        this document. {BRAND_NAME} agrees by presenting this Statement of Work for signature.
      </Text>
      <View style={{ width: SIGNATURE_BLOCK_WIDTH }}>
        <Text
          style={{
            fontFamily: fonts.body,
            fontWeight: 600,
            fontSize: 9,
            color: colors.textPrimary,
            marginBottom: 8,
          }}
        >
          CLIENT ACCEPTANCE
        </Text>
        {/* Client signature — SignWell text tag rendered invisibly (white on white).
            SignWell places the signature field over this tag's bounding box at signing
            time, so template edits and field placement move together by construction. */}
        <Text
          style={{ fontFamily: fonts.body, fontSize: 36, color: colors.white, letterSpacing: 1 }}
        >{`{{s:${CLIENT_SIGNER_INDEX}}}`}</Text>
        <View style={{ height: 1, backgroundColor: colors.textBody, marginBottom: 4 }} />
        <Text
          style={{ fontFamily: fonts.body, fontWeight: 400, fontSize: 9, color: colors.textBody }}
        >
          {client.contactName}
        </Text>
        {client.contactTitle && (
          <Text
            style={{ fontFamily: fonts.body, fontWeight: 400, fontSize: 9, color: colors.textBody }}
          >
            {client.contactTitle}
          </Text>
        )}
        {/* Date row — "Date:" label visible, SignWell date tag rendered invisibly.
            SignWell auto-fills the signing date into the tag's bounding box. */}
        <View style={{ flexDirection: 'row', alignItems: 'baseline', marginTop: 4 }}>
          <Text
            style={{
              fontFamily: fonts.body,
              fontWeight: 400,
              fontSize: 8,
              color: colors.textMuted,
              marginRight: 4,
            }}
          >
            Date:
          </Text>
          <Text
            style={{ fontFamily: fonts.body, fontSize: 11, color: colors.white, letterSpacing: 1 }}
          >{`{{d:${CLIENT_SIGNER_INDEX}}}`}</Text>
        </View>
        <Text style={{ ...finePrintStyle, marginTop: 16, lineHeight: 1.4 }}>
          {BRAND_NAME} assents to this agreement by presenting this Statement of Work for signature.
        </Text>
      </View>
      <SOWFooter sowNumber={sowNumber} pageLabel="Page 3 of 3" />
    </Page>
  )
}
