"""A write may not name a matter other than the one it is filed on.

Every Smokeball write carries the true matter as an argument and the composed
text as another. Nothing compared them. On 2026-07-14 a memo filed on
2026-PI-101 stated that two tasks came "from matter PI-2026-0001" — merging a
matter whose service date was known with one whose date was not. That is the
path by which one matter's facts reach another matter's record, and it is the
shape a wrong deadline would travel.

The comparison is free at this point and available nowhere else: the overlay's
pre_tool_call hook can only BLOCK, not read the resolved number, and a skill
instruction is prose — which is the thing that failed.

Also covers provenance stamping. Smokeball records every Operator write under
the OAuth-consenting human, so without a marker in the content the client cannot
tell a person's entry from a machine's in their own system.

No live calls: an httpx.MockTransport serves scripted matter reads.
"""

from __future__ import annotations

import httpx
import pytest

from smokeball_connector import server as srv
from smokeball_connector.client import SmokeballClient

M101 = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"  # 2026-PI-101
M106 = "062d73bd-4d91-41a7-8160-34bea8f7f81b"  # 2026-PI-106


def _client(matters: dict[str, dict]) -> SmokeballClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600, "token_type": "Bearer"})
        if "/matters/" in path:
            mid = path.rsplit("/", 1)[-1]
            if mid in matters:
                return httpx.Response(200, json=matters[mid])
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"ok": True})

    c = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
    c._http = httpx.Client(transport=httpx.MockTransport(handler))
    return c


# ---- The write is refused when the text names a different matter ----------


def test_refuses_text_citing_another_matter() -> None:
    """The 2026-07-14 defect, as a regression case."""
    c = _client({M101: {"id": M101, "number": "2026-PI-101"}})
    with pytest.raises(srv.MatterReferenceMismatch) as e:
        srv._verify_matter_reference(c, M101, "tasks 0705cf01 and d1daf4fd from matter PI-2026-0001")
    assert "2026-PI-101" in str(e.value)
    assert "PI-2026-0001" in str(e.value)


def test_refuses_the_lookalike_binding() -> None:
    """A task on 062d73bd was reported as 2026-PI-107, the planted lookalike."""
    c = _client({M106: {"id": M106, "number": "2026-PI-106"}})
    with pytest.raises(srv.MatterReferenceMismatch):
        srv._verify_matter_reference(c, M106, "RFP Set One, matter 2026-PI-107")


def test_allows_text_citing_its_own_matter() -> None:
    c = _client({M101: {"id": M101, "number": "2026-PI-101"}})
    srv._verify_matter_reference(c, M101, "Response due on matter 2026-PI-101")


def test_checks_every_field() -> None:
    c = _client({M101: {"id": M101, "number": "2026-PI-101"}})
    with pytest.raises(srv.MatterReferenceMismatch):
        srv._verify_matter_reference(c, M101, "clean subject", "note cites 2026-PI-104")


def test_allows_text_citing_no_matter_at_all() -> None:
    c = _client({M101: {"id": M101, "number": "2026-PI-101"}})
    srv._verify_matter_reference(c, M101, "Records chase follow-up, no number cited")


# ---- Fail-open on unresolvable, fail-closed on mismatch -------------------


def test_unresolvable_matter_does_not_block_the_write() -> None:
    """A read failure must not obstruct the firm's work. We only refuse when we
    KNOW the number and the text says a different one."""
    c = _client({})  # every matter read 404s
    srv._verify_matter_reference(c, M101, "cites matter 2026-PI-999")


def test_matter_without_a_number_does_not_block() -> None:
    c = _client({M101: {"id": M101}})
    srv._verify_matter_reference(c, M101, "cites matter 2026-PI-999")


def test_empty_matter_id_is_tolerated() -> None:
    c = _client({})
    srv._verify_matter_reference(c, "", "cites matter 2026-PI-999")


# ---- Provenance stamping --------------------------------------------------


def test_stamps_machine_authored_content() -> None:
    assert srv._stamp("Records outstanding.").startswith("[Operator]")


def test_stamping_is_idempotent() -> None:
    once = srv._stamp("Records outstanding.")
    assert srv._stamp(once) == once


def test_does_not_stamp_empty_or_missing() -> None:
    assert srv._stamp(None) is None
    assert srv._stamp("") == ""
    assert srv._stamp("   ") == "   "


# ---- The matter-number pattern ------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("matter 2026-PI-101", ["2026-PI-101"]),
        ("PI-2026-0001 and 2026-PI-104", ["PI-2026-0001", "2026-PI-104"]),
        ("no identifiers here", []),
        ("a date 2026-07-31 is not a matter", []),
        ("case no. 24STCV18223 is a court number, not ours", []),
    ],
)
def test_matter_number_pattern(text: str, expected: list[str]) -> None:
    assert sorted(srv._MATTER_NUMBER_RE.findall(text)) == sorted(expected)
