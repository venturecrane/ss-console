"""A firm's AUTHORED drafting inputs against this runner, end to end.

The firm inputs live in the private engagements repo and never in this public
one, so CI cannot read them and this suite SKIPS there. Run it against an
authored directory before a stage or a reprovision:

    SMD_DRAFTING_INPUTS_FIXTURE=<engagements>/operator/customers/<slug>/drafting \\
      python -m pytest -c pyproject.toml tests/test_drafting_authored_inputs.py

It proves the contract the two repos share and that no fixture of ours can
stand in for: the loader accepts the authored yaml and its pins; the authored
proof of service and declaration, appended by the job, render and pass the
authored ``format`` block; the authored eleven mediation sections pass the
section-order check. A mismatch between the authored files and the runner
fails here, not on a seat. The bodies below are invented; only the firm's
fixed text (attachments, format rules) is read.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from medchron.drafting import firm as firm_mod, format_check, render

ROOT = os.environ.get("SMD_DRAFTING_INPUTS_FIXTURE", "")
pytestmark = pytest.mark.skipif(not ROOT, reason="set SMD_DRAFTING_INPUTS_FIXTURE to an authored drafting inputs dir")


def _firm() -> firm_mod.DraftingFirm:
    return firm_mod.load(ROOT)


def _set(n: int) -> str:
    items = "\n\n".join(
        f"SPECIAL INTERROGATORY NO. {i}\n\nIDENTIFY each witness to the INCIDENT." for i in range(1, n + 1)
    )
    return (
        "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | GAMMA EXAMPLE |\n| RESPONDING PARTY: | DELTA EXAMPLE |\n\n"
        f'# DEFINITIONS\n\n1. "INCIDENT" means the collision.\n\n# SPECIAL INTERROGATORIES\n\n{items}\n\nDated: x\n'
    )


def _render_check(tmp_path: Path, md: str, cls: str, digest: str = "") -> format_check.Result:
    firm = _firm()
    doc, _ = render.attach(md, cls, firm, digest)
    path, _ = render.render(doc, cls, tmp_path / f"{cls}.docx", firm.data["format"])
    return format_check.check(path, cls, firm.data["format"], digest)


def test_the_authored_inputs_load():
    firm = _firm()
    assert set(firm.data["classes"]) == set(firm_mod.CLASSES)


@pytest.mark.parametrize("n", [3, 36])
def test_a_discovery_set_with_the_authored_attachments_passes_the_authored_format(tmp_path, n):
    res = _render_check(tmp_path, _set(n), "discovery_set")
    assert res.ok, res.fails
    doc, _ = render.attach(_set(n), "discovery_set", _firm(), "")
    tail = doc[doc.index("SPECIAL INTERROGATORY NO. %d" % n) :]
    assert ("DECLARATION FOR ADDITIONAL DISCOVERY" in tail) is (n > 35)
    assert "PROOF OF SERVICE" in tail and "| at service}}" in tail
    if n > 35:  # the code-filled slots: the counts and both parties from the set's own block
        assert f"a total of {n} specially prepared" in tail or f"total of {n}" in tail
        assert "party, as captioned" not in tail.split("PROOF OF SERVICE")[0]


def test_responses_with_the_authored_proof_of_service_pass(tmp_path):
    md = (
        "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | DELTA EXAMPLE |\n| RESPONDING PARTY: | GAMMA EXAMPLE |\n\n"
        "RESPONSE TO SPECIAL INTERROGATORY NO. 1\n\n{{CLIENT: the witnesses}}\n\nVERIFICATION\n\nI declare.\n"
    )
    res = _render_check(tmp_path, md, "discovery_response")
    assert res.ok, res.fails


def test_a_brief_in_the_authored_section_order_passes(tmp_path):
    sections = _firm().data["format"]["mediation_brief_sections"]
    from drafting_testkit import COURT

    body = COURT + "\n\n".join(f"# {s}\n\nText." for s in sections)
    body = body.replace("CASE VALUE\n\nText.", "CASE VALUE\n\n{{ATTORNEY: settlement authority}}\n\nText.")
    res = _render_check(tmp_path, body, "mediation_brief")
    assert res.ok, res.fails


@pytest.mark.parametrize("cls", ["memo", "depo_outline"])
def test_memo_and_outline_pass(tmp_path, cls):
    assert _render_check(tmp_path, "# I. QUESTION\n\nText.\n\n1. A topic.", cls).ok


@pytest.mark.parametrize("cls", ["mediation_brief", "memo", "depo_outline"])
def test_the_authored_skeleton_itself_renders_and_passes_the_authored_format(tmp_path, cls):
    """The skeleton as a drafter's output would carry it (comments gone,
    every slot still a marker): structure, headings, caption and footer pass."""
    md = render.COMMENT.sub("", _firm().skeleton(cls))
    res = _render_check(tmp_path, md, cls)
    assert res.ok, res.fails
