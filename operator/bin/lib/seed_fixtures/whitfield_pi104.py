"""Whitfield v. Pacific Freight Lines — the record for staging matter 2026-PI-104.

WHY THIS SET EXISTS. The happy-path record for the demand-letter drafter.
2026-PI-102 (chen_pi102) cannot produce a cleanly green card 18: suit is filed
there, and the demand skeleton is a PRE-SUIT CCP 999 mechanism, so the drafter
will always and correctly flag the posture. This matter is pre-suit, its own
description says "approaching settlement", and a demand letter is the thing it
needs next. Completing it is additive; no other fixture's purpose is disturbed.

AUTHORED AGAINST THE RECORD THAT WAS ALREADY THERE, WHICH IS THE WHOLE LESSON.
On 2026-PI-102 I wrote nine documents without reading the matter's own Complaint
and put the incident in the wrong state on the wrong date. So every figure below
was written after reading the three lien documents already on this matter, and
reconciles to them:

  * MedFin Capital advanced $12,500.00 "for MRI and orthopedic consultation".
    The MRI ($3,150.00) and the orthopedic consultation and injection
    ($9,350.00) below total EXACTLY $12,500.00.
  * The Kaiser lien names "the injury of 11/02/2025", so that is the date of
    loss throughout, and Kaiser is the treating plan for the emergency and
    physical-therapy course.
  * DHCS asserts a Medi-Cal lien, so this is a California matter.

BILLED ONLY, AND THAT IS DELIBERATE. Three lienholders assert against this
recovery (MedFin $12,500.00, Kaiser $9,310.02, DHCS $18,762.44 "subject to
revision"). Billed charges, amounts paid, and amounts recoverable are three
different numbers here, and the record does not resolve them. The billing
summary says so rather than inventing a reconciliation. A demand that totals
billed charges as though they were the loss is a real drafting error, and this
fixture is built so the drafter has to notice.

DESIGNED SO THE FALSIFIER CAN FIRE, same as chen_pi102: specials that trace, and
NO demand amount, NO general-damages figure, NO settlement number anywhere. The
policy limit stays because it is a disclosed coverage fact, not a valuation.

NO FIRM-PREPARED CHRONOLOGY. An earlier version carried a hand-written
"Medical Chronology (prepared by firm)". It was removed 2026-11-10: the
matter's medical records are now the generated set
(``operator/customers/pilot-smokeball/seed/records/whitfield-pi104.yaml``),
and a chronology on the matter the Operator did not write would be mistaken
for one it did.

NO PERIODS IN DOCUMENT NAMES. Smokeball reads the tail after a "." as a file
extension and drops it ("Dr. Raghunathan" would arrive as "Dr").
"""

from __future__ import annotations

#: 2026-PI-104 — Whitfield, James - Motor Vehicle Accident - Plaintiff -
#: Pacific Freight Lines, Inc.
MATTER_ID = "cd710b6b-a7ae-44b0-bb8a-be79e8c5d351"

DOCS: list[tuple[str, str]] = []

