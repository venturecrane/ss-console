"""Every firm-authored format key in drafting-firm.yaml has one named home.

ADR 0083 amendment 2026-11-10: firm format a Word style can carry lives in the
firm's template (item 1); a preference no style can express may stay in the
firm yaml, listed by key (item 4). ``firm.FORMAT_KEY_HOME`` is that list. A new
layout key added without a home fails here, so the yaml cannot grow a style
setting unnoticed.
"""

from __future__ import annotations

from jobs.drafting import firm


def test_every_layout_key_has_a_home() -> None:
    layout = {f"format.layout.{k}" for k in firm.LAYOUT_KEYS}
    homed = {k for k in firm.FORMAT_KEY_HOME if k.startswith("format.layout.")}
    assert layout - homed == set(), f"layout keys with no home: {sorted(layout - homed)}"
    assert homed - layout == set(), f"homes for keys that no longer exist: {sorted(homed - layout)}"


def test_homes_are_the_two_the_adr_names() -> None:
    assert set(firm.FORMAT_KEY_HOME.values()) <= {"template", "firm_yaml"}


def test_font_and_size_belong_in_the_template() -> None:
    assert firm.FORMAT_KEY_HOME["format.font"] == "template"
    assert firm.FORMAT_KEY_HOME["format.size_pt"] == "template"
