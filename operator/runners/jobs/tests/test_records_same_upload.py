"""One upload under two file ids (same sha256): the reply groups a record's
results by sha, and the result that carries the record's best outcome keeps
it. Review of #3725: the first copy saved not found (page 10 not reached on
the wire), the second copy found it on page 10, and the grouping kept the
first, filtered it empty and skipped the second, so the record vanished from
the reply. Names are invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import records_testkit
from jobs import proof_mode
from records_testkit import LABEL, FakeFinderModel, FakeSmokeball, pdf
from test_records_end_states import _facts, _job, _rec, by_sections

XRAY = "the Northside Clinic x-ray"


@pytest.fixture(autouse=True)
def _no_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)


class _Flaky(FakeFinderModel):
    """Page 10 fails on the wire for its first three requests, then reads."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.left = 3

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        if "pages" in body["output_config"]["format"]["schema"]["properties"]:
            labels = {
                int(m.group(1)) for b in content if b["type"] == "text" for m in [LABEL.match(b.get("text", ""))] if m
            }
            if 10 in labels and self.left > 0:
                self.left -= 1
                self.bodies.append(body)
                return httpx.Response(400, json={"error": {"type": "invalid_request_error"}})
        return super().__call__(request)


def test_the_copy_that_found_the_record_is_kept_when_the_first_copy_did_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(records_testkit.TRUTH, 10, ("1/1/2022", "Northside Clinic", "x-ray", True))
    sb = FakeSmokeball()
    first = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    second = sb.add(2, "Northside Clinic records copy.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, _Flaky(match_fn=by_sections), sb, [_rec(XRAY)], f"Please send {XRAY}.")
    located = {fid: res for _n, fid, res in r.located()}
    assert set(located) == {first, second}  # both copies saved: not found, then found
    assert v["verdict"] == "delivered" and v["items"] == [{"n": 1, "outcome": "attached", "files": 1}], v
    assert [c["file_id"] for c in v["cuts"]] == [second]
    assert "Northside Clinic records copy pp. 10.pdf, pages 10-10 (1 page)" in _facts(r)
    assert "Not " not in _facts(r)  # the not-found copy is not named beside the found one
