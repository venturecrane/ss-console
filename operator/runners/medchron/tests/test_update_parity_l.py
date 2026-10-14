"""Parity fix L: a re-printed copy of delivered pages is never a new exhibit (2026-10-14).

A real update pulled a second print of a hospital admission the delivery had
already filed as an exhibit. The picture pass (``update/pageid``) matched most
of its pages to the delivered ones as NEAR, but ``pageid_io.confirmed`` then
rejected three of them: the EMR re-print carried a new "Generated on" print
date (so the two pages' date sets differed) and its text layer flowed the
columns in another order (so its 8-word shingles barely overlapped). Those
pages were composed again, the copy became a new exhibit and an Exhibit List
line, and one of them produced a duplicate entry. Every test here fails on the
branch head before the fix (b3adda24) and passes after. Every record is
invented."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf

ALREADY = "already read in a delivered exhibit"

_WORDS = [
    "patient", "reports", "intermittent", "discomfort", "lower", "abdomen", "since", "lifting",
    "exam", "shows", "soft", "nontender", "reducible", "bulge", "right", "groin", "plan",
    "elective", "repair", "discussed", "risks", "benefits", "alternatives", "consent", "signed",
    "labs", "reviewed", "within", "normal", "limits", "follow", "clinic", "after", "procedure",
]  # fmt: skip


def _page(i: int, date: str = "11/03/2025", bp: str = "128/82", stamp: str = "12/18/25 7:00 AM") -> list[str]:
    """One invented record page as short lines (a two-column EMR print), with
    its visit date, one clinical value and the print stamp at its foot."""
    lines = [f"Example Regional Hospital note {i}", f"Visit date {date}", f"Blood pressure {bp}"]
    for k in range(24):
        a, b = _WORDS[(i * 5 + k) % len(_WORDS)], _WORDS[(i * 7 + 3 * k + 1) % len(_WORDS)]
        lines.append(f"{a} {b}")
    return [*lines, f"Generated on {stamp}"]


def _reprint(lines: list[str], stamp: str = "9/15/26 10:33 AM") -> list[str]:
    """The same page printed again: the columns flow in another order and the
    print stamp carries the new date."""
    body = lines[:-1]
    return [*body[0::2], *body[1::2], f"Generated on {stamp}"]


def _text_pdf(pages: list[list[str]]) -> bytes:
    with pymupdf.open() as doc:
        for lines in pages:
            page = doc.new_page()
            page.insert_text((60, 60), "\n".join(lines), fontsize=9)
        return doc.tobytes()


def _seed(tmp: Path, delivered: list[list[str]], docs: dict[str, list[list[str]]], ident: dict) -> SimpleNamespace:
    """A slug dir after update_pages: delivered Exhibit 1 (a text layer), the
    new documents (their text files as the pull wrote them) and the picture
    pass's page_identity.json."""
    ud = tmp / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    for d in ("delivered", "raw", "text"):
        (tmp / d).mkdir()
    name = "Exhibit 1 - Example Regional Hospital (Medical Records).pdf"
    pdf = _text_pdf(delivered)
    (tmp / "delivered" / name).write_bytes(pdf)
    plan = {"exhibits": [{"exhibit": 1, "name": name, "sha256": hashlib.sha256(pdf).hexdigest()}], "top_exhibit": 1}
    (ud / "update_plan.json").write_text(json.dumps(plan))
    rows = []
    for rid, pages in docs.items():
        raw = tmp / "raw" / f"{rid}.pdf"
        raw.write_bytes(_text_pdf(pages))
        sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        rows.append({"id": rid, "ok": True, "path": str(raw), "sha256": sha, "ext": ".pdf", "name": f"{rid} records"})
        body = "".join(f"[p.{n}]\n" + "\n".join(lines) + "\n" for n, lines in enumerate(pages, 1))
        (tmp / "text" / f"{rid}.txt").write_text(body)
    (tmp / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (ud / "page_identity.json").write_text(json.dumps({"pages": ident, "extracts": {}}))
    log: list[str] = []
    return SimpleNamespace(
        slug_dir=tmp,
        unit=SimpleNamespace(unit="alpha"),
        job=SimpleNamespace(units=[SimpleNamespace(surname="Example", dob="")]),
        log=log.append,
        logged=log,
    )


def test_a_reprint_with_a_new_print_date_and_reflowed_columns_is_confirmed() -> None:
    from medchron.update import pageid_io as PG
    from medchron.update import select as S

    original = "\n".join(_page(1))
    copy = "\n".join(_reprint(_page(1)))
    assert len(S.shingles(copy) & S.shingles(original)) < 0.8 * len(S.shingles(copy))  # the text rule misses it
    assert PG.confirmed(copy, original)


def test_a_near_picture_that_states_another_date_or_value_is_never_confirmed() -> None:
    from medchron.update import pageid_io as PG

    original = "\n".join(_page(1))
    assert not PG.confirmed("\n".join(_reprint(_page(1, date="11/10/2025"))), original)  # another visit
    assert not PG.confirmed("\n".join(_reprint(_page(1, bp="142/90"))), original)  # another reading


def test_a_reprinted_copy_of_delivered_pages_is_cited_to_the_delivered_exhibit(tmp_path: Path) -> None:
    """The whole re-print is a re-filed copy of Exhibit 1: no new exhibit, no
    page composed, no exhibit PDF filed. A document beside it with one copy
    page and one page that LOOKS like a delivered page but records another
    visit stays an exhibit, takes the next number, and reads that visit."""
    from medchron.stages import update_select as US

    delivered = [_page(i) for i in range(1, 4)]
    reprint = [_reprint(p) for p in delivered]
    other_visit = _reprint(_page(2, date="11/24/2025", bp="136/88"))
    near = {str(n): ["already", "exhibit:1", n, "near"] for n in (1, 2, 3)}
    sr = _seed(
        tmp_path,
        delivered,
        {"reprint": reprint, "mixed": [reprint[0], other_visit]},
        {"reprint": near, "mixed": {"1": near["1"], "2": near["2"]}},
    )
    assert US.run(sr) == 0, sr.logged  # type: ignore[arg-type]
    sel = json.loads((tmp_path / "runs" / "alpha" / "update" / "selection.json").read_text())
    by_id = {r["id"]: r for r in sel["exhibits"]}
    assert set(by_id) == {"mixed"}
    assert by_id["mixed"]["exhibit"] == 2
    assert by_id["mixed"]["read"] == [2]  # the other visit is read, never dropped
    assert by_id["mixed"]["excluded"] == {ALREADY: 1}
    subs = {s["id"]: s for s in sel["subsets"]}
    assert subs["reprint"]["subset_of"]["exhibit"] == 1 and subs["reprint"]["subset_of"]["id"] == ""
    assert subs["reprint"]["matched"] == 3
    filed = [p.name for p in (tmp_path / "out" / "alpha").iterdir()]
    assert not any("reprint" in f for f in filed), filed
    units = json.loads((tmp_path / "units" / "alpha.json").read_text())
    assert [u["id"] for u in units] == ["mixed"]


def test_a_document_with_a_page_left_to_read_or_a_bill_is_never_refiled_whole() -> None:
    from medchron.update import select as S

    by_ex = {1: S.shingles("\n".join(_page(1))), 2: S.shingles("\n".join(_page(5)))}
    pages = {1: "\n".join(_page(5)), 2: ""}
    whole = {"pages": 2, "read": [], "unread": [], "excluded": {ALREADY: 1, S.BLANK: 1}}
    assert S.delivered_home(whole, pages, by_ex, {}) == 2  # by its words: the exhibit that holds the page
    assert S.delivered_home(whole, pages, by_ex, {1: 1}) == 1  # by its picture first
    assert S.delivered_home({**whole, "read": [2]}, pages, by_ex, {}) is None
    assert S.delivered_home({**whole, "excluded": {ALREADY: 1, "a claim form": 1}}, pages, by_ex, {}) is None
    assert S.delivered_home({**whole, "excluded": {S.BLANK: 2}}, pages, by_ex, {}) is None
