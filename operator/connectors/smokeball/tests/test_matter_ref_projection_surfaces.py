"""The matter.id -> matter.number projection over the memo and document surfaces
(ss#2390), the two the tasks/events enrichment did not reach.

WHY THESE TWO SURFACES. The 2026-07-31 provenance audit found 0 of 204 output
fields projected from a record: a task exposes a matter GUID, the number lives on
the matter, and nothing joined them, so the model joined them in context and
re-derived the answer differently on different days. Tasks and events got the
join. Memos and documents did not, and they are where the damage lands hardest:
a memo is a permanent entry on a client file (the 2026-07-14 cross-matter merge,
audit section 3.4), and a document read is what a drafting skill quotes from.

WHERE THESE FIXTURES COME FROM. Not from imagination. The audit's section 3.6
lesson is that an AUTHORED fixture taught a `source_tag` field that does not
exist in the Smokeball API, so a skill gated a whole lane on a branch that could
never fire on any tenant. Every field below is copied from a shape this repo
already asserts against the real API:

  * memo row `{id, matterId, text (RTF), plainText, createdDate}` -- the shape
    tests/test_memo_slimming.py pins for the RTF-slimming path, and `matterId`
    is why a memo resolves its own matter without being told.
  * file row `{fileId, name, fileExtension, sizeBytes}` -- the field names of the
    download contract recorded as "observed live 2026-07-05" at
    smokeball_connector/client.py:398. A file row states no matter at all, which
    is the point: its projection comes from the request path.
  * matter `{id, number}`, `number` MAY BE BLANK -- operator/verticals/law-firm/
    smokeball-surface.md:59, the field census taken against the published schema.

No live Smokeball calls: an httpx.MockTransport is injected so the real
resolution logic runs against scripted responses, and the tools are exercised
end to end through a monkeypatched `_get_client`.

THE DIRECTION THAT MATTERS. Every absence test below asserts the field is not
merely wrong but ABSENT. Supplying nothing leaves the model with nothing to
copy; supplying a plausible guess is the failure this whole layer exists to end.
"""

from __future__ import annotations

import httpx
import pytest

from smokeball_connector import server as srv
from smokeball_connector.client import SmokeballClient

M1 = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"
M2 = "062d73bd-4d91-41a7-8160-34bea8f7f81b"

_RTF = "{\\rtf1\\ansi\\ansicpg1252\\deff0 \\fs17 Scope-fix verification memo.\\par}"


def _mock_client(handler) -> SmokeballClient:
    client = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="apikey")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _handler(
    matters: dict[str, dict],
    payloads: dict[str, object],
    captured: list[httpx.Request] | None = None,
):
    """Serve `/matters/<id>` from ``matters`` (404 anything absent) and each path
    in ``payloads`` verbatim. Party contacts are not scripted, so caption
    composition no-ops and these tests isolate number resolution."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if path in payloads:
            return httpx.Response(200, json=payloads[path])
        if "/matters/" in path and path.count("/") == 2:
            mid = path.rsplit("/", 1)[-1]
            if mid in matters:
                return httpx.Response(200, json=matters[mid])
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(404, json={"error": "not found"})

    return handler


def _memo(memo_id: str, matter_id: str) -> dict:
    """A memo row in the shape tests/test_memo_slimming.py pins."""
    return {
        "id": memo_id,
        "matterId": matter_id,
        "text": _RTF,
        "plainText": "Scope-fix verification memo.",
        "createdDate": "2026-07-05T00:00:00Z",
    }


def _file(file_id: str, name: str) -> dict:
    """A file row carrying only file metadata: no matter, by design of the API."""
    return {
        "fileId": file_id,
        "name": name,
        "fileExtension": ".pdf",
        "sizeBytes": 51_233,
    }


def _matter_gets(captured: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in captured if "/matters/" in r.url.path and r.url.path.count("/") == 2]


@pytest.fixture()
def install(monkeypatch):
    """Install a mock-transport client as the tool layer's client."""

    def _install(client: SmokeballClient) -> SmokeballClient:
        monkeypatch.setattr(srv, "_get_client", lambda: client)
        return client

    return _install


