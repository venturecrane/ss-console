"""An update sets a page aside as a repeat only when it IS the same page
(2026-11-21). On three real delivered updates, of 387 non-identical pages
the update set aside as repeats, three readers unanimously ruled 21
DIFFERENT from the page they were matched to (18 by a near picture whose
words "confirmed" it, 3 by a text copy), and at least 10 of those facts were
missing from the delivered documents. On 29 code-drawn pairs of different
documents on one template, the lane excluded 29 of 29.

The rule now: a page is a repeat only when its picture is pixel-equal
(``update/pageid`` EXACT) or its text says EXACTLY what the covering page
says, export and print stamps and edge fax lines aside
(``select.same_text``). Everything else is read. Each test names whether it
fails on the code before the fix or pins behaviour that must not change.
Every name and value is invented."""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron import config as config_mod, job as job_mod
from jobs.medchron.update import pageid as PI, pageid_io as PG, select as S
from jobs.stages import update_select as US
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat, make_pdf

VISIT = (
    "Example Physical Therapy daily note\n"
    "Patient: Alpha Example\n"
    "Date of service: {dos}\n"
    "Subjective: neck pain after the collision, improving with therapy at home.\n"
    "Objective: cervical rotation limited to the left, paraspinal tenderness on palpation.\n"
    "Blood pressure 128/82. Pulse 74.\n"
    "Assessment: cervical strain, improving. Plan: continue therapy twice weekly."
)


# ---- 1. one template, another visit -------------------------------------------------------


def test_a_later_visit_on_the_same_template_whose_words_are_a_subset_is_read() -> None:
    """FAILS before the fix. The delivered exhibit holds the 04/01 visit and
    names the 04/08 one ("Next visit"); the new page is the 04/08 visit on the
    same template, its words and values a subset of the 04/01 page. The
    shingle rule set it aside as already read, and its date of service never
    reached the update."""
    delivered = VISIT.format(dos="04/01/2025") + "\nNext visit: 04/08/2025."
    new = VISIT.format(dos="04/08/2025").replace(" Pulse 74.", "")
    seen = S.Delivered.of_docs([[delivered]], ["exhibit:3"])
    assert S.classify_new({1: new}, 1, seen)[1] == (S.READ, "new document")
    earlier = S.Earlier()
    S.classify_new({1: delivered}, 1, S.Delivered(), earlier, doc_id="first")
    assert S.classify_new({1: new}, 1, S.Delivered(), earlier, doc_id="second")[1] == (S.READ, "new document")


# ---- 2. the same text, other furniture ----------------------------------------------------


def test_an_exact_text_copy_that_differs_only_in_its_furniture_is_excluded() -> None:
    """PINS behaviour that must not change: a re-filed copy whose fax header,
    export stamp and page count differ says exactly what the delivered page
    says, so it is already read, and its cover is recorded."""
    page = VISIT.format(dos="04/01/2025")
    delivered = f"FAX 555-0101 07/01/2025 10:02 PAGE 3 OF 9\n{page}\nPage 1 of 4"
    copy = f"Exported 11/09/2025 by records staff\nFAX 555-0199 11/09/2025 08:44 PAGE 7 OF 12\n{page}\nPage 2 of 6"
    seen = S.Delivered.of_docs([[delivered]], ["exhibit:3"])
    covers: dict[int, S.Cover] = {}
    assert S.classify_new({1: copy}, 1, seen, covers_out=covers)[1] == (S.EXCLUDED, S.ALREADY)
    assert covers[1] == S.Cover(S.DELIVERED_SRC, "exhibit:3", 1, S.BY_TEXT)
    # the same copy filed in a second new document is a repeat of the first
    earlier = S.Earlier()
    S.classify_new({1: delivered}, 1, S.Delivered(), earlier, doc_id="first")
    assert S.classify_new({1: copy}, 1, S.Delivered(), earlier, doc_id="second")[1] == (S.EXCLUDED, S.OVERLAP)


