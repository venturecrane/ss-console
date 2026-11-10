"""Bulk page templates: the pages a real production carries around the few
that a chronology cites.

A firm's records are mostly not the encounter notes. They are nursing notes
and vitals grids, medication administration records, order lists, patient
education handouts, chart printouts dated "as of" a day nobody was seen,
therapy flowsheets, and the fax covers and custodian certifications wrapped
around all of it. A demo set without that bulk shows a reader nothing about
what the pipeline actually reads, so each template here writes plausible,
seeded, uncited bulk. Billing templates live in ``pages_billing``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .model import Page, Provider, us_date


@dataclass
class Ctx:
    doc_id: str
    provider: Provider
    patient: dict[str, Any]
    matter: dict[str, Any]
    rng: random.Random


Template = Callable[[dict[str, Any], Ctx], list[Page]]

_NURSE = ["K. Alvarado, RN", "M. Osei, RN", "T. Lindqvist, RN", "J. Park, RN", "D. Brennan, LVN"]
_NURSING = [
    "Patient resting on stretcher, reports back pain {p}/10. Call light within reach.",
    "Ambulated to restroom with steady gait, no assistance required.",
    "Reassessed pain after medication, now {p}/10. Patient tolerating position changes.",
    "Ice pack applied to lower back per order. Skin intact.",
    "Patient states pain is worse when sitting up. Repositioned with pillow support.",
    "Neuro checks intact. Moves all extremities equally. Denies numbness at this time.",
    "Family member at bedside. Plan of care reviewed with patient.",
    "Patient requests water, tolerating oral fluids without nausea.",
    "Fall precautions in place. Bed in lowest position, side rails up x2.",
    "Awaiting imaging results. Patient updated on expected wait time.",
]
_ORDERS = [
    "Ibuprofen 600 mg PO once",
    "Cyclobenzaprine 10 mg PO once",
    "Acetaminophen 1000 mg PO once",
    "Ice pack to affected area PRN",
    "Fall precautions",
    "Vital signs every 1 hour",
    "Pain reassessment 30 min after analgesia",
    "Discharge when stable per provider",
    "Ambulate with assistance as tolerated",
    "Saline lock, flush every 8 hours",
]
_EDUCATION = [
    "Back strain happens when the muscles or ligaments that support the spine are stretched or torn.",
    "Most back strains improve within a few weeks with rest, gentle movement and time.",
    "Apply ice for 20 minutes at a time, several times a day, for the first two days.",
    "After the first two days, heat may relax tight muscles. Do not sleep on a heating pad.",
    "Avoid heavy lifting, twisting and long periods of sitting until your provider says it is safe.",
    "Take medicines only as directed. Do not drive after taking medicines that make you sleepy.",
    "Return to the emergency department right away for new weakness, numbness in the groin, or loss of bladder control.",
    "Call your doctor if pain is not improving after one week or keeps you from sleeping.",
    "Gentle walking each day helps recovery more than staying in bed.",
    "When you lift, bend at the knees and keep the object close to your body.",
    "Keep all follow-up appointments, even if you are feeling better.",
    "Your discharge medicines are listed on the medication sheet given to you today.",
]
_HEP = [
    "Pelvic tilt: lie on your back with knees bent. Flatten your low back into the floor. Hold 5 seconds. 10 reps.",
    "Bridge: lie on your back, knees bent. Lift your hips. Hold 3 seconds. 10 reps, 2 sets.",
    "Cat and camel: on hands and knees, round then gently arch your back. 10 reps.",
    "Sciatic nerve glide: seated, straighten the knee while lifting the toes. 10 reps each side.",
    "Bird dog: on hands and knees, extend the opposite arm and leg. Hold 5 seconds. 10 reps.",
    "Prone press-up: lie on your stomach and press up on your hands, keeping hips down. 10 reps.",
    "Walking program: walk 15 to 20 minutes daily on level ground.",
    "Stop any exercise that increases pain down the leg and tell your therapist at the next visit.",
]
_EXERCISES = [
    "Nustep warm-up",
    "Pelvic tilt",
    "Bridge",
    "Bird dog",
    "Dead bug",
    "Sciatic nerve glide",
    "Hamstring stretch",
    "Piriformis stretch",
    "Side plank (modified)",
    "Squat to chair",
    "Lift training, box to waist",
    "Farmer carry",
    "Prone press-up",
    "Wall slides",
    "Grip strengthening putty",
    "Wrist curls",
    "Theraband row",
    "Single-leg balance",
]


def _clock(start: str, rng: random.Random, n: int) -> list[str]:
    t = datetime.strptime(start, "%H:%M")
    out = []
    for _ in range(n):
        t += timedelta(minutes=rng.choice((8, 12, 15, 20, 25, 30)))
        out.append(t.strftime("%H:%M"))
    return out


def _many(p: dict[str, Any], cx: Ctx, kind: str, title: str, body: Callable[[int], list[str]]) -> list[Page]:
    return [Page(cx.doc_id, kind, title, body(i), date=str(p.get("date") or "")) for i in range(int(p.get("count", 1)))]


def nursing_notes(p: dict[str, Any], cx: Ctx) -> list[Page]:
    start = str(p.get("start", "17:45"))

    def body(i: int) -> list[str]:
        times = _clock(start, cx.rng, 6)
        out = [f"Date: {us_date(str(p['date']))}", ""]
        for t in times:
            text = cx.rng.choice(_NURSING).format(p=cx.rng.randint(3, 7))
            out += [f"{t}  {text}", f"       {cx.rng.choice(_NURSE)}", ""]
        return out

    return _many(p, cx, "nursing_notes", "Nursing Notes", body)


def vitals(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        r = cx.rng
        out = [f"Date: {us_date(str(p['date']))}", "", "Time    BP        HR   RR   Temp    SpO2   Pain"]
        for t in _clock(str(p.get("start", "17:40")), r, 16):
            bp = f"{r.randint(118, 142)}/{r.randint(72, 90)}"
            row = f"{t}   {bp:<9} {r.randint(68, 96):<4} {r.randint(14, 19):<4} {r.uniform(97.8, 99.0):.1f}F  "
            out.append(row + f"{r.randint(96, 100)}%    {r.randint(3, 7)}/10")
        return out

    return _many(p, cx, "vitals", "Vital Signs Flowsheet", body)


def mar(p: dict[str, Any], cx: Ctx) -> list[Page]:
    meds = [str(m) for m in p.get("meds") or _ORDERS[:3]]

    def body(i: int) -> list[str]:
        out = [f"Date: {us_date(str(p['date']))}", "", "Medication / Dose / Route        Given   By"]
        for t, m in zip(_clock(str(p.get("start", "18:10")), cx.rng, len(meds)), meds):
            out += [f"{m}", f"                                  {t}   {cx.rng.choice(_NURSE)}", ""]
        return out + ["Allergies: No known drug allergies"]

    return _many(p, cx, "mar", "Medication Administration Record", body)


def orders(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        picks = cx.rng.sample(_ORDERS, 7)
        out = [f"Date: {us_date(str(p['date']))}", "", "Order                                      Status       Time"]
        for t, o in zip(_clock(str(p.get("start", "17:50")), cx.rng, 7), picks):
            out.append(f"{o:<42} {cx.rng.choice(('Completed', 'Completed', 'Discontinued')):<12} {t}")
        return out

    return _many(p, cx, "orders", "Orders", body)


def education(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        paras = cx.rng.sample(_EDUCATION, 6)
        out = [str(p.get("topic", "Caring for Your Back")), ""]
        for para in paras:
            out += [para, ""]
        return out

    return _many(p, cx, "education", "Patient Education", body)


def health_summary(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        return [
            f"Health summary as of {us_date(str(p['as_of']))}. Printed for release of information.",
            "",
            "Problem list:",
            *[f"  {x}" for x in p.get("problems") or ["No active problems recorded"]],
            "",
            "Medications:",
            *[f"  {x}" for x in p.get("meds") or ["None recorded"]],
            "",
            "Allergies: No known drug allergies",
            "Immunizations: Influenza (seasonal); Tdap",
            "",
            "This printout is not a record of an encounter.",
        ]

    return _many(p, cx, "health_summary", "Health Summary", body)


def pt_flowsheet(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        out = [
            f"Date of service: {us_date(str(p['date']))}",
            "",
            "Exercise                      Sets x Reps   Load      Notes",
        ]
        for ex in cx.rng.sample(_EXERCISES, 12):
            load = cx.rng.choice(("body wt", "red band", "green band", "5 lb", "10 lb", "15 lb", "level 4"))
            note = cx.rng.choice(("good form", "cues for posture", "no increase in symptoms", "tolerated well", ""))
            out.append(f"{ex:<30}{cx.rng.choice(('2 x 10', '3 x 10', '2 x 15', '3 x 12')):<14}{load:<10}{note}")
        return out + ["", f"Therapist: {p.get('signer', '')}"]

    return _many(p, cx, "pt_flowsheet", "Therapy Flowsheet", body)


def hep(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        out = ["Your home exercise program. Do these once or twice a day.", ""]
        for line in cx.rng.sample(_HEP, 6):
            out += [line, ""]
        return out

    return _many(p, cx, "hep", "Home Exercise Program", body)


def pharmacy_log(p: dict[str, Any], cx: Ctx) -> list[Page]:
    fills = [[str(x) for x in f] for f in p.get("fills") or []]
    per = 14
    pages = []
    for k in range(0, max(len(fills), 1), per):
        rows = ["Fill date    Drug / strength                 Qty   Days   Prescriber"]
        rows += [f"{us_date(f[0])}   {f[1]:<32}{f[2]:<6}{f[3]:<7}{f[4]}" for f in fills[k : k + per]]
        pages.append(Page(cx.doc_id, "pharmacy_log", "Pharmacy Dispensing History", rows))
    return pages


def blank(p: dict[str, Any], cx: Ctx) -> list[Page]:
    return _many(p, cx, "blank", "", lambda i: ["", "", "This page intentionally left blank."])


def access_log(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        out = ["Release of information log", "", "Date        Requester                         Purpose"]
        day = date.fromisoformat(str(p["date"]))
        for _ in range(10):
            day += timedelta(days=cx.rng.randint(0, 3))
            who = cx.rng.choice(("Patient portal", "Attorney request", "Payer audit", "Continuity of care"))
            out.append(f"{us_date(day.isoformat())}  {who:<34}{cx.rng.choice(('Copy', 'View', 'Copy'))}")
        return out

    return _many(p, cx, "access_log", "Disclosure Log", body)


def fax_cover(p: dict[str, Any], cx: Ctx) -> list[Page]:
    m = cx.matter
    lines = [
        "FACSIMILE TRANSMITTAL",
        "",
        f"To:      {m['firm']}",
        f"Fax:     {m['firm_fax']}",
        f"From:    {cx.provider.facility}, Health Information Management",
        f"Fax:     {cx.provider.fax}",
        f"Date:    {us_date(str(p.get('date', '')))}",
        "Pages:   {PAGES} including this cover",
        "",
        f"Re:      {cx.patient['name']}, DOB {cx.patient['dob']}",
        f"         {p.get('re', 'Medical records per your request')}",
        "",
        "CONFIDENTIALITY NOTICE: this transmission contains protected health information.",
        "If you received it in error, notify the sender and destroy all copies.",
    ]
    return [Page(cx.doc_id, "fax_cover", "", lines)]


def custodian_cert(p: dict[str, Any], cx: Ctx) -> list[Page]:
    lines = [
        "CERTIFICATION OF CUSTODIAN OF RECORDS",
        "",
        f"I, {cx.provider.custodian}, am the duly authorized custodian of the records of",
        f"{cx.provider.facility}. The accompanying {{PAGES}} pages are true copies of the records",
        f"of {cx.patient['name']}, DOB {cx.patient['dob']}, for dates of service",
        f"{p.get('span', '')}, prepared in the ordinary course of business at or near the time of",
        "the acts recorded.",
        "",
        f"Executed {us_date(str(p.get('date', '')))}.",
        "",
        cx.provider.custodian,
        "Custodian of Records",
    ]
    return [Page(cx.doc_id, "custodian_cert", "", lines, scan=True, hand=[10])]


def intake_form(p: dict[str, Any], cx: Ctx) -> list[Page]:
    """A printed form filled in by hand: the labels are type, the answers ink."""
    lines = [str(p.get("heading", "PATIENT INTAKE FORM")), ""]
    hand = []
    for label, answer in (p.get("fields") or {}).items():
        lines.append(f"{label}:")
        hand.append(len(lines))
        lines += [f"    {answer}", ""]
    return [Page(cx.doc_id, "intake_form", "", lines, date=str(p.get("date") or ""), scan=True, hand=hand)]


_LABS = [
    ("Sodium", "mmol/L", 136, 145), ("Potassium", "mmol/L", 3.5, 5.1), ("Chloride", "mmol/L", 98, 107),
    ("CO2", "mmol/L", 22, 29), ("BUN", "mg/dL", 7, 20), ("Creatinine", "mg/dL", 0.7, 1.2),
    ("Glucose", "mg/dL", 70, 99), ("Calcium", "mg/dL", 8.6, 10.3), ("WBC", "K/uL", 4.0, 11.0),
    ("Hemoglobin", "g/dL", 13.5, 17.5), ("Platelets", "K/uL", 150, 400), ("ALT", "U/L", 7, 56),
]  # fmt: skip


def lab_results(p: dict[str, Any], cx: Ctx) -> list[Page]:
    """A cumulative results printout: dated values, no encounter."""

    def body(i: int) -> list[str]:
        out = [f"Cumulative results as of {us_date(str(p['as_of']))}. Printed for release of information.", ""]
        out.append("Test           Result    Units     Reference     Collected")
        for name, unit, lo, hi in cx.rng.sample(_LABS, 9):
            val = cx.rng.uniform(lo, hi)
            shown = f"{val:.1f}" if hi < 20 else f"{val:.0f}"
            out.append(f"{name:<15}{shown:<10}{unit:<10}{lo}-{hi:<10}{cx.rng.choice(p['dates'])}")
        return out + ["", "This printout is not a record of an encounter."]

    return _many(p, cx, "lab_results", "Results Review", body)


def key_images(p: dict[str, Any], cx: Ctx) -> list[Page]:
    def body(i: int) -> list[str]:
        series = cx.rng.choice(("Sagittal T2", "Sagittal T1", "Axial T2", "Sagittal STIR"))
        return [f"Study date: {us_date(str(p['date']))}", f"Key image page {i + 1}", "",
                f"Series {cx.rng.randint(2, 7)}   {series}   Images {cx.rng.randint(1, 9)} to {cx.rng.randint(10, 18)}",
                "", "[thumbnail images not reproduced in this release]"]  # fmt: skip

    return _many(p, cx, "key_images", "Key Images", body)


TEMPLATES: dict[str, Template] = {
    "lab_results": lab_results,
    "key_images": key_images,
    "nursing_notes": nursing_notes,
    "vitals": vitals,
    "mar": mar,
    "orders": orders,
    "education": education,
    "health_summary": health_summary,
    "pt_flowsheet": pt_flowsheet,
    "hep": hep,
    "pharmacy_log": pharmacy_log,
    "blank": blank,
    "access_log": access_log,
    "fax_cover": fax_cover,
    "custodian_cert": custodian_cert,
    "intake_form": intake_form,
}
