"""Fictional PI record sets and their answer keys, for $0 demos and rehearsals.

WHY. A sales rep needs to show a prospect a medical chronology in the exact
format a client receives, built from records that look like the records a firm
actually holds, without spending a cent on a model and without touching any
client's material. The same record set, because its every fact was authored
with the page it lands on, is the answer key a chronology change is rehearsed
against at $0.

THE PIECES.

* ``spec``: a committed case spec (YAML, one per matter under
  ``operator/customers/pilot-smokeball/seed/records/``) expanded into a page
  plan: every document, every page's text, and every chronology paragraph with
  the page that proves it.
* ``pages``: the templates that write the bulk pages a real production carries
  (nursing notes, flowsheets, claim forms, EOBs, lien itemizations).
* ``pdf``: renders each document as a provider-styled PDF and turns most pages
  into image-only scans (skew, fax header, noise, handwriting, one page nobody
  can read). Seeded, so the same spec gives the same bytes.
* ``answer_key``: what a model WOULD have produced at each paid seam (the page
  transcriptions, the billing transcriptions, the map entries), derived from
  the spec instead of from a model.
* ``pipeline``: seeds those artifacts into a job workdir and runs the
  PRODUCTION $0 stages over them, so the chronology and billing worksheet are
  the deliverables the real renderer writes, citing real exhibit pages.

NOTHING HERE CALLS A MODEL. The stage runs get a client that raises on any
call, so a stage that would have spent stops the run instead.

Every document is fictional and carries the [SEED] marker.
"""
