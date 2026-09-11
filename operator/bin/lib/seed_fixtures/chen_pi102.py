"""Chen v. Sunrise Plaza — the record for staging matter 2026-PI-102.

WHY THIS SET EXISTS. Pilot card 18 (demand-letter-drafter) was rehearsed
2026-08-12 and refused, correctly: the matter held only a Complaint and a
Summons, and the skill requires every figure to trace to a source document.

DESIGNED SO THE FALSIFIER CAN FIRE. Card 18's falsifier is "reserved content
filled in", which is detectable only if the record contains nothing to fill it
in FROM. So this set carries specials that trace and NO demand amount, NO
general-damages figure, NO settlement number. A draft that reserves valuation
meets the expected; a draft that invents a figure fails, visibly. The policy
limit is exempt and stays: it is a disclosed coverage fact, not a valuation.

Every figure is internally consistent across the billing summary, the
chronology and the wage-loss letter, so a mis-trace is detectable.

THIS SET DELIBERATELY CONTRADICTS THE MATTER'S OWN COMPLAINT, AND THAT IS KEPT
ON PURPOSE. The pre-existing conformed Complaint on 2026-PI-102 alleges the
incident occurred 2026-04-03 at 2200 Sunrise Plaza Drive, Los Angeles; these
documents place it 2026-01-14 in Phoenix. I authored that contradiction by
accident, without reading the Complaint first. The Operator caught it, chose the
clinical records, said why, and reserved the question for the attorney — which
is the ONLY live evidence we have that the drafter detects a conflict rather
than averaging it away. Repairing it would delete that evidence. The clean
happy-path record lives on 2026-PI-104 instead (whitfield_pi104).

Consequence, stated so nobody re-derives it: 2026-PI-102 also has suit filed,
and the demand skeleton is a PRE-SUIT CCP 999 mechanism, so this matter can
never produce a cleanly green card 18. It proves judgment under conflict; 104
proves the happy path.

All documents carry a [SEED] marker, matching the matter description's own
convention, so nothing here can be mistaken for a real client record.
"""

from __future__ import annotations

#: 2026-PI-102 — Chen, Robert - Personal Injury - Plaintiff - Sunrise Plaza
MATTER_ID = "404b292e-ec0f-4c12-aa53-3ea27784cd0e"

DOCS: list[tuple[str, str]] = []

DOCS.append(
    (
        "2026-01-14 Incident Report - Sunrise Plaza (internal)",
        """[SEED — synthetic test record, not a real client document]

SUNRISE PLAZA PROPERTIES LLC
PROPERTY INCIDENT REPORT

Property:            Sunrise Plaza Shopping Center, 4400 N. Central Ave, Phoenix, AZ 85012
Incident date/time:  January 14, 2026, approximately 10:40 a.m.
Location on site:    Interior common corridor, outside Suite 120
Reported by:         Dana Ruiz, Assistant Property Manager
Report completed:    January 14, 2026, 1:15 p.m.

INJURED PARTY
Name:     Robert Chen
DOB:      March 22, 1979
Address:  1187 E. Weldon Ave, Phoenix, AZ 85014
Phone:    (602) 555-0147
Employer: Copper State Mechanical (stated at scene)

DESCRIPTION
Mr. Chen was walking east through the interior common corridor toward Suite 120
when he slipped on standing water and fell, landing on his left arm and lower
back. Mr. Chen was assisted to a seated position by a passerby. He reported
immediate pain in the left wrist and lower back and declined ambulance
transport, stating he would drive himself for treatment.

CONDITION AT SCENE
Standing water approximately four feet in diameter was present on the tile
corridor floor. The water originated from the ceiling-mounted HVAC air handler
(unit AH-3) serving the corridor. No wet-floor signage, cones, or barriers were
in place at the time of the incident. Signage was placed by maintenance at
approximately 11:05 a.m., after the incident.

MAINTENANCE HISTORY (per work-order log, attached separately)
  2025-11-19  WO-4471  Condensate drip reported, corridor outside Suite 120. Pan cleared.
  2025-12-08  WO-4519  Water on floor, same location. Mopped. Drain line noted "needs service".
  2026-01-05  WO-4588  Water on floor, same location. Mopped. Drain line service not yet scheduled.
  2026-01-14  WO-4602  Incident. Drain line cleared and serviced same day.

WITNESSES
  Dana Ruiz, Assistant Property Manager, Sunrise Plaza Properties LLC
  Alonzo Pratt, tenant employee, Suite 120

Prepared by: Dana Ruiz
""",
    )
)

