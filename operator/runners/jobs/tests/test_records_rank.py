"""The record's candidate files, ranked by its words (records/rank.py), from
the shapes measured on the A&P matter's 100 PDFs on 2026-11-03. Names are
invented; the structure is the seat's."""

from __future__ import annotations

from typing import Any

from jobs.records import rank


def _row(n: int, name: str, size: int = 1000, ext: str = ".pdf") -> dict[str, Any]:
    return {"id": f"{n:08d}-aaaa-bbbb-cccc-dddddddddddd", "name": name, "fileExtension": ext, "sizeBytes": size}


def _rec(asked: str, provider: str = "", dates: list[str] | None = None) -> dict[str, Any]:
    return {"asked": asked, "hints": {"provider": provider, "dates": dates or [], "pages": ""}}


def test_credentials_in_the_request_do_not_keep_a_file_named_without_them_out() -> None:
    rec = _rec("the office notes of Dr. Jane Roe, M.D., PsyD", provider="Dr. Jane Roe, M.D., PsyD")
    rows = [_row(1, "Exhibit 8-B Roe Jane office notes"), _row(2, "MD invoice"), _row(3, "Retainer")]
    got = rank.rank(rows, rec)
    assert got[0][0] == rows[0]["id"] and got[0][1] >= 4
    assert rows[1]["id"] not in dict(got)  # "md" alone scores nothing


def test_a_one_word_provider_over_eight_files_keeps_the_best_scored_not_the_first_listed() -> None:
    rec = _rec("the Shriners Hospital clinic visit of May 11, 2026", provider="Shriners", dates=["May 11, 2026"])
    rows = [_row(i, f"Shriners billing {i}") for i in range(1, 7)]
    rows.append(_row(7, "Shriners clinic notes May 2026"))  # listed 7th, the file that holds it
    rows.append(_row(8, "Shriners records"))
    got = rank.rank(rows, rec)
    assert got[0] == (rows[6]["id"], 2 + 1 + 2)  # provider 2, "clinic" 1, may + 2026
    assert len(got) == rank.TOP


def test_a_record_no_file_name_carries_ranks_nothing() -> None:
    rows = [_row(1, "Northside Clinic records"), _row(2, "Retainer agreement")]
    assert rank.rank(rows, _rec("the Westfield Dental panorex", provider="Westfield Dental")) == []


def test_dates_score_by_year_month_and_day_and_only_pdfs_rank() -> None:
    rec = _rec("the visit", provider="", dates=["3/3/2021"])
    rows = [_row(1, "Visit 2021-03-03"), _row(2, "March 3 2021 visit"), _row(3, "Visit 2021-03-03", ext=".docx")]
    got = dict(rank.rank(rows, rec))
    assert got[rows[0]["id"]] == 2 and got[rows[1]["id"]] == 3 and rows[2]["id"] not in got
    assert rank.date_tokens(["3/3/2021"]) >= {"2021", "3", "march", "mar"}


def test_a_tie_goes_to_the_smaller_file() -> None:
    rec = _rec("the chart", provider="Northside")
    rows = [_row(1, "Northside chart", size=9_000_000), _row(2, "Northside chart", size=56_272)]
    assert [fid for fid, _ in rank.rank(rows, rec)] == [rows[1]["id"], rows[0]["id"]]