def test_a_body_line_with_a_furniture_word_is_what_the_page_says() -> None:
    """FAILS before the fix: every line naming "received" or "sent" was
    dropped as furniture, so two pages giving another dose compared equal."""
    a = VISIT.format(dos="04/01/2025").replace(
        "Blood pressure", "Received ketorolac 30 mg IM in the clinic.\nBlood pressure"
    )
    b = a.replace("30 mg", "60 mg")
    seen = S.Delivered.of_docs([[a]], ["exhibit:3"])
    assert S.classify_new({1: b}, 1, seen)[1] == (S.READ, "new document")
    assert S.classify_new({1: a}, 1, seen)[1] == (S.EXCLUDED, S.ALREADY)


# ---- 3 and 4. by picture, through the stage's own classification -------------------------


def _stage(job_dir: Path, firm_config_path: Path, data_root: Path) -> tuple[StageRun, Path]:
    job = job_mod.load(job_dir)
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True)
    sr = StageRun(job, config_mod.load(str(firm_config_path)), job.units[0], sd, {}, lambda _m: None,
                  lambda: FakeSeat([], [], {}))  # fmt: skip
    return sr, sd


def _doc(sd: Path, rid: str, pages: list[str], text: list[str] | None = None) -> dict:
    path = sd / f"{rid}.pdf"
    path.write_bytes(make_pdf(pages))
    body = text if text is not None else pages
    (sd / "text" / f"{rid}.txt").write_text("".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(body, 1)), "utf-8")
    return {"id": rid, "name": f"Example {rid} records", "ext": ".pdf", "path": str(path)}


def test_a_near_picture_whose_words_confirm_it_is_read(job_dir, firm_config_path, data_root, tmp_path) -> None:
    """FAILS before the fix. The picture record pairs the new page with a
    delivered page by NEAR picture, and the two transcriptions state the same
    dates and values and most of the same words, so the page was set aside.
    A near picture cannot tell a re-encoded copy from the same form saying
    another thing: the page is read."""
    sr, sd = _stage(job_dir, firm_config_path, data_root)
    delivered = VISIT.format(dos="04/01/2025")
    new = delivered.replace("improving with therapy at home", "worse after lifting at work")
    row = _doc(sd, "new", [new])
    ud = tmp_path / "update"
    ud.mkdir()
    (ud / PG.NAME).write_text(json.dumps({"pages": {"new": {"1": [PI.ALREADY, "exhibit:3", 1, "near"]}}}), "utf-8")
    ident = PG.load(ud)
    assert ident.pages["new"][1] == PG.Page(S.ALREADY, "exhibit:3", 1, False)
    seen = S.Delivered.of_docs([[delivered]], ["exhibit:3"])
    got = US._classify(sr, [row], seen, ident, {})
    assert got["new"].decisions[1] == (S.READ, "new document")
    assert got["new"].covers == {}


def test_a_pixel_equal_page_is_excluded(job_dir, firm_config_path, data_root, tmp_path) -> None:
    """PINS behaviour that must not change: a page whose picture is pixel-equal
    to a page of a document read before it (the real ``pageid.identify`` on
    real rendered PDFs) was never transcribed and is a repeat, its cover the
    page it repeats."""
    sr, sd = _stage(job_dir, firm_config_path, data_root)
    whole = [VISIT.format(dos="04/01/2025"), VISIT.format(dos="04/08/2025")]
    big = _doc(sd, "big", whole)
    other = "Example Imaging Center\nMRI cervical spine 05/12/2025\nImpression: mild disc bulge at C5-C6."
    small = _doc(sd, "small", [whole[1], other], [PI.SKIPPED, other])  # one page of its own: not an extract
    ident = PI.identify([], [("big", PI.signatures(Path(big["path"]))), ("small", PI.signatures(Path(small["path"])))])
    ud = tmp_path / "update"
    ud.mkdir()
    (ud / PG.NAME).write_text(json.dumps(PI.to_json(ident)), "utf-8")
    loaded = PG.load(ud)
    assert loaded.pages["small"][1].exact
    got = US._classify(sr, [big, small], S.Delivered(), loaded, {})
    assert got["small"].decisions[1] == (S.EXCLUDED, S.OVERLAP)
    assert got["small"].covers[1] == S.Cover(S.EARLIER_SRC, "big", 2, S.BY_PICTURE)
    assert got["big"].decisions == {1: (S.READ, "new document"), 2: (S.READ, "new document")}
    assert got["small"].decisions[2] == (S.READ, "new document")