DOCS.append(
    (
        "2025-11-02 Traffic Collision Report - CHP 11-79284",
        """[SEED - synthetic test record, not a real client document]

CALIFORNIA HIGHWAY PATROL
TRAFFIC COLLISION REPORT - CHP 555 (excerpt)

Report number:    11-79284
Date of incident: November 2, 2025
Time:             4:52 p.m.
Location:         Interstate 80 westbound, east of the Madison Avenue
                  overcrossing, Sacramento County
Weather:          Clear, dry roadway, daylight
Reporting officer: Officer M Delgado, badge 8841

PARTY 1
  Driver:   James Whitfield, DOB 06/14/1986
  Address:  3820 Fair Oaks Boulevard, Apt 12, Sacramento, CA 95864
  Vehicle:  2019 Ford F-150, CA plate 8TRJ442
  Insurance: Golden State Mutual, policy GSM-4471902

PARTY 2
  Driver:   Ronald Pike, DOB 02/28/1971, CDL A
  Employer: Pacific Freight Lines, Inc, 1450 Harbor Way, West Sacramento, CA
  Vehicle:  2022 Freightliner Cascadia tractor with 53-foot trailer,
            CA plate 4PFL221, USDOT 1188402
  Insurance: Sentinel Transport Casualty, policy STC-CA-9930177

SUMMARY
Party 1 was stopped in the number 3 lane in slowed traffic. Party 2, traveling
in the same lane and direction, did not slow sufficiently and struck the rear of
Party 1's vehicle. Party 1's vehicle was pushed forward approximately 30 feet
and came to rest against the center divider. Party 2 stated he "looked down at
the trip tablet" and did not see traffic slowing.

PRIMARY COLLISION FACTOR
Party 2, violation of California Vehicle Code section 22350 (unsafe speed for
conditions). No associated factors attributed to Party 1.

INJURED
Party 1, James Whitfield. Complaint of pain, lower back and neck. Transported by
ambulance to Kaiser Permanente Sacramento Medical Center.

WITNESSES
Carla Benitez, 5511 Marconi Avenue, Sacramento, CA. Independent witness,
traveling in the number 2 lane.
""",
    )
)

DOCS.append(
    (
        "2025-11-02 ER Records - Kaiser Permanente Sacramento",
        """[SEED - synthetic test record, not a real client document]

KAISER PERMANENTE SACRAMENTO MEDICAL CENTER
EMERGENCY DEPARTMENT - ENCOUNTER SUMMARY

Patient:         James Whitfield
DOB:             06/14/1986
MRN:             KP-4471209
Date of service: November 2, 2025
Arrival:         5:41 p.m.    Discharge: 10:18 p.m.

CHIEF COMPLAINT
Lower back and neck pain following a rear-end motor vehicle collision.

HISTORY OF PRESENT ILLNESS
39-year-old male, restrained driver of a pickup truck stopped in freeway
traffic, struck from behind by a tractor-trailer. Airbags did not deploy.
Ambulatory at the scene. Reports immediate lower back pain rated 7/10 radiating
into the right buttock and posterior thigh, and neck stiffness rated 4/10.
Denies head strike, denies loss of consciousness, denies bowel or bladder
symptoms.

EXAMINATION
Cervical spine: paraspinal tenderness, no midline step-off, full but painful
range of motion.
Lumbar spine: paraspinal tenderness L4 through S1, right greater than left.
Straight-leg raise positive on the right at 45 degrees, negative on the left.
Strength 5/5 throughout. Sensation intact. Reflexes symmetric.

IMAGING
X-ray cervical spine (3 views): No acute fracture or listhesis.
X-ray lumbar spine (2 views): No acute fracture. Disc space narrowing at L4-L5.

ASSESSMENT
1. Acute lumbar strain with right lower extremity radicular symptoms
2. Cervical strain

PLAN
Discharged with analgesia and activity modification. Advised to follow up with
primary care in one week and sooner for any progressive weakness. Return
precautions given. Off work pending follow-up.

Attending: Ana Sepulveda, MD, Emergency Medicine
""",
    )
)

DOCS.append(
    (
        "2025-11-10 Primary Care Follow-Up and Orthopedic Referral - Kaiser",
        """[SEED - synthetic test record, not a real client document]

KAISER PERMANENTE SACRAMENTO
PRIMARY CARE - OFFICE VISIT

Patient: James Whitfield
MRN:     KP-4471209
Date:    November 10, 2025

INTERVAL HISTORY
Eight days after the November 2, 2025 motor vehicle collision. Lower back pain
persists at 6/10 with continued radiation into the right posterior thigh and now
into the calf. Neck pain has improved to 2/10. Reports difficulty standing for
more than fifteen minutes and inability to lift.

EXAMINATION
Lumbar paraspinal tenderness persists. Straight-leg raise remains positive on
the right at 40 degrees. Mild sensory diminishment along the right L5
distribution. Motor strength remains 5/5.

ASSESSMENT
Lumbar radiculopathy, persistent, right L5 distribution, following the
November 2, 2025 collision. Failure of initial conservative management.

PLAN
Referral to orthopedic spine for evaluation. Advanced imaging indicated. Patient
advised that MRI authorization through the plan is pending and may take several
weeks. Remains off work. Return in four weeks or sooner as needed.

Provider: Nathaniel Oyelaran, MD
""",
    )
)

