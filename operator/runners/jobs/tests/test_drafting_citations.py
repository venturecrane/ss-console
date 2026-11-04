"""The firm's citation policy, the first page's gaps, pleading paper by
default, and the footer title (A&P's standing instruction, 2026-11-04).

"In a mediation brief, cite only direct quotes from a deposition transcript or
from case law; never cite the demand or the medical records, mention the CHP
report without citing it, and cite discovery responses only when quoting them
directly; on the first page leave extra space between the attorney information
and the party represented and between the court name and the caption, and file
the finished brief on pleading paper."
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import docx
import pytest

from drafting_testkit import COURT, SECTIONS, firm_data
from jobs.drafting import citations, finish, firm, format_check, frontpage, pleading, render

POLICY = {
    "mediation_brief": {
        "allow": ["deposition-quote", "legal-authority", "discovery-quote"],
        "forbid": ["demand", "medical", "police-report", "position-paper", "offer", "email"],
    }
}
CORPUS = {
    "Gamma Example Deposition 03-01-26.pdf": "DEPOSITION OF GAMMA EXAMPLE\n12 Q. Did you stop?\n13 A. I never saw the light change at all.",
    "Defendant's Responses to Form Interrogatories.pdf": "RESPONSES TO FORM INTERROGATORIES. Defendant admits the vehicle was owned by Delta Example.",
    "Demand Letter to Insurer 01-10-26.pdf": "We demand the policy limits.",
    "Northfield Physical Therapy Records.pdf": "Patient reports neck pain at 7 of 10.",
    "CHP Traffic Collision Report 555.pdf": "Party 2 ran the red signal.",
}
POL = citations.Policy(frozenset(POLICY["mediation_brief"]["allow"]))


@pytest.mark.parametrize(
    "name, kind",
    [
        ("Gamma Example Deposition 03-01-26.pdf", "deposition"),
        ("Defendant's Responses to Form Interrogatories.pdf", "discovery"),
        ("Plaintiff's Responses to Demand for Production.pdf", "discovery"),
        ("Demand Letter to Insurer 01-10-26.pdf", "demand"),
        ("Northfield Physical Therapy Records.pdf", "medical"),
        ("CHP Traffic Collision Report 555.pdf", "police-report"),
        ("Defense Mediation Position Statement.pdf", "position-paper"),
        ("Re claim.eml", "email"),
        ("scan0001.pdf", "other"),
    ],
)
def test_a_files_kind_is_read_from_the_file(name, kind):
    assert citations.kind_of(name, CORPUS.get(name, "")) == kind


def test_forbidden_cites_are_dropped_and_the_sentences_stay():
    md = (
        'He said "I never saw the light change at all." (Example Dep. 12:12-13). '
        "He had neck pain (FILE: Northfield Physical Therapy Records.pdf, p. 1). "
        "The demand asked for limits (FILE: Demand Letter to Insurer 01-10-26.pdf, p. 1). "
        "The CHP report places fault on defendant (FILE: CHP Traffic Collision Report 555.pdf, p. 1). "
        'Defendant admits "the vehicle was owned by Delta Example" (FILE: Defendant\'s Responses to Form Interrogatories.pdf, p. 1). '
        "Defendant owned the car (FILE: Defendant's Responses to Form Interrogatories.pdf, p. 1). "
        "Gamma testified he did not stop (Example Dep. 12:12-13). "
        "See Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541."
    )
    out, notes = citations.apply(md, POL, CORPUS)
    assert "(Example Dep. 12:12-13). He had neck pain." in out  # quoted testimony keeps its cite
    for gone in ("Northfield", "Demand Letter", "CHP Traffic"):
        assert gone not in out
    assert "The CHP report places fault on defendant." in out  # mentioned, not cited
    assert "Delta Example\" (FILE: Defendant's Responses" in out  # quoted discovery keeps its cite
    assert "Defendant owned the car." in out  # unquoted discovery loses it
    assert "did not stop." in out and "did not stop (Example" not in out  # unquoted testimony loses it
    assert "52 Cal.4th 541" in out  # legal authority untouched
    assert citations.check(out, POL, CORPUS) == []
    assert any("medical" in n for n in notes) and any("demand" in n for n in notes)


def test_a_mixed_cite_keeps_its_allowed_part():
    md = '"I never saw the light change at all." (Example Dep. 12:13; FILE: Demand Letter to Insurer 01-10-26.pdf, p. 1).'
    out, _ = citations.apply(md, POL, CORPUS)
    assert out.endswith('at all." (Example Dep. 12:13).')


def test_a_file_name_with_its_own_parentheses_is_still_one_cite():
    name = "Exhibit 4 - Shriners Children's (Medical Records).pdf"
    md = f"She was admitted (FILE: {name}, p. 3; Example Dep. 12:13). Next."
    out, _ = citations.apply(md, POL, {**CORPUS, name: "Admission note."})
    assert out == "She was admitted. Next."  # the medical part forbidden, the unquoted testimony too


def test_a_quote_not_in_the_cited_file_is_not_a_quotation_of_it():
    md = '"words nobody ever said in this case" (FILE: Defendant\'s Responses to Form Interrogatories.pdf, p. 1).'
    out, _ = citations.apply(md, POL, CORPUS)
    assert "FILE:" not in out


def test_the_render_check_fails_on_a_forbidden_cite_left_in(tmp_path):
    md = COURT + "\n\n".join(f"# {s}\n\nBody text." for s in SECTIONS)
    md = md.replace("Body text.", "Pain (FILE: Northfield Physical Therapy Records.pdf, p. 1).", 1)
    path, _ = render.render(md, "mediation_brief", tmp_path / "b.docx", firm_data()["format"])
    res = format_check.check(path, "mediation_brief", firm_data()["format"], "", CORPUS, "plain", POL)
    assert any(f.startswith("citations:") for f in res.ours), res.fails


def test_the_policy_config_is_closed():
    assert citations.validate(POLICY, firm.CLASSES) == []
    assert citations.validate({"mediation_brief": {"allow": ["medical-records"]}}, firm.CLASSES)
    assert citations.validate({"mediation_brief": {"allow": ["demand"], "forbid": ["demand"]}}, firm.CLASSES)
    assert citations.validate({"brief": {"allow": []}}, firm.CLASSES)


def _fmt(**layout):
    f = copy.deepcopy(firm_data()["format"])
    f["layout"]["mediation_brief"] = {**f["layout"]["mediation_brief"], **layout}
    return f


def _brief():
    return COURT + "\n\n".join(f"# {s}\n\nBody text." for s in SECTIONS) + "\n\n{{ATTORNEY: settlement authority}}"


def test_the_first_page_gaps_are_rendered_and_checked(tmp_path):
    gap = _fmt(front_attorney_gap_lines=2, front_caption_gap_lines=1)
    path, _ = render.render(_brief(), "mediation_brief", tmp_path / "b.docx", gap)
    d = docx.Document(str(path))
    assert frontpage.check(d, gap, "mediation_brief") == []
    texts = [p.text for p in d.paragraphs][:8]
    i = next(i for i, t in enumerate(texts) if t.startswith("Attorneys for"))
    assert texts[i - 2 : i] == ["", ""]
    plain, _ = render.render(_brief(), "mediation_brief", tmp_path / "p.docx", firm_data()["format"])
    fails = frontpage.check(docx.Document(str(plain)), gap, "mediation_brief")
    assert len(fails) == 2, fails


def test_the_firm_can_file_a_class_on_pleading_paper_by_default():
    f = copy.deepcopy(firm_data()["format"])
    f["pleading"] = {"lines": 28, "line_pitch_pt": 24, "margins_in": [0.5, 0.5, 0.69, 1.5]}
    f["pleading_classes"] = ["mediation_brief"]
    run = SimpleNamespace(
        firm=SimpleNamespace(data={"format": f}), job=SimpleNamespace(request_text="Draft the brief.")
    )
    assert finish.Finish.paper(SimpleNamespace(**vars(run), cls="mediation_brief")) == "pleading"
    assert finish.Finish.paper(SimpleNamespace(**vars(run), cls="memo")) == "plain"
    assert not [e for e in firm._format(f) if "pleading_classes" in e]
    del f["pleading"]
    assert [e for e in firm._format(f) if "pleading_classes" in e]


def test_a_briefs_pleading_footer_carries_the_firms_title_never_body_prose(tmp_path):
    md = _brief().replace("Body text.", "Defendant denied the request for admission on liability.", 1)
    path, _ = render.render(md, "mediation_brief", tmp_path / "b.docx", firm_data()["format"])
    assert pleading.title_of(docx.Document(str(path))) == ""  # body prose is never a title
    spec = pleading.Spec(lines=28, pitch_pt=24, margins_in=(0.5, 0.5, 0.69, 1.5), font="Times New Roman", size_pt=12)
    pleading.apply(path, spec, pleading.authored_title(firm_data()["format"], "mediation_brief"))
    footer = [p.text for p in docx.Document(str(path)).sections[0].footer.paragraphs if p.text.strip()]
    assert footer == ["Plaintiff's Mediation Brief"]