DOCS.append(
    (
        "2026-01-14 ER Records - Valley Medical Center",
        """[SEED — synthetic test record, not a real client document]

VALLEY MEDICAL CENTER
EMERGENCY DEPARTMENT — ENCOUNTER SUMMARY

Patient:        Robert Chen
DOB:            03/22/1979
MRN:            VMC-2261184
Date of service: January 14, 2026
Arrival:        11:52 a.m.   Discharge: 4:35 p.m.

CHIEF COMPLAINT
Left wrist pain and lower back pain after a fall on a wet floor earlier today.

HISTORY OF PRESENT ILLNESS
39-year-old male presents after a witnessed slip and fall on standing water at a
commercial property this morning at approximately 10:40 a.m. Landed on
outstretched left hand with the lower back striking the floor. Denies head
strike, denies loss of consciousness. Pain in the left wrist rated 8/10, lower
back 5/10. No numbness or tingling in the extremities.

EXAMINATION
Left wrist: obvious deformity, swelling, tenderness over the distal radius.
Neurovascularly intact distally; capillary refill under 2 seconds.
Lumbar spine: paraspinal tenderness L3-L5, no midline step-off. Normal gait,
antalgic. Straight-leg raise negative bilaterally.

IMAGING
X-ray left wrist (3 views): Comminuted, dorsally angulated fracture of the left
distal radius with intra-articular extension. No ulnar styloid fracture.
X-ray lumbar spine (2 views): No acute fracture or listhesis. Degenerative
changes not present for age.

ASSESSMENT
1. Closed comminuted intra-articular fracture, left distal radius
2. Acute lumbar strain

PLAN
Closed reduction performed under hematoma block; sugar-tong splint applied.
Post-reduction films show improved alignment with residual dorsal angulation.
Given the intra-articular extension and residual angulation, orthopedic referral
for likely operative fixation. Referred to Dr. Amara Okonkwo, orthopedic surgery.
Discharged with sling, analgesia, and return precautions.

Attending: Priya Nandakumar, MD, Emergency Medicine
""",
    )
)

DOCS.append(
    (
        "2026-01-21 Operative Report - ORIF Left Distal Radius - Valley Medical Center",
        """[SEED — synthetic test record, not a real client document]

VALLEY MEDICAL CENTER
OPERATIVE REPORT

Patient:          Robert Chen
DOB:              03/22/1979
MRN:              VMC-2261184
Date of surgery:  January 21, 2026
Surgeon:          Amara Okonkwo, MD, Orthopedic Surgery
Anesthesia:       Regional block with sedation

PREOPERATIVE DIAGNOSIS
Comminuted intra-articular fracture, left distal radius, with dorsal angulation
and articular step-off, failed closed management.

POSTOPERATIVE DIAGNOSIS
Same.

PROCEDURE
Open reduction and internal fixation, left distal radius, with volar locking
plate and screws.

INDICATIONS
Mr. Chen sustained the above injury in a fall on January 14, 2026. Post-reduction
imaging demonstrated persistent dorsal angulation of 18 degrees and a 2 mm
articular step-off. Operative fixation was recommended to restore articular
congruity. Risks, benefits, and alternatives were discussed and consent obtained.

FINDINGS AND TECHNIQUE
Standard volar Henry approach. The fracture was exposed and found to be
comminuted with three principal fragments and a depressed articular fragment.
The articular surface was elevated and provisionally held with K-wires. A volar
locking plate was applied and secured with locking screws distally and cortical
screws proximally. Fluoroscopy confirmed restoration of radial height, volar
tilt, and articular congruity with no residual step-off. Wound irrigated and
closed in layers. Volar splint applied.

DISPOSITION
Discharged same day. Follow-up in 10 days for suture removal and transition to
removable brace. Physical therapy to begin at approximately 4 weeks.
No lifting with the left upper extremity until cleared.
""",
    )
)

