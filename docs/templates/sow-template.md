# SOW Template Design Spec

> Design specification for the SMD Services Statement of Work PDF template.
> Intended for Forme (WASM) JSX template implementation in Phase 2.
>
> Source decisions: #10 (scope boundaries), #14 (payment terms), #18 (SOW format), #20 (voice standard)

---

## 1. Document Parameters

| Property        | Value                                              |
| --------------- | -------------------------------------------------- |
| Page size       | US Letter (8.5" x 11" / 612 x 792 pt)              |
| Page count      | 3 pages (dedicated signing page)                   |
| Orientation     | Portrait                                           |
| Margins         | Top: 0.75in, Bottom: 0.75in, Left: 1in, Right: 1in |
| Printable width | 6.5in (468pt)                                      |
| Color mode      | CMYK-safe (no transparency effects)                |

---

## 2. Typography

| Role             | Font              | Weight | Size | Color     |
| ---------------- | ----------------- | ------ | ---- | --------- |
| Document title   | Plus Jakarta Sans | 700    | 20pt | `#1e293b` |
| Section headings | Plus Jakarta Sans | 700    | 12pt | `#1e40af` |
| Body text        | Inter             | 400    | 10pt | `#334155` |
| Body emphasis    | Inter             | 600    | 10pt | `#334155` |
| Table headers    | Inter             | 600    | 9pt  | `#1e293b` |
| Table cells      | Inter             | 400    | 9pt  | `#334155` |
| Fine print       | Inter             | 400    | 8pt  | `#64748b` |
| Field labels     | Inter             | 500    | 8pt  | `#64748b` |

Font loading: Plus Jakarta Sans (700, 800) and Inter (400, 500, 600) are already loaded on smd.services. Forme templates should embed these fonts or reference the same Google Fonts source.

---

## 3. Color Palette

| Token         | Hex       | Usage                                        |
| ------------- | --------- | -------------------------------------------- |
| Primary       | `#1e40af` | Section headings, accent line, logo mark     |
| Primary dark  | `#1e3a8a` | Hover states (not relevant in PDF, reserve)  |
| Text primary  | `#1e293b` | Document title, table headers, emphasis text |
| Text body     | `#334155` | Body copy, table cells                       |
| Text muted    | `#64748b` | Labels, fine print, dates                    |
| Border        | `#e2e8f0` | Table borders, dividers, section separators  |
| Surface light | `#f8fafc` | Alternating table rows, callout backgrounds  |
| White         | `#ffffff` | Page background                              |

---

## 4. Page 1 Layout

Page 1 contains all engagement details: header, scope, deliverables, timeline, and pricing.

### 4.1 Header Block

```
┌──────────────────────────────────────────────────────────────┐
│  [LOGO]                                  STATEMENT OF WORK   │
│  SMD Services                                                │
│  smd.services                                                │
│                                                              │
│  Prepared for: {{client.business_name}}                      │
│  Attn: {{client.contact_name}}                               │
│  Date: {{document.date}}                                     │
│  Valid through: {{document.expiration_date}}                  │
│  SOW #: {{document.sow_number}}                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Logo: top-left, max 120pt wide x 40pt tall
- "STATEMENT OF WORK" label: top-right, Plus Jakarta Sans 700, 14pt, `#1e40af`
- Company info (SMD Services, smd.services): below logo, Inter 400 9pt, `#64748b`
- Client block: left-aligned below company info, 16pt spacing above
- Each field label ("Prepared for:", "Date:", etc.): Inter 500 8pt `#64748b`
- Each field value: Inter 400 10pt `#334155`
- SOW number format: `SOW-YYYYMM-NNN` (e.g., `SOW-202604-001`)
- 1pt `#e2e8f0` horizontal rule below header, full printable width

**Placeholder fields:**
| Field | Source |
| ------------------------------ | -------------------------- |
| `{{client.business_name}}` | Client record |
| `{{client.contact_name}}` | Client contact record |
| `{{document.date}}` | Generation date |
| `{{document.expiration_date}}` | Generation date + 5 days |
| `{{document.sow_number}}` | Auto-generated sequence |

### 4.2 Engagement Overview

A brief paragraph summarizing what we discussed and what this engagement will address. This is the human-readable bridge between the assessment conversation and the formal scope below.

