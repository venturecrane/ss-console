"""Parity M1 (2026-10-14): a later report on a practice's template is read.

The delivered exhibits held a practice's earlier follow-up evaluations. Its
later follow-up, filed as a new document, shares that template, so its exam,
diagnoses, plan, causation opinion and signature pages were EXCLUDED as
"already read in a delivered exhibit": the rule pooled shingle coverage
across every delivered page. Compose never saw them, and the entry lost its
exam, diagnoses, plan, causation opinion and its provider's name.

A page is already read only when ONE delivered page covers it and states
every word and clinical value it states. A re-printed copy that differs only
in its print date is still excluded. Synthetic text only.
"""

from __future__ import annotations

from pathlib import Path

from medchron.update import select as S

EXAM = (
    "OBJECTIVE FINDINGS. Examination of the lumbar spine reveals tenderness from the third to the fifth level "
    "bilaterally with limited range of motion. Straight leg raising is positive on the right side. Deep tendon "
    "reflexes are diminished at the right knee and ankle. Motor strength is reduced in the right lower extremity. "
    "DIAGNOSTIC IMPRESSIONS. Lumbar radiculopathy. Lumbar disc bulges at multiple levels. Lumbar spine strain. "
    "Sacroiliac joint pain. TREATMENT PLAN. Lumbar epidural steroid injection at one level. Continue home exercise "
    "and medications as needed. Follow up in the clinic after the procedure for reassessment of the symptoms."
)
PLAN_A = (
    "TREATMENT PLAN CONTINUED. The patient was instructed in a home exercise program and the use of ice and heat. "
    "Medications were reviewed with the patient and no changes were made at this visit to the current regimen. "
    "The patient was advised to return to the clinic for follow up or sooner if the symptoms worsen at any time."
)
PLAN_B = (
    "WORK STATUS. The patient may continue modified duty with no lifting over a stated weight and no repetitive "
    "bending or twisting at the waist. The restrictions remain in place until the next scheduled reassessment of "
    "the patient in this clinic, when the work status will be reviewed again with the patient and the employer."
)
# The later report's causation opinion and signer: words no delivered page
# states. Everything else on its pages is the template the delivery read.
OPINION = (
    " Within reasonable medical probability the symptoms described herein are causally related to the subject"
    " collision. Signed by Example Signer, M.D."
)
#: One page that the delivered pages cover only when POOLED (exam + plan).
POOLED = EXAM + " " + PLAN_A + OPINION
#: One page a single delivered page covers past COVERED_FRAC, but whose
#: opinion and signer that page never states.
COVERED = EXAM + OPINION
PRINTED_A = "Printed on 01/02/2026\n" + EXAM
PRINTED_B = "Printed on 09/15/2026\n" + EXAM


def _pdf(tmp_path: Path, pages: list[str]) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_textbox(pymupdf.Rect(36, 36, 576, 756), text, fontsize=9)
    out = tmp_path / "delivered.pdf"
    doc.save(str(out))
    doc.close()
    return out


def test_a_later_report_pooled_across_delivered_pages_is_read(tmp_path: Path) -> None:
    seen = S.seen_shingles([_pdf(tmp_path, [EXAM, PLAN_A, PLAN_B])])
    sh = S.shingles(POOLED)
    assert len(sh & S.shingles(EXAM)) < S.COVERED_FRAC * len(sh)  # no ONE delivered page covers it
    assert S.classify_new({1: POOLED}, 1, seen)[1][0] == S.READ


def test_a_template_page_with_a_causation_opinion_and_signer_is_read(tmp_path: Path) -> None:
    seen = S.seen_shingles([_pdf(tmp_path, [EXAM, PLAN_A])])
    sh = S.shingles(COVERED)
    assert len(sh & S.shingles(EXAM)) >= S.COVERED_FRAC * len(sh)  # the template page covers it by shingles
    assert S.classify_new({1: COVERED}, 1, seen)[1][0] == S.READ


def test_a_reprinted_copy_differing_only_in_its_print_date_is_still_excluded(tmp_path: Path) -> None:
    seen = S.seen_shingles([_pdf(tmp_path, [PRINTED_A, PLAN_A])])
    assert S.classify_new({1: PRINTED_B}, 1, seen)[1] == (S.EXCLUDED, S.ALREADY)


def test_a_new_clinical_value_on_a_covered_page_is_read(tmp_path: Path) -> None:
    seen = S.seen_shingles([_pdf(tmp_path, [EXAM])])
    assert S.classify_new({1: EXAM + " Blood pressure 150/95."}, 1, seen)[1][0] == S.READ


def test_the_compose_prompt_names_the_signer_from_any_page_and_flags_a_missing_one() -> None:
    """Parity M2: a report's signer is often on its last page. The provider is
    that signer; when no page names one, the placeholder is one the exhibit
    grouping already reads as "not a name" (``stages/group.PLACEHOLDER``)."""
    from medchron.stages.group import PLACEHOLDER

    text = (Path(__file__).parents[1] / "medchron" / "prompts" / "map-system.md").read_text()
    assert "Read every page of the file for it" in text and "never invent or infer a name" in text
    assert PLACEHOLDER.search("(provider not stated in the record)")
    assert "(provider not stated in the record)" in text
