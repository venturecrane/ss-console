"""Judicial Council form interrogatories, as data and code.

The official form (DISC-001, Form Interrogatories, General) is a public court
form. Its text, its lettered subparts and the position of every checkbox are
read by code from the Judicial Council's own PDF by the build tool
``tools/forms/build_catalog.py`` (outside the package: neither the tool nor
the PDF ships to a seat) and committed as data (``disc001-rev-2024-01-01.json``),
never typed from memory. The modules here are pure code around that data:

* ``catalog``: the official text per interrogatory number, the definitions
  and the instructions to the answering party;
* ``checked``: a checked list ("2.1-2.13, 6.1-6.7") parsed against the form;
* ``checkbox``: reading the checked boxes off rendered page images, in code
  (``jobs/matter/pdf.py`` opens and renders the pages);
* ``reader``: the model readers' instruction, the strict parser of a reply,
  and the reconciliation of two readers with the pixel read;
* ``answers``: the shape a model returns for one section, and its validation;
* ``render_body``: the response body laid out by code from the answers;
* ``checks``: the deterministic checks over a rendered body before filing.

Nothing here names a firm, a client or a matter, and nothing here calls a
model.
"""