```
┌──────────────────────────────────────────────────────────────┐
│  ENGAGEMENT OVERVIEW                                         │
│                                                              │
│  {{engagement.overview}}                                     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: Plus Jakarta Sans 700 12pt `#1e40af`, uppercase, 4pt `#1e40af` left border accent (3pt wide, 20pt tall, vertically centered on heading text)
- 12pt space below heading
- Body: Inter 400 10pt `#334155`, 1.4 line height
- Max 3-4 sentences. Generated from assessment extraction, reviewed by admin before sending.

**Placeholder fields:**
| Field | Source |
| --------------------------- | -------------------------------------- |
| `{{engagement.overview}}` | Admin-written from assessment capture |

**Voice requirement (Decision #20):** Use "we" throughout. Example: "Based on our conversation on [date], we identified three areas where your operation can improve: [problem 1], [problem 2], and [problem 3]. This engagement scopes the work to address those areas together."

### 4.3 Scope of Work

The deliverables table. Each row is a line item from the quote builder.

```
┌──────────────────────────────────────────────────────────────┐
│  SCOPE OF WORK                                               │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  #   Deliverable              Description            │    │
│  ├──────────────────────────────────────────────────────┤    │
│  │  1   {{items[0].name}}        {{items[0].desc}}      │    │
│  │  2   {{items[1].name}}        {{items[1].desc}}      │    │
│  │  3   {{items[2].name}}        {{items[2].desc}}      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: same style as 4.2
- Table: full printable width
- Column widths: # (30pt), Deliverable (160pt), Description (remaining)
- Header row: `#f8fafc` background, Inter 600 9pt `#1e293b`, bottom border 1pt `#e2e8f0`
- Body rows: alternating white / `#f8fafc` background, Inter 400 9pt `#334155`
- Row padding: 6pt vertical, 8pt horizontal
- No per-item pricing columns. No hours column. (Decision #18: project price only.)
- Typical row count: 3-6 items. Template must handle up to 8 gracefully.

**Placeholder fields:**
| Field | Source |
| ------------------------- | ------------------- |
| `{{items[n].name}}` | Quote line item |
| `{{items[n].description}}`| Quote line item |

### 4.4 Timeline

A simple two-column layout showing engagement phases and their sequence.

```
┌──────────────────────────────────────────────────────────────┐
│  TIMELINE                                                    │
│                                                              │
│  Estimated start:  {{engagement.start_date}}                 │
│  Estimated completion:  {{engagement.end_date}}              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: same style as 4.2
- Two inline fields, Inter 400 10pt
- Label: Inter 500 8pt `#64748b`
- Value: Inter 600 10pt `#334155`
- Start date is the tentative sprint slot hold (Decision #18: 3-day confirmation deadline)
- No per-phase durations. Timeline is scoped per engagement — the SOW shows start and estimated completion only.

**Placeholder fields:**
| Field | Source |
| ----------------------------- | ------------------------------ |
| `{{engagement.start_date}}` | Sprint slot date |
| `{{engagement.end_date}}` | Calculated from scope estimate |

### 4.5 Project Investment

The total project price. No line-item pricing, no hourly breakdown.

```
┌──────────────────────────────────────────────────────────────┐
│  PROJECT INVESTMENT                                          │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │                                                      │    │
│  │   Project total              {{quote.total_price}}   │    │
│  │                                                      │    │
│  │   Due at signing (50%)       {{payment.deposit}}     │    │
│  │   Due on Day 8 (50%)         {{payment.completion}}  │    │
│  │                                                      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: same style as 4.2
- Container: `#f8fafc` background, 1pt `#e2e8f0` border, 12pt padding, 4pt border-radius
- Project total: Inter 700 14pt `#1e293b`, right-aligned value
- Payment split rows: Inter 400 10pt `#334155`, right-aligned values
- 1pt `#e2e8f0` divider between project total and payment split rows
- For 40+ hour engagements with 3-milestone split: show three rows instead of two (deposit / milestone / completion). Template must handle both cases.

**Placeholder fields:**
| Field | Source |
| ------------------------- | ----------------------------------------- |
| `{{quote.total_price}}` | Quote calculated total |
| `{{payment.deposit}}` | 50% of total (or configured percentage) |
| `{{payment.completion}}` | 50% of total (or remaining balance) |
| `{{payment.milestone}}` | Only present for 3-milestone engagements |

**Payment terms note (below container):**
Inter 400 8pt `#64748b`. Text: "Payment is due regardless of scope additions surfaced during the engagement." (Decision #14)

---

## 5. Page 2 Layout

Page 2 contains terms, exclusions, and signature fields. This page has a lighter content density — white space is intentional to keep the document approachable, not legalistic.

### 5.1 What's Included

A brief affirmative scope statement before the exclusions. Keeps the tone collaborative rather than defensive.

```
┌──────────────────────────────────────────────────────────────┐
│  WHAT'S INCLUDED                                             │
│                                                              │
│  This engagement includes problem diagnosis, process         │
│  documentation, tool configuration, one handoff training     │
│  session with your team, and a written handoff document.     │
│  Scope is limited to the deliverables listed on page 1.     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: same style as page 1 sections
- Body: Inter 400 10pt `#334155`, 1.4 line height
- This text is static (not templated). It directly reflects Decision #10's positive scope definition.

### 5.2 Exclusions

The five hard exclusions from Decision #10.

```
┌──────────────────────────────────────────────────────────────┐
│  EXCLUSIONS                                                  │
│                                                              │
│  The following are outside the scope of this engagement:     │
│                                                              │
│  1. Bookkeeping remediation or catch-up                      │
│  2. Data migration from legacy systems                       │
│  3. Custom software or application development               │
│  4. Ongoing support beyond the handoff session               │
│  5. Multi-location or franchise scope                        │
│                                                              │
│  Work discovered during the engagement that falls outside    │
│  the agreed scope will be logged and reviewed together       │
│  before the final handoff. If additional work is warranted,  │
│  we'll propose a separate scope and estimate.                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: same style
- Intro line: Inter 400 10pt `#334155`
- Numbered list: Inter 400 9pt `#334155`, 4pt between items, 16pt left indent
- Parking lot paragraph: Inter 400 9pt `#64748b`, 12pt above. References Decision #11 (scope creep protocol) without using the internal term "parking lot."
- All text is static.

### 5.3 Terms

Standard engagement terms. Kept short and readable — this is not a legal contract, it's a working agreement.

```
┌──────────────────────────────────────────────────────────────┐
│  TERMS                                                       │
│                                                              │
│  1. This SOW is valid for 5 business days from the date      │
│     above. After expiration, scope and pricing may be        │
│     revised.                                                 │
│                                                              │
│  2. The engagement start date is tentative until the         │
│     deposit is received. We will confirm the start date      │
│     within 1 business day of receiving the deposit.          │
│                                                              │
│  3. A 2-week stabilization period follows the final          │
│     handoff. During this period, we will address questions   │
│     and minor adjustments related to the work delivered.     │
│     New scope requires a separate engagement.                │
│                                                              │
│  4. Either party may terminate this agreement with 3         │
│     business days' written notice. Work completed to date    │
│     will be delivered and invoiced proportionally.           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: same style
- Numbered list: Inter 400 9pt `#334155`, 8pt between items
- Terms are static text. Term 1 reflects Decision #18 (5-day confirmation deadline). Term 3 reflects Decision #27 (2-week safety net, Day 24 cutoff). The word "stabilization" is used instead of "safety net" or "support window" for client-facing language.

### 5.4 Signature Block — Dedicated Signing Page (Page 3)

The AGREEMENT section lives on its own dedicated page (page 3). This eliminates content-dependent coordinate drift — the signing page has zero variable content, making SignWell field placement deterministic.

Page 3 includes a brief "Next Steps" section above the signature block so the page reads as intentional document design, not empty whitespace.

```
┌──────────────────────────────────────────────────────────────┐
│  NEXT STEPS                                                  │
│                                                              │
│  Once you sign below, we will send a deposit invoice.        │
│  Work begins after the deposit is received. We will          │
│  confirm the kickoff date within one business day.           │
│                                                              │
│  AGREEMENT                                                   │
│                                                              │
│  By signing below, the client agrees to the scope,           │
│  timeline, pricing, and terms described in this              │
│  document. SMD Services agrees by presenting this            │
│  Statement of Work for signature.                            │
│                                                              │
│  ┌──────────────────────┐                                    │
│  │  CLIENT ACCEPTANCE    │                                    │
│  │                       │                                    │
│  │  {{s:1}}              │  ← SignWell signature tag (hidden) │
│  │  ___________________  │                                    │
│  │  {{client.contact}}   │                                    │
│  │  {{client.title}}     │                                    │
│  │  Date: {{d:1}}        │  ← SignWell date tag (hidden)      │
│  │                       │                                    │
│  │  SMD Services assents │                                    │
│  │  by presenting this   │                                    │
│  │  SOW for signature.   │                                    │
│  └──────────────────────┘                                    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- Section heading: same style
- Intro sentence: Inter 400 10pt `#334155`
- Single signature block, 216pt wide, left-aligned to the page margin
- Column header ("CLIENT ACCEPTANCE"): Inter 600 9pt `#1e293b`
- Signature area: invisible `{{s:1}}` tag rendered in white at 36pt — SignWell places the signature field over the tag's bounding box
- Signature line: 1pt `#334155` rule, full column width, directly below the tag
- Name and title below line: Inter 400 9pt `#334155`
- Date row: visible "Date:" label (Inter 400 8pt `#64748b`) followed by an invisible `{{d:1}}` tag rendered in white at 11pt
- Assent note below date: Inter 400 8pt `#64748b`

**SignWell field placement architecture:**

Field placement uses **SignWell text tags** embedded in the PDF — not hardcoded coordinates. This keeps field positions and template layout in lockstep by construction.

- The template embeds `{{s:1}}` (client signature) and `{{d:1}}` (client date) inline in the signing block — rendered in white on white so they are invisible on the printed/unsigned PDF.
- The signing request passes `text_tags: true` in the SignWell API body and omits the `fields[]` array.
- SignWell scans the uploaded PDF at send time, finds the tags, and creates signature/date fields over their bounding boxes. The client's signature and date stamp are rendered on top of the tags at signing time, fully covering them.
- Template edits can no longer drift from field placement: moving/resizing the signing block moves the tags with it, which moves the fields.
- Tag syntax: `{{<type>:<signer>}}` short form — `s` = signature, `d` = date, signer `1` is the client. Ref: [SignWell text tag docs](https://developers.signwell.com/reference/adding-text-tags).
- Source: `src/lib/pdf/sow-template.tsx` (tag embedding) and `src/lib/sow/service.ts` (the `text_tags: true` flag).

**Placeholder fields:**
| Field | Source |
| ------------------------- | ------------------------------------------- |
| `{{s:1}}` | SignWell signature field (text-tag placed) |
| `{{client.contact_name}}` | Client contact record |
| `{{client.contact_title}}`| Client contact record |
| `{{d:1}}` | SignWell date field — auto-filled on sign |

### 5.5 Footer

Present on all three pages.

```
┌──────────────────────────────────────────────────────────────┐
│  SMD Services | smd.services           {{document.sow_number}} | Page X of 3  │
└──────────────────────────────────────────────────────────────┘
```

**Layout details:**

- 1pt `#e2e8f0` rule above footer, full printable width
- Left: "SMD Services | smd.services" — Inter 400 8pt `#64748b`
- Right: SOW number and page number — Inter 400 8pt `#64748b`
- 8pt below the rule

---

## 6. Complete Placeholder Field Map

All dynamic fields that the Forme template must accept from the quote/engagement data model.

| Field                          | Type     | Source Table | Required | Notes                                 |
| ------------------------------ | -------- | ------------ | -------- | ------------------------------------- |
| `{{client.business_name}}`     | string   | clients      | yes      |                                       |
| `{{client.contact_name}}`      | string   | contacts     | yes      | Primary contact on the engagement     |
| `{{client.contact_title}}`     | string   | contacts     | no       | Falls back to empty string            |
| `{{document.date}}`            | date     | generated    | yes      | Format: Month DD, YYYY                |
| `{{document.expiration_date}}` | date     | generated    | yes      | document.date + 5 business days       |
| `{{document.sow_number}}`      | string   | generated    | yes      | Format: SOW-YYYYMM-NNN                |
| `{{engagement.overview}}`      | text     | quotes       | yes      | Admin-written, 3-4 sentences max      |
| `{{items[n].name}}`            | string   | quote_items  | yes      | Deliverable name                      |
| `{{items[n].description}}`     | string   | quote_items  | yes      | Brief description of what's delivered |
| `{{engagement.start_date}}`    | date     | quotes       | yes      | Format: Month DD, YYYY                |
| `{{engagement.end_date}}`      | date     | quotes       | yes      | Format: Month DD, YYYY                |
| `{{quote.total_price}}`        | currency | quotes       | yes      | Format: $X,XXX                        |
| `{{payment.deposit}}`          | currency | calculated   | yes      | Format: $X,XXX                        |
| `{{payment.completion}}`       | currency | calculated   | yes      | Format: $X,XXX                        |
| `{{payment.milestone}}`        | currency | calculated   | no       | Only for 3-milestone engagements      |
| `{{payment.schedule}}`         | enum     | quotes       | yes      | "two_part" or "three_milestone"       |
| `{{smd.signer_name}}`          | string   | admin_users  | yes      |                                       |
| `{{smd.signer_title}}`         | string   | admin_users  | yes      |                                       |

---

## 7. Conditional Rendering

### 7.1 Payment Schedule Variants

**Two-part (default):** Show two rows — "Due at signing (50%)" and "Due on Day 8 (50%)."

**Three-milestone (40+ hour engagements):** Show three rows — "Due at signing," "Due at [milestone name]," and "Due at completion." Labels and amounts come from the quote payment configuration.

### 7.2 Contact Title

If `{{client.contact_title}}` is empty, omit the title line in the signature block. Do not render an empty line.

### 7.3 Deliverable Count

The scope table must render 1-8 rows dynamically. If items exceed 6, reduce row padding to 4pt vertical to maintain page 1 fit. If items exceed 8, the template should not render — surface an error to the admin. (Exceeding 8 deliverables likely signals scope that's too broad for one engagement.)

---

## 8. Branding Assets Required

| Asset             | Format  | Usage                | Status    |
| ----------------- | ------- | -------------------- | --------- |
| SMD Services logo | SVG/PNG | Header, top-left     | Needed    |
| Favicon/mark      | SVG/PNG | Watermark (optional) | Available |

The logo must work at 120pt x 40pt on a white background. If no formal logo exists at implementation time, use "SMD Services" in Plus Jakarta Sans 800 20pt `#1e40af` as a wordmark.

---

## 9. Forme Implementation Notes

### 9.1 Template Structure

The Forme template will be a JSX component at `src/lib/pdf/sow-template.tsx` (per PRD directory structure). It receives a typed props object matching the field map in Section 6.

```
SOWTemplate(props: SOWTemplateProps) → PDF document (3 pages)
```

### 9.2 Props Interface (TypeScript)

```typescript
interface SOWTemplateProps {
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
```

### 9.3 Testing Checklist

Before shipping the Forme template:

- [ ] Renders correctly with 1 deliverable item
- [ ] Renders correctly with 6 deliverable items (maximum without padding reduction)
- [ ] Renders correctly with 8 deliverable items (padding reduction kicks in)
- [ ] Errors on 9+ deliverable items
- [ ] Two-part payment renders correctly
- [ ] Three-milestone payment renders correctly
- [ ] Missing contact title renders without empty line
- [ ] All text fits within printable area (no clipping)
- [ ] Fonts render correctly in generated PDF
- [ ] PDF opens correctly in Preview, Chrome, Adobe Reader
- [ ] SignWell can overlay signature fields at expected coordinates
- [ ] Generated file size < 500KB

---

## 10. Content Voice Compliance

All static text in this template has been written in "we" voice per Decision #20. The following phrases appear in the template and must not be altered without reviewing the voice standard:

| Section         | Text excerpt                                            | Voice check          |
| --------------- | ------------------------------------------------------- | -------------------- |
| What's Included | "This engagement includes..."                           | Neutral              |
| Exclusions      | "...will be logged and reviewed together..."            | "we" / collaborative |
| Exclusions      | "...we'll propose a separate scope and estimate."       | "we"                 |
| Terms #2        | "We will confirm the start date..."                     | "we"                 |
| Terms #3        | "...we will address questions and minor adjustments..." | "we"                 |
| Agreement       | "...the client agrees to the scope..."                  | Neutral              |

No instance of "I," "the consultant," or "my" appears anywhere in this document.

---

_SMD Services | SOW Template Design Spec | Confidential_
