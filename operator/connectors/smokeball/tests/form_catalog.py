"""The form catalog this suite's seat customer.yaml carries (``form_letters.forms``).

Since ADR 0100 W4 the catalog is the firm's config, not a list in
``form_letters.py``, so a test that renders a form writes a seat yaml holding
it. ``fixtures/form_catalog.yaml`` is the catalog A&P's toolbar uses, the same
block its customer.yaml carries (``test_form_catalog.py`` holds the two equal).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from smokeball_connector import form_letters as fl

PATH = Path(__file__).parent / "fixtures" / "form_catalog.yaml"
TEXT = PATH.read_text(encoding="utf-8")
CATALOG = fl.parse_forms(yaml.safe_load(TEXT)["forms"])

#: The catalog as lines under a ``form_letters:`` key, to prefix a test's own
#: ``form_letters`` entries: ``"form_letters:\n" + UNDER_FORM_LETTERS + "  signers: ..."``.
UNDER_FORM_LETTERS = "".join(f"  {line}\n" for line in TEXT.splitlines() if line.strip() and not line.startswith("#"))