# ---- Memos: the record states its own matter ------------------------------


def test_memo_resolves_its_own_matter_id() -> None:
    """A memo carries `matterId` flat, not `matter: {id}`. The projection reads
    both shapes, so a memo needs no help from the caller to be projected."""
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}}, {}))
    memo = _memo("memo-1", M1)
    srv._attach_matter_ref(client, memo)
    assert memo["matterNumber"] == "2026-PI-101"


def test_memo_list_projects_every_row_on_one_lookup(install) -> None:
    captured: list[httpx.Request] = []
    install(
        _mock_client(
            _handler(
                {M1: {"id": M1, "number": "2026-PI-101"}},
                {f"/matters/{M1}/memos": {"value": [_memo("memo-1", M1), _memo("memo-2", M1)]}},
                captured,
            )
        )
    )

    resp = srv.get_memos_on_matter(M1)

    assert [m["matterNumber"] for m in resp["value"]] == ["2026-PI-101"] * 2
    # N+1 guard: a twenty-memo listing must not cost twenty matter reads.
    assert len(_matter_gets(captured)) == 1
    # The slimming this surface already performed is untouched.
    assert all("text" not in m for m in resp["value"])


def test_memo_row_matter_id_outranks_the_request_path() -> None:
    """Record-first precedence. The path says one matter, the row says another;
    the row is the binding and wins, so a projected number can never describe a
    matter the record is not on."""
    client = _mock_client(
        _handler(
            {
                M1: {"id": M1, "number": "2026-PI-101"},
                M2: {"id": M2, "number": "2026-PI-106"},
            },
            {},
        )
    )
    memo = _memo("memo-1", M2)
    srv._attach_matter_ref(client, memo, matter_id=M1)
    assert memo["matterNumber"] == "2026-PI-106"


# ---- Files: the record states nothing; the request path does ---------------


def test_file_list_projects_from_the_request_path(install) -> None:
    """A file row names no matter, so the number comes from the matter the read
    was scoped to. That GUID is the one the API just filtered on, never one
    inferred from a file name."""
    captured: list[httpx.Request] = []
    install(
        _mock_client(
            _handler(
                {M1: {"id": M1, "number": "2026-PI-101"}},
                {
                    f"/matters/{M1}/documents/files": {
                        "value": [
                            _file("file-7", "RFP Set One"),
                            _file("file-8", "Records release"),
                        ]
                    }
                },
                captured,
            )
        )
    )

    resp = srv.get_files_on_matter(M1)

    assert [f["matterNumber"] for f in resp["value"]] == ["2026-PI-101"] * 2
    assert len(_matter_gets(captured)) == 1


def test_empty_file_listing_still_carries_the_number(install) -> None:
    """ "Nothing on file for <number>" is a sentence a skill has to write, and the
    number in it has to come from somewhere. The envelope carries it, so an empty
    listing is not the moment the model reaches into its memory."""
    install(
        _mock_client(
            _handler(
                {M1: {"id": M1, "number": "2026-PI-101"}},
                {f"/matters/{M1}/documents/files": {"value": []}},
            )
        )
    )

    resp = srv.get_files_on_matter(M1)

    assert resp["matterNumber"] == "2026-PI-101"


def test_get_file_projects_the_single_record(install) -> None:
    install(
        _mock_client(
            _handler(
                {M1: {"id": M1, "number": "2026-PI-101"}},
                {f"/matters/{M1}/documents/files/file-7": _file("file-7", "RFP Set One")},
            )
        )
    )

    got = srv.get_file(M1, "file-7")

    assert got["matterNumber"] == "2026-PI-101"


# ---- Absence, not fabrication ---------------------------------------------