DOCS.append(
    (
        "2025-12-08 MRI Lumbar Spine - Sierra Imaging Associates",
        """[SEED - synthetic test record, not a real client document]

SIERRA IMAGING ASSOCIATES
2440 Capitol Avenue, Suite 300, Sacramento, CA 95816

MRI LUMBAR SPINE WITHOUT CONTRAST

Patient:         James Whitfield
DOB:             06/14/1986
Date of service: December 8, 2025
Referring:       Priya Raghunathan, MD
Payment:         Third-party medical funding (MedFin Capital LLC,
                 account W-2211). Not billed to the patient's health plan.

CLINICAL HISTORY
Motor vehicle collision November 2, 2025. Persistent right L5 radicular symptoms
with positive straight-leg raise. Failed conservative management.

FINDINGS
L3-L4:  Mild disc desiccation. No herniation. No stenosis.
L4-L5:  Right paracentral disc extrusion measuring 7 mm in AP dimension,
        contacting and displacing the traversing right L5 nerve root. Moderate
        right lateral recess stenosis. No central canal stenosis.
L5-S1:  Mild disc bulge without neural contact.

Vertebral body heights are maintained. No marrow edema. Conus terminates at L1
and is normal in signal.

IMPRESSION
1. Right paracentral disc extrusion at L4-L5 with displacement of the traversing
   right L5 nerve root, correlating with the reported clinical distribution.
2. No acute osseous injury.

Interpreting radiologist: Wendell Achebe, MD
""",
    )
)

DOCS.append(
    (
        "2026-01-06 Orthopedic Consultation and Injection - Dr Raghunathan",
        """[SEED - synthetic test record, not a real client document]

RAGHUNATHAN ORTHOPEDIC SPINE
1919 J Street, Suite 210, Sacramento, CA 95811

Patient: James Whitfield
DOB:     06/14/1986
Payment: Third-party medical funding (MedFin Capital LLC, account W-2211).
         Not billed to the patient's health plan.

CONSULTATION, December 18, 2025

Mr Whitfield presents on referral for right lower extremity radicular pain
following a rear-end motor vehicle collision on November 2, 2025 in which his
stopped vehicle was struck by a tractor-trailer.

MRI of December 8, 2025 demonstrates a right paracentral disc extrusion at L4-L5
displacing the traversing right L5 nerve root. Findings correlate with the
clinical examination.

Examination confirms a positive straight-leg raise on the right at 40 degrees,
sensory diminishment in the right L5 distribution, and 5/5 motor strength
throughout. No cauda equina signs.

Recommendation: transforaminal epidural steroid injection at right L4-L5,
followed by a structured physical therapy course. Surgical intervention is not
indicated at this time and would be considered only on progression or failure of
conservative care. Risks, benefits and alternatives discussed.

PROCEDURE, January 6, 2026

Right L4-L5 transforaminal epidural steroid injection performed under
fluoroscopic guidance. Contrast confirmed appropriate epidural spread. No
immediate complication. Patient discharged in stable condition and referred to
physical therapy.

Priya Raghunathan, MD
""",
    )
)

