---
title: Sample Medical Records and Chronologies
section: product
order: 7
owner: captain
review_every: 90
last_reviewed: "2026-11-10"
summary: Three fictional personal-injury record sets, the sample medical chronology the production pipeline builds from each at no model cost, how to regenerate them, and how a rep shows them to a prospect without overclaiming.
sources:
  - label: The case specs (one per matter)
    href: operator/customers/pilot-smokeball/seed/records/whitfield-pi104.yaml
  - label: The generator and the answer key
    href: operator/runners/jobs/jobs/medchron/sample/pipeline.py
  - label: What the answer key feeds each paid seam
    href: operator/runners/jobs/jobs/medchron/sample/answer_key.py
  - label: The tests that hold it true
    href: operator/runners/jobs/tests/test_sample_records.py
  - label: The uploader for the demo tenant
    href: operator/bin/seed-staging-matter.py
---

## What this is

SMD's own Smokeball demo tenant (`pilot-smokeball`, the fictional firm Brannock & Ferreira
LLP) holds three personal-injury matters with full medical record sets. Every person,
provider, number and address in them is invented, every page carries a `[SEED]` footer, and
nothing is drawn from any client's records.

| Matter | Case | Pages | What it shows |
|---|---|---|---|
| 2026-PI-104 | Whitfield v. Pacific Freight Lines (truck rear-end, three lienholders) | about 1,470 | A long production: emergency chart, primary care, MRI, spine consult and injection, 20 therapy visits, claim forms, EOBs and three lien itemizations |
| 2026-PI-102 | Chen v. Sunrise Plaza (slip and fall, wrist surgery) | about 670 | Emergency care, surgery, orthopedic follow-up, imaging, 24 therapy visits |
| 2026-PI-103 | Ramirez v. Ortiz (a child's dog bite) | about 150 | A small set with a treatment gap |

About 70% of the pages are image-only scans (faxed, a little crooked, with a fax header and
speckle), a few forms are filled in by hand, and about a fifth of the pages arrive twice
because a provider re-sent them. The sets carry deliberate problems a real file has: a
missing ambulance bill, a therapy note and its bill on different dates, one page nobody can
read, another patient's page filed in the wrong chart, a prior injury mentioned in a note,
and a 63-day gap in treatment.

Each matter has a committed case spec in `operator/customers/pilot-smokeball/seed/records/`.
The spec states every document and every page, and for every sentence of the chronology it
states the page that proves it and a phrase printed on that page. The PDFs are built from
the spec; they are never committed.

## The sample chronology

From each record set the build produces the exact package a client receives: the Medical
Chronology and the Medical Billing Worksheet as Word documents, and the exhibits as PDFs, all
written by the production stages of the chronology pipeline. Every citation in it points at a
real page of the generated exhibits, and the tests check each one lands on a page that prints
the fact it supports.

What is real and what is supplied, so nobody overstates it:

- **Real (production code):** text extraction and scan detection, assembly, exhibit
  building and page citations, the Treatment Timeline and Diagnostic Highlights tables, the
  coverage and date-of-service checks, the page strip that takes fax covers and custodian
  certifications out of the exhibits and renumbers every citation, the duplicate-exhibit
  check, the billing chart and worksheet, the Word rendering, and the delivery manifest. The
  exhibits come out as clean as a client delivery.
- **Supplied by the answer key instead of a model:** the reading of each scanned page, the
  reading of each bill, the call on which scanned pages are covers rather than records, and
  the chronology entries themselves. They are written from the case spec in the exact shape
  the model's output takes, so the stages after them cannot tell the difference. No model is
  called and nothing is spent; a stage that tried to call one would stop the build.
- **Not run:** the independent audit and the pre-delivery review, which are model reads of
  the finished package with nothing to supply them from.

The pipeline's own checks show the planted problems: the billing worksheet marks itself
incomplete for the missing ambulance bill, the date-of-service check names the therapy visit
billed a day after its note, the Limitations section names the unreadable page, and the
timeline draws the Ramirez treatment gap.

## How a rep presents it

Show it as **a sample of the format, built from fictional records**. Say plainly: "These are
made-up records for a made-up client. This is the chronology our pipeline produces from them,
in exactly the form a firm receives." Then open a citation and the exhibit page it points at.

Never present it as work delivered to a firm, never say the Operator "read" these records, and
never quote its page counts or turnaround as a client result. The reading of the records was
supplied, not performed, and saying otherwise is the fabricated client-facing content the
no-fabrication policy forbids.

## Regenerating it

From `operator/`, in the jobs runner's environment (the same one CI builds, from
`requirements/jobs.txt` plus `runners/jobs`):

```
python -m jobs.medchron.sample customers/pilot-smokeball/seed/records/*.yaml \
  --out customers/pilot-smokeball/seed/records/out
```

The whole set takes a little over a minute and writes about 70 MB of record PDFs, plus the
exhibits and the job's working files. For each matter,
`out/<slug>/` holds `records/` (the PDFs to upload), `data/<slug>/out/<unit>/` (the
chronology, the worksheet and the exhibits), `answer-key.json`, and `build.json` (pages,
timings, sizes, each stage's verdict). The output folder is gitignored. The same spec always
produces the same record PDFs, byte for byte.

## Rehearsing a chronology change at no cost

The answer key doubles as the rehearsal for any change to the stages after the chronology
entries are composed. Run the build above on the changed code: `build.json` lists every
departure from the key under `answer_key_problems` (a paragraph lost, one cited to the wrong
page, one whose page does not print its fact), and the command exits non-zero if there is any.
The same check runs in CI as `test_sample_records.py`. It does not plug into `jobs rehearse`,
which replays a real matter's saved job; this one builds its own job from the spec.

## Putting it on the demo tenant

`operator/bin/seed-staging-matter.py <slug> --records out/<slug>/records` uploads a matter's
PDFs, refusing a folder that does not hold exactly the spec's set. It runs on the
`pilot-smokeball` seat, where the tenant's token lives, so the PDFs travel to the seat as
files first. Uploading is a separate, deliberate step; building the set touches no tenant.