def test_unresolvable_matter_leaves_memos_bare(install) -> None:
    """The matter 404s. Every memo comes back without a number, and the envelope
    without one too. This is the fixture the audit's section 3.6 lesson demands:
    the failure path is exercised, and what it produces is nothing."""
    install(
        _mock_client(
            _handler(
                {},  # every /matters/<id> 404s
                {f"/matters/{M1}/memos": {"value": [_memo("memo-1", M1)]}},
            )
        )
    )

    resp = srv.get_memos_on_matter(M1)

    assert "matterNumber" not in resp["value"][0]
    assert "matterCaption" not in resp["value"][0]
    assert "matterNumber" not in resp
    # The read itself still succeeds: enrichment can fail, the firm's work cannot.
    assert resp["value"][0]["id"] == "memo-1"


def test_blank_matter_number_is_absence_not_an_empty_string(install) -> None:
    """`number` may be blank on a real matter (smokeball-surface.md:59). A blank
    must not become `matterNumber: ""`, which reads as a value and invites the
    model to fill it in. The key is simply not there."""
    install(
        _mock_client(
            _handler(
                {M1: {"id": M1, "number": ""}},
                {f"/matters/{M1}/documents/files": {"value": [_file("file-7", "RFP Set One")]}},
            )
        )
    )

    resp = srv.get_files_on_matter(M1)

    assert "matterNumber" not in resp["value"][0]
    assert "matterNumber" not in resp


def test_a_failing_matter_read_never_breaks_the_document_surface(install) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if path == f"/matters/{M1}/documents/files":
            return httpx.Response(200, json={"value": [_file("file-7", "RFP Set One")]})
        return httpx.Response(500, json={"error": "upstream exploded"})

    install(_mock_client(boom))

    resp = srv.get_files_on_matter(M1)

    assert resp["value"][0]["fileId"] == "file-7"
    assert "matterNumber" not in resp["value"][0]


# ---- The document read a drafting skill quotes from ------------------------


def test_read_document_projects_the_number_beside_the_text(install, monkeypatch) -> None:
    """A served pleading names ITS matter inside its own text, which is not always
    the matter it was filed on. The number beside the extracted text is resolved
    from the matter, so a drafting skill quoting the document cites the record."""
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}}, {}))
    monkeypatch.setattr(
        type(client),
        "download_file",
        lambda self, matter_id, file_id: (
            {"name": "RFP Set One", "fileExtension": ".txt", "sizeBytes": 42},
            b"REQUESTS FOR PRODUCTION, SET ONE. Halverson v. Bell, 2026-PI-999.",
        ),
        raising=True,
    )
    install(client)

    out = srv.read_document(M1, "file-7")

    assert out["matterNumber"] == "2026-PI-101"
    # The number inside the document is left in the text, where it is evidence,
    # and is never promoted into the projected field.
    assert "2026-PI-999" in out["text"]


def test_read_document_unsupported_type_still_projects(install, monkeypatch) -> None:
    """The refusal payload is what a skill reports from when a file cannot be
    read, and that report names a matter too."""
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}}, {}))
    monkeypatch.setattr(
        type(client),
        "download_file",
        lambda self, matter_id, file_id: (
            {"name": "scan", "fileExtension": ".tiff", "sizeBytes": 42},
            b"\x00\x01",
        ),
        raising=True,
    )
    install(client)

    out = srv.read_document(M1, "file-9")

    assert out["error"]
    assert out["matterNumber"] == "2026-PI-101"


# ---- The helper's own edges ------------------------------------------------


def test_item_matter_id_reads_both_shapes_and_invents_none() -> None:
    assert srv._item_matter_id({"matter": {"id": M1}}) == M1
    assert srv._item_matter_id({"matterId": M1}) == M1
    assert srv._item_matter_id({"subject": "Halverson v. Bell 2026-PI-106"}) is None
    assert srv._item_matter_id({"matter": {"id": ""}}) is None
    assert srv._item_matter_id({"matterId": 17}) is None


def test_bare_list_response_is_projected(install) -> None:
    """The memo surface returns either an envelope or a bare list; the projection
    handles both rather than silently skipping one."""
    client = _mock_client(_handler({M1: {"id": M1, "number": "2026-PI-101"}}, {}))
    rows = [_memo("memo-1", M1)]
    srv._attach_matter_refs_to_list(client, rows, matter_id=M1)
    assert rows[0]["matterNumber"] == "2026-PI-101"