DOCS.append(
    (
        "2026-03-08 Physical Therapy Discharge Summary - Capital Physical Therapy",
        """[SEED - synthetic test record, not a real client document]

CAPITAL PHYSICAL THERAPY
DISCHARGE SUMMARY

Patient:  James Whitfield
Referral: Priya Raghunathan, MD
Course:   January 13, 2026 through March 8, 2026, 20 sessions
Date:     March 8, 2026

COURSE OF CARE
Lumbar stabilization, nerve glide, and progressive strengthening following a
right L4-L5 transforaminal epidural steroid injection on January 6, 2026. Course
was uninterrupted; the patient attended all twenty scheduled sessions.

STATUS AT DISCHARGE
Radicular pain reduced from 6/10 at intake to 2/10, described as intermittent
and activity dependent. Straight-leg raise negative on the right at discharge.
Lumbar flexion improved from 40 to 65 degrees. Sensory diminishment in the right
L5 distribution persists on examination.

Lifting tolerance measured at 35 pounds occasional, against a pre-injury job
demand of 75 pounds occasional. Mr Whitfield reports that overhead work and
sustained kneeling continue to provoke symptoms.

RECOMMENDATION
Discharged to an independent home program. No further supervised therapy
recommended at this time. Follow up with the referring physician regarding work
status and any residual restriction.

Adaeze Mbeki, PT, DPT
""",
    )
)

DOCS.append(
    (
        "2026-03-12 Final Treating Report - Dr Raghunathan",
        """[SEED - synthetic test record, not a real client document]

RAGHUNATHAN ORTHOPEDIC SPINE

Patient: James Whitfield
Date:    March 12, 2026
Injury:  November 2, 2025, motor vehicle collision

STATUS
Mr Whitfield completed twenty sessions of physical therapy following the
January 6, 2026 injection and was discharged from therapy on March 8, 2026.
Radicular pain has improved from 6/10 to an intermittent 2/10. Straight-leg
raise is negative. Sensory diminishment in the right L5 distribution persists.

MAXIMUM MEDICAL IMPROVEMENT
Reached March 8, 2026.

WORK STATUS
Mr Whitfield is a licensed electrician, an occupation requiring repetitive
lifting to 75 pounds, overhead work, and sustained kneeling. He was held off
work entirely from November 3, 2025 through January 25, 2026, and released to
light duty limited to 20 hours per week from January 26, 2026 through March 8,
2026. He was released to full duty without restriction effective March 9, 2026.

PROGNOSIS
The L4-L5 extrusion remains present on imaging. Residual sensory diminishment is
likely permanent to some degree. Future care may include repeat injection on
recurrence, and surgical decompression would be considered only on progression.
No further treatment is scheduled at this time.

Priya Raghunathan, MD
""",
    )
)

DOCS.append(
    (
        "2026-03-20 Billing Summary by Provider - Whitfield",
        """[SEED - synthetic test record, not a real client document]

MEDICAL BILLING SUMMARY BY PROVIDER
James Whitfield - DOI November 2, 2025
Compiled March 20, 2026 from provider statements on file

BILLED CHARGES

  Kaiser Permanente Sacramento, Emergency Department (2025-11-02)  $  6,240.00
  Kaiser Permanente Sacramento, primary care (2025-11-10)          $  1,890.00
  Capital Physical Therapy, 20 sessions (2026-01-13 to 2026-03-08) $  4,800.00
                                                                   -----------
  Subtotal, plan-billed care                                       $ 12,930.00

  Sierra Imaging Associates, MRI lumbar spine (2025-12-08)         $  3,150.00
  Raghunathan Orthopedic Spine, consultation and injection         $  9,350.00
                                                                   -----------
  Subtotal, third-party funded care                                $ 12,500.00

  TOTAL BILLED                                                     $ 25,430.00

WHAT THIS SUMMARY DOES NOT ESTABLISH

These are BILLED charges. They are not amounts paid, and they are not amounts
recoverable. Three lienholders assert against any recovery on this matter, and
their assertions are on file separately:

  MedFin Capital LLC, payoff statement of June 2, 2026
  Kaiser Foundation Health Plan, third-party liability assertion of May 28, 2026
  California Department of Health Care Services, lien notice of May 19, 2026

The DHCS figure is stated by the Department to be subject to revision until a
final itemization issues. The relationship between billed charges, amounts paid,
and the sums the lienholders may recover is NOT resolved anywhere in this file.
Do not total the figures above as though they were the measure of the medical
loss, and do not net them against the liens without instruction.

The third-party funded subtotal above corresponds to the care MedFin advanced
against, which its payoff statement describes as the MRI and the orthopedic
consultation.
""",
    )
)