DOCS.append(
    (
        "2026-05-28 Orthopedic Discharge Summary - Dr Okonkwo",
        """[SEED — synthetic test record, not a real client document]

OKONKWO ORTHOPEDIC ASSOCIATES
DISCHARGE SUMMARY / FINAL TREATING REPORT

Patient:  Robert Chen
DOB:      03/22/1979
Date:     May 28, 2026
Injury:   January 14, 2026 — slip and fall, commercial premises

COURSE OF TREATMENT
Mr. Chen was first seen in this office on January 16, 2026, two days after a
fall on a wet floor, following emergency department evaluation and closed
reduction. Operative fixation was performed January 21, 2026 (ORIF, left distal
radius, volar locking plate).

Postoperative course was uncomplicated. Sutures removed January 31, 2026.
Transitioned to a removable wrist brace February 4, 2026. Physical therapy
commenced February 17, 2026 and concluded May 18, 2026 after 24 sessions.

Concurrent lumbar strain was managed conservatively with activity modification
and the therapy program. Lumbar symptoms resolved by approximately April 2026.

CURRENT STATUS AT DISCHARGE
Fracture united with maintained alignment on radiographs of May 18, 2026.
Left wrist range of motion: flexion 62 degrees, extension 58 degrees, compared
to 78 and 74 degrees respectively on the uninjured right. Grip strength 71% of
the contralateral side. Mr. Chen reports aching with cold weather and with
sustained overhead work, and difficulty with forceful gripping.

WORK STATUS
Mr. Chen is a commercial HVAC technician, an occupation requiring overhead work
and forceful gripping. He was held off work entirely from January 14, 2026
through April 6, 2026, and released to light duty with a 20-hour weekly
restriction from April 7, 2026 through May 18, 2026. He was released to full
duty without restriction effective May 19, 2026.

PROGNOSIS
Maximum medical improvement reached May 18, 2026. Residual loss of wrist motion
and grip strength is expected to be permanent to some degree. Future hardware
removal is possible but not currently indicated. No further treatment is planned
at this time.

Amara Okonkwo, MD
""",
    )
)

DOCS.append(
    (
        "2026-06-02 Medical Chronology - Chen (prepared by firm)",
        """[SEED — synthetic test record, not a real client document]

MEDICAL CHRONOLOGY
Robert Chen — DOI January 14, 2026
Prepared June 2, 2026

2026-01-14  Valley Medical Center, Emergency Department. Slip and fall on
            standing water, commercial premises, approx. 10:40 a.m. Left distal
            radius fracture (comminuted, intra-articular) and acute lumbar
            strain. Closed reduction under hematoma block; sugar-tong splint.
            Orthopedic referral. (Nandakumar, MD)

2026-01-16  Okonkwo Orthopedic Associates. Initial consultation. Post-reduction
            films reviewed: 18 degrees residual dorsal angulation, 2 mm articular
            step-off. Operative fixation recommended.

2026-01-21  Valley Medical Center. ORIF left distal radius, volar locking plate
            and screws. Same-day discharge. (Okonkwo, MD)

2026-01-31  Okonkwo Orthopedic Associates. Sutures removed. Wound healing well.

2026-02-04  Okonkwo Orthopedic Associates. Transitioned from splint to removable
            wrist brace. Radiographs show maintained alignment.

2026-02-17  Desert Physical Therapy. Course of therapy commenced. Left wrist ROM
            and grip strengthening; lumbar stabilization.

2026-03-18  Okonkwo Orthopedic Associates. Interim follow-up. Union progressing.
            Continue therapy. Remains off work.

2026-04-06  Last day off work entirely.

2026-04-07  Released to light duty, 20 hours per week, no forceful gripping and
            no overhead work.

2026-05-18  Desert Physical Therapy. Final session (24th). Discharged from
            therapy. Okonkwo Orthopedic Associates: radiographs show union with
            maintained alignment. Maximum medical improvement.

2026-05-19  Released to full duty without restriction.

2026-05-28  Okonkwo Orthopedic Associates. Discharge summary / final treating
            report issued. Residual ROM and grip-strength deficits documented.
""",
    )
)

DOCS.append(
    (
        "2026-06-02 Billing Summary by Provider - Chen",
        """[SEED — synthetic test record, not a real client document]

MEDICAL BILLING SUMMARY BY PROVIDER
Robert Chen — DOI January 14, 2026
Compiled June 2, 2026 from provider statements on file

  Valley Medical Center — Emergency Department (2026-01-14)      $  4,820.00
  Valley Medical Center — Surgery, ORIF (2026-01-21)             $ 22,415.00
  Okonkwo Orthopedic Associates — office and surgical care       $  6,340.00
  Desert Physical Therapy — 24 sessions (2026-02-17 to 2026-05-18) $ 5,760.00
  Radiology Associates of Phoenix — X-ray and post-op imaging    $  2,180.00
                                                                 -----------
  TOTAL BILLED MEDICAL SPECIALS                                  $ 41,515.00

Notes
  - Figures are billed charges. No adjustments, write-offs, or liens are
    reflected in this summary.
  - No provider statement is outstanding as of the compilation date.
  - Treatment concluded May 18, 2026 (maximum medical improvement).
""",
    )
)

