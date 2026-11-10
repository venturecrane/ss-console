"""No seat logs at DEBUG: at that level the model client logs a person's words.

WHY (ss-console #3946). The guide-seat rehearsal's canary inventory captured
the run's log records: at INFO no record carried a person's marker string; at
DEBUG the Anthropic client (``anthropic._base_client``) logged whole request
bodies, so every email a person sent, and every note they kept, would land in
the seat's log stream, which is kept by decision and never purged. The seat's
level is set in two places: ``HERMES_LOG_LEVEL`` in the Fly template, and
``logging.level`` in each profile's ``config.yaml``, which Hermes' own
``setup_logging`` reads (``hermes_logging.py``) and which translate does not
author, so it defaults to INFO. This pins both, and every authored seat's
``logging.level``, as not DEBUG.

FALSIFIER: set ``HERMES_LOG_LEVEL = "debug"`` in the template, or author
``logging: {level: debug}`` on any seat, and this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

OPERATOR = Path(__file__).resolve().parents[2]
CUSTOMERS = sorted(
    p for p in (OPERATOR / "customers").glob("*/customer.yaml") if not p.parent.name.startswith("_")
)


def test_the_fly_template_sets_info() -> None:
    template = (OPERATOR / "templates" / "fly.toml.template").read_text(encoding="utf-8")
    levels = re.findall(r'^\s*HERMES_LOG_LEVEL\s*=\s*"([^"]+)"', template, re.MULTILINE)
    assert levels == ["info"], levels


@pytest.mark.parametrize("seat", CUSTOMERS, ids=lambda p: p.parent.name)
def test_no_authored_seat_asks_for_debug(seat: Path) -> None:
    doc = yaml.safe_load(seat.read_text(encoding="utf-8"))
    level = ((doc.get("logging") or {}).get("level") or "info") if isinstance(doc, dict) else "info"
    assert str(level).lower() != "debug", f"{seat.parent.name} authors logging.level: debug"


def test_translate_never_authors_a_logging_level() -> None:
    """Hermes defaults to INFO when a profile names no ``logging.level``; the
    translator writes no ``logging`` block for any seat."""
    source = (OPERATOR / "hermes-smd-overlay" / "bootstrap" / "translate.py").read_text()
    assert not re.search(r"""["']logging["']\s*[\]:]""", source)


def test_the_guide_seats_translated_profile_logs_at_info(tmp_path: Path) -> None:
    from bootstrap.translate import translate_customer_yaml

    seat = next(p for p in CUSTOMERS if p.parent.name == "smd-guide")
    slugs = translate_customer_yaml(
        str(seat),
        str(tmp_path / "home"),
        skills_dir=str(OPERATOR / "skills"),
        roles_dir=str(OPERATOR / "roles"),
    )
    for slug in slugs:
        config = yaml.safe_load((tmp_path / "home" / "profiles" / slug / "config.yaml").read_text())
        level = ((config or {}).get("logging") or {}).get("level") or "INFO"
        assert str(level).upper() != "DEBUG", f"{seat.parent.name}/{slug} logs at DEBUG"