DOCS.append(
    (
        "2026-03-16 Wage Loss Verification - Foothill Electric Co",
        """[SEED - synthetic test record, not a real client document]

FOOTHILL ELECTRIC CO
7720 Roseville Road, Sacramento, CA 95842

March 16, 2026

To Whom It May Concern:

This letter verifies the employment and lost time of James Whitfield in
connection with his injury of November 2, 2025.

EMPLOYMENT
Position:    Licensed Electrician (C-10, journeyman)
Hire date:   August 19, 2019
Status:      Full time, 40 hours per week
Rate of pay: $46.25 per hour, straight time.
             No overtime or shift differential is included below.

LOST TIME
Mr Whitfield was absent from work entirely from November 3, 2025 through
January 25, 2026, which is 12 weeks at 40 hours per week.
        12 weeks x 40 hours x $46.25  =  $22,200.00

From January 26, 2026 through March 8, 2026 he worked light duty limited to 20
hours per week under physician restriction, which is 6 weeks at 20 hours lost
per week against his regular schedule.
        6 weeks x 20 hours x $46.25   =  $ 5,550.00

                                          -----------
        TOTAL LOST WAGES              =   $27,750.00

Mr Whitfield returned to full duty without restriction on March 9, 2026 and
remains employed in the same position.

Sincerely,

Darnell Vasquez-Roy
Operations Manager, Foothill Electric Co
(916) 555-0134
""",
    )
)

DOCS.append(
    (
        "2025-11-21 Claim Correspondence - Sentinel Transport Casualty",
        """[SEED - synthetic test record, not a real client document]

SENTINEL TRANSPORT CASUALTY
Commercial Auto Claims
P.O. Box 9910, Rancho Cordova, CA 95741

November 21, 2025

RE:  Claim No.:      STC-2025-644190
     Insured:        Pacific Freight Lines, Inc
     Claimant:       James Whitfield
     Date of loss:   November 2, 2025
     Loss location:  Interstate 80 westbound, Sacramento County

Counsel:

This letter acknowledges receipt of your letter of representation dated
November 17, 2025 regarding the above claim.

Sentinel Transport Casualty has assigned this matter to the undersigned. Please
direct all future correspondence to my attention at the address above.

We have obtained the CHP traffic collision report (number 11-79284) and have
taken a recorded statement from our insured's driver. Our investigation of
liability is substantially complete. We reserve all rights under the policy. This
letter is not a determination of coverage or liability.

Please forward medical records, billing, and wage documentation as they become
available so that we may evaluate the claim. Please also advise of any liens or
third-party funding asserted against a recovery, as those affect the mechanics
of any resolution.

Very truly yours,

Bernadette Kowalczyk
Senior Claims Representative
Sentinel Transport Casualty
(916) 555-0277
""",
    )
)

DOCS.append(
    (
        "2026-02-11 Policy Limits Disclosure - Sentinel Transport Casualty",
        """[SEED - synthetic test record, not a real client document]

SENTINEL TRANSPORT CASUALTY
Commercial Auto Claims
P.O. Box 9910, Rancho Cordova, CA 95741

February 11, 2026

RE:  Claim No.:      STC-2025-644190
     Insured:        Pacific Freight Lines, Inc
     Claimant:       James Whitfield
     Date of loss:   November 2, 2025

Counsel:

In response to your request of January 28, 2026, Sentinel Transport Casualty
provides the following coverage information for the above insured as of the date
of loss:

     Policy number:                    STC-CA-9930177
     Policy period:                    March 1, 2025 to March 1, 2026
     Coverage:                         Commercial Automobile Liability
     Combined single limit:            $1,000,000.00 per accident
     MCS-90 endorsement:               Attached to the policy
     Self-insured retention:           $50,000.00 per occurrence

There is no additional excess or umbrella coverage applicable to this loss known
to the company at this time.

This disclosure is provided for the limited purpose stated above and is not an
admission of coverage or liability.

Very truly yours,

Bernadette Kowalczyk
Senior Claims Representative
Sentinel Transport Casualty
""",
    )
)