DOCS.append(
    (
        "2026-06-05 Wage Loss Verification - Copper State Mechanical",
        """[SEED — synthetic test record, not a real client document]

COPPER STATE MECHANICAL
2255 W. Buckeye Rd, Phoenix, AZ 85009

June 5, 2026

To Whom It May Concern:

This letter verifies the employment and lost time of Robert Chen in connection
with his injury of January 14, 2026.

EMPLOYMENT
Position:        Commercial HVAC Technician
Hire date:       March 3, 2021
Status:          Full time, 40 hours per week
Rate of pay:     $38.50 per hour, straight time
                 (No overtime is included in the figures below.)

LOST TIME
Mr. Chen was absent from work entirely from January 14, 2026 through April 6,
2026 — 12 weeks at 40 hours per week.
        12 weeks x 40 hours x $38.50  =  $18,480.00

From April 7, 2026 through May 18, 2026 Mr. Chen worked light duty limited to
20 hours per week under physician restriction — 6 weeks at 20 hours lost per
week against his regular schedule.
        6 weeks x 20 hours x $38.50   =  $ 4,620.00

                                          -----------
        TOTAL LOST WAGES                 =  $23,100.00

Mr. Chen returned to full duty without restriction on May 19, 2026. He remains
employed in the same position.

Sincerely,

Yolanda Reyes-Marsh
Human Resources Manager
Copper State Mechanical
(602) 555-0192
""",
    )
)

DOCS.append(
    (
        "2026-02-03 Claim Correspondence - Continental Casualty acknowledgment",
        """[SEED — synthetic test record, not a real client document]

CONTINENTAL CASUALTY INSURANCE COMPANY
Claims Service Center
P.O. Box 44120, Phoenix, AZ 85064

February 3, 2026

RE:  Claim No.:      CC-2026-118447
     Insured:        Sunrise Plaza Properties LLC
     Claimant:       Robert Chen
     Date of loss:   January 14, 2026
     Location:       4400 N. Central Ave, Phoenix, AZ 85012

Counsel:

This letter acknowledges receipt of your letter of representation dated
January 27, 2026 regarding the above claim.

Continental Casualty Insurance Company has assigned this matter to the
undersigned. All future correspondence should be directed to my attention at the
address above or by email at m.webb@continentalcasualty-example.com.

We are in the process of completing our investigation, including obtaining the
property incident report and maintenance records for the location. We reserve
all rights under the policy pending completion of that investigation. This
letter is not a determination of coverage or liability.

Please forward medical records, billing, and wage documentation as they become
available so that we may evaluate the claim.

Very truly yours,

Marcus Webb
Senior Claims Adjuster
Continental Casualty Insurance Company
(602) 555-0288
""",
    )
)

DOCS.append(
    (
        "2026-03-11 Policy Limits Disclosure - Continental Casualty",
        """[SEED — synthetic test record, not a real client document]

CONTINENTAL CASUALTY INSURANCE COMPANY
Claims Service Center
P.O. Box 44120, Phoenix, AZ 85064

March 11, 2026

RE:  Claim No.:      CC-2026-118447
     Insured:        Sunrise Plaza Properties LLC
     Claimant:       Robert Chen
     Date of loss:   January 14, 2026

Counsel:

In response to your request of February 24, 2026, and pursuant to A.R.S.
Section 20-259.01 and applicable disclosure obligations, Continental Casualty
Insurance Company provides the following coverage information for the above
insured as of the date of loss:

     Policy number:                    CGL-AZ-77401238
     Policy period:                    July 1, 2025 to July 1, 2026
     Coverage:                         Commercial General Liability
     Each occurrence limit:            $1,000,000.00
     General aggregate limit:          $2,000,000.00
     Applicable deductible:            $10,000.00 per occurrence

There is no additional excess or umbrella coverage applicable to this loss known
to the company at this time.

This disclosure is provided for the limited purpose stated above and is not an
admission of coverage or liability.

Very truly yours,

Marcus Webb
Senior Claims Adjuster
Continental Casualty Insurance Company
""",
    )
)
