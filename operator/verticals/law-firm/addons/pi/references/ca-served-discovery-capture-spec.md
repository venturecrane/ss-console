# CA Served-Discovery Capture Spec

**What this is:** the taxonomy of what the Operator READS off a served California
discovery document, the discovery **type** and the **service method + date**, so it
can surface the right input for the court-rules engine (or, only where the firm
confirms deadlines are done by hand, present a computed date for attorney confirm).

**What this is NOT:** a deadline calculator. Per the pack's bright line
(`discovery-deadline-input-capture-only`), the certified court-rules engine
(LawToolBox / Smokeball-InfoTrack) owns the computation. The Operator captures the
inputs and reads/chases the engine's dates. The base arithmetic below is reference
for the present-for-confirm case only, and a computed date is **never** treated as
final without attorney confirmation.

> **Statute grounding: fetched and verified 2026-07-01.** Sources:
> [CCP §1013 (FindLaw)](https://codes.findlaw.com/ca/code-of-civil-procedure/ccp-sect-1013/),
> [CCP §1010.6 (FindLaw)](https://codes.findlaw.com/ca/code-of-civil-procedure/ccp-sect-1010-6/),
> [CCP §2030.260 (FindLaw)](https://codes.findlaw.com/ca/code-of-civil-procedure/ccp-sect-2030-260/),
> §2031.260, §2033.250. Also verified 2026-07-01 against
> [leginfo](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CCP&sectionNum=2016.060.):
> §2016.060 (final-day roll, governs the whole Discovery Act), the propounding-timing
> family §2030.020(b)/§2031.020(b)/§2033.020(b) (when a plaintiff may be served), and
> §2025.220(a)(4)/§2025.410 (deposition-notice document rider and its objection clock).
> Re-verify at connect and on any statute amendment; California
> discovery timing is amendment-prone. County/department local rules can add
> requirements and are **out of scope until A&P's actual venues are known**.

## 1. Discovery type taxonomy (what to identify)

| Type                                                  | Recognize by                                             | Response requires party verification?                                                    |
| ----------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Interrogatories** (Form: DISC-001/003, or Special)  | caption "Interrogatories"; numbered questions            | Yes (unless objections-only), §2030.250                                                  |
| **Requests for Production** (Inspection Demand)       | "Demand for … Production / Inspection"; numbered demands | Yes (unless objections-only), §2031.250                                                  |
| **Requests for Admission**                            | "Requests for Admission"; numbered matters to admit/deny | Yes (unless objections-only), §2033.240; **unsigned/late → deemed admitted (§2033.280)** |
| **Deposition notice**                                 | "Notice of Deposition"; date/time/place or remote        | No party verification; drives calendar + prep, not a response-verification               |
| **Compound document** (e.g. depo notice + doc demand) | depo notice carrying an embedded production rider        | Surface **both** facets; never file the whole thing as "no response clock" (see note)    |

The capture surfaces the type; it does not judge sufficiency or draft a response.

**Compound documents (do not collapse to one facet).** A Notice of Deposition can
carry an embedded document-production rider (§2025.220(a)(4), the "records only" or
hybrid deposition, which specifies materials the deponent must produce). That rider
has its own obligations: objections to the notice are due at least **3 calendar days**
before the deposition (§2025.410), and production happens at the deposition. When a
served document has more than one facet (a depo notice that is also a document
demand), the capture must surface **each** facet with its own clock. It must not be
recorded as a bare "deposition notice, no response clock," which would drop the
production and objection obligations. If it is unclear whether a rider is present,
surface and ask.

## 2. Service method + date (read off the PROOF OF SERVICE)

The proof of service (POS) at the end of the document states the **method** and
**date** of service. These are the inputs the deadline turns on. Read them; do not
infer them from the email/postmark alone (the POS is the authoritative statement).

| Method (as stated on the POS)        | Extension added to the base window | Statute          |
| ------------------------------------ | ---------------------------------- | ---------------- |
| Personal service                     | +0                                 | none             |
| Mail, place of address in California | **+5 calendar days**               | §1013(a)         |
| Mail, address elsewhere in the U.S.  | +10 calendar days                  | §1013(a)         |
| Mail, address outside the U.S.       | +20 calendar days                  | §1013(a)         |
| Overnight / express delivery         | **+2 court days**                  | §1013(c)         |
| Electronic service                   | **+2 court days**                  | §1010.6(a)(3)(B) |

**Multi-method / ambiguous POS is surface-and-ask.** A single proof of service that
states more than one method for the served party yields **different** extensions
(§1013 mail is +5 calendar days; §1010.6 electronic is +2 court days), and those can
resolve to different dates. When the POS lists more than one method for the same
party, or the method cannot be pinned to one row above, the capture **surfaces and
asks** the attorney. It never silently picks the shorter or the longer extension.

**Base response window:** **30 days** from service for interrogatories (§2030.260),
requests for production (§2031.260), and requests for admission (§2033.250). (The
30-day base is the general rule; shortened/extended variants exist by statute or
stipulation, the engine handles them; the capture only records the served type,
date, and method.)

**Court-day counting** (for the +2-court-day methods): weekends and court holidays
are excluded. The Operator does not implement the calendar; the rules engine does.
Where the firm computes by hand and the Operator presents a date for confirm, it
shows the base date, the method-extension applied, and the statute, always for
attorney confirmation, never final on its own.

**Final-day roll (§2016.060) is a distinct rule from court-day counting.** This is not
the +2-court-day method extension. §2016.060 governs the **entire** Civil Discovery
Act (Title 4): "when the last day to perform ... any act provided for in this title
falls on a Saturday, Sunday, or holiday as specified in Section 10, the time limit is
extended until the next court day closer to the trial date" (verified 2026-07-01). So
it applies to **every** computed deadline, including an all-calendar-day mail +5 date
under §1013 (which counts calendar days, not court days), not only the +2-court-day
overnight/electronic methods. The +2-court-day counting excludes weekends/holidays as
you count; §2016.060 is a separate, final check on wherever the count lands. On the
by-hand path the Operator must flag: "final day rolls to the next court day if it
lands on a weekend or holiday (§2016.060); attorney/engine confirms."

**Base-window variants (plaintiff-side caveat).** The 30-day base is not always the
base. Two verified variants: (a) in an unlawful detainer or other Chapter 4 proceeding
the base is **5 days** (§2030.260(b) / §2031.260(b) / §2033.250(b)), as verified
2026-07-01, those (b) subsections are the UD 5-day rule, and A&P is personal-injury
plaintiff-side so this should almost never fire, but the by-hand path should recognize
it rather than assume 30. (b) A plaintiff may only be **served** discovery on a
summons-anchored schedule (10 days after service of the summons on, or appearance by,
that party, §2030.020(b) / §2031.020(b) / §2033.020(b)); this governs when service is
valid, not a longer response window. A separate "response due the later of 30 days or a
summons-anchored date" extension is **not** confirmed in current CCP at the sections
checked; treat any such claim as **confirm at connect**, do not compute around it. On
the by-hand path, standing caveat: "base is 30 days unless a statutory variant applies
(UD 5-day §§2030.260(b)/2031.260(b)/2033.250(b), or an early-service rule to confirm at
connect); attorney confirms."

## 3. What the capture emits (the surfaced input)

For each served document, the Operator surfaces, for attorney confirmation:

- the matter it matched to (by case name + number),
- the discovery **type**,
- the **service date** and **method** as read off the POS (with the POS quoted/located),
- the derived response deadline **if** the firm computes by hand (base 30 days +
  method extension, with the statute cited), flagged "proposed, confirm",
- OR, where the rules engine is active, a note that the engine's date should be read
  and confirmed.

## 4. Fail-closed rules (anti-fiction)

- If the POS is missing, ambiguous, or the method/date cannot be read with
  confidence → **surface and ask**; never guess the method or date.
- If the POS states **more than one method** for the served party → **surface and
  ask**; never silently pick the shorter or longer extension (§1013 +5 cal vs
  §1010.6 +2 court resolve to different dates).
- If the discovery type is unclear → surface for confirmation; never default.
- If the served document is **compound** (e.g. a deposition notice carrying a
  §2025.220(a)(4) document rider) → surface **both** facets and their clocks; never
  file it as a bare notice with "no response clock."
- A computed by-hand date must carry the **final-day-roll** flag (§2016.060: rolls to
  next court day if it lands on a weekend/holiday) and, where relevant, the
  **base-variant** flag (30-day base is not universal); attorney/engine confirms.
- A computed date is a **proposal for attorney confirm**, never calendared silently.
- Local/department rules that shorten or add to the timeline are **not** applied
  until A&P's venues are configured, where a local rule might govern, surface it as
  a flag, do not compute around it.
