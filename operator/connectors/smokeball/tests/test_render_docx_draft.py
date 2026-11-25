"""Unit coverage for ``render_docx_draft`` — the .docx FILLED-DRAFT producer.

The template renderer and this one are the same machine pointed at opposite
artifacts, so almost every test here is a pair: the same input, refused by one
gate and accepted by the other. That pairing is the contract. If both gates ever
agree about case content, one of them is wrong.

Three things this file is really protecting:

1. **Case content passes.** A demand letter is dates and dollar figures. The
   template gate refuses those for a good reason that does not apply here, and
   the consequence of getting it wrong is the one already observed on the pilot:
   the draft could not be a .docx at all and was filed as a .txt an attorney
   cannot edit in Word.
2. **Markers survive, visibly.** A draft whose ``{{ATTORNEY:}}`` marker was
   silently resolved or dropped looks finished. That is worse than a refusal,
   because it is invisible.
3. **The em-dash carve-out is bounded to quotations.** The record's words are
   not ours to restyle; our own prose is.
"""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest

from smokeball_connector import server
from smokeball_connector.extract import _docx_text
from smokeball_connector.render import (
    TemplateContentRefused,
    check_draft_content,
    check_template_content,
    find_draft_violations,
    find_violations,
    render_markdown_to_docx,
)

_UPLOAD_URL = "https://s3.example.com/apiuploads/draft-1?X-Amz-Signature=deadbeef"

_FILLED_DRAFT = """# DEMAND LETTER DRAFT - ATTORNEY REVIEW REQUIRED

Matter 2026-PI-104. Date of loss November 2, 2025.

## Medical specials

- Kaiser Permanente Sacramento, emergency department: $6,240.00
- Sierra Imaging Associates, MRI lumbar spine: $3,150.00
- Total billed: $25,430.00

## Wage loss

Mr Whitfield lost $27,750.00 in wages between 2025-11-03 and 2026-03-08.

## Reserved

{{ATTORNEY: decision reserved - the demand figure, and whether damages exceed
the disclosed each-occurrence limit}}

{{NOT IN RECORD: final DHCS itemization, searched the lien correspondence}}
"""


def _draft_rules(markdown: str) -> list[str]:
    return [v.rule for v in find_draft_violations(markdown)]


# ---- The inversion: case content ------------------------------------------


def test_a_filled_draft_passes_the_draft_gate() -> None:
    assert find_draft_violations(_FILLED_DRAFT) == []
    check_draft_content(_FILLED_DRAFT)  # does not raise


def test_the_same_draft_is_refused_by_the_TEMPLATE_gate() -> None:
    """The pairing that makes the first test mean something. If this ever stops
    failing, the two gates have collapsed into one and the template rule that
    keeps one matter's facts out of every future matter is gone."""
    rules = [v.rule for v in find_violations(_FILLED_DRAFT)]
    assert "case-content" in rules
    with pytest.raises(TemplateContentRefused):
        check_template_content(_FILLED_DRAFT)


@pytest.mark.parametrize(
    "content",
    [
        "2024-03-01",
        "3/1/2024",
        "March 1, 2024",
        "$4,500.00",
        "$250",
        "ZZ-9999-0001",
        "2026-PI-102",
        "000123-000456",
        "1234567",
    ],
)
def test_every_case_content_shape_the_template_refuses_is_allowed_in_a_draft(content: str) -> None:
    """Each of these is refused by the template gate BY DESIGN and is required
    in a draft. Parametrized against the same list the template tests use, so
    the two files disagree explicitly rather than by omission."""
    body = f"The record establishes {content} on this matter.\n"
    assert "case-content" in [v.rule for v in find_violations(body)]
    assert find_draft_violations(body) == []


# ---- What the draft gate KEEPS ---------------------------------------------


@pytest.mark.parametrize(
    "broken",
    [
        "A sentence with {{FILL: unterminated marker\n",
        "A sentence with a stray }} close\n",
        "An {{}} empty marker\n",
    ],
)
def test_malformed_markers_are_still_refused(broken: str) -> None:
    """An unterminated marker is a sentence fragment a reader answers as prose.
    That is true of a draft exactly as it is of a template."""
    assert _draft_rules(broken), f"{broken!r} produced no violation"


def test_html_comments_are_still_refused() -> None:
    """Drafting gate 9, and MORE load-bearing in a draft than in a template: the
    thing usually written in a comment is the reservation, and a reservation
    that renders as nothing is one the attorney never sees."""
    assert "html-comment" in _draft_rules("Body text.\n\n<!-- reserved: the demand figure -->\n")


def test_an_em_dash_in_our_own_prose_is_still_refused() -> None:
    assert "em-dash" in _draft_rules("We reviewed the record — carefully.\n")


# ---- The em-dash carve-out, and its bounds ---------------------------------


def test_an_em_dash_inside_a_quotation_is_allowed() -> None:
    """House style bans em dashes; the drafting checker requires quotations to
    appear VERBATIM in a source. A record whose quoted words contain an em dash
    can satisfy only one of those. The quote wins, because restyling the
    record's words is a misquotation and that is the worse defect."""
    body = 'The report reads "the lane looked clear — nothing ahead" at 24:7.\n'
    assert find_draft_violations(body) == []


def test_the_carve_out_does_not_leak_past_the_closing_quote() -> None:
    """The exemption is the quoted span, not the rest of the document."""
    body = 'He said "the lane looked clear" and then we — the firm — followed up.\n'
    assert "em-dash" in _draft_rules(body)


def test_an_unterminated_quote_exempts_nothing() -> None:
    """Same fail-toward-refusing choice the marker scanner makes: treating the
    rest of the document as quoted would let one stray quote mark disable the
    rule for everything after it."""
    body = 'He said "the lane looked clear and then we — the firm — followed up.\n'
    assert "em-dash" in _draft_rules(body)


def test_curly_quotes_are_honored_too() -> None:
    """A record pasted from Word carries curly quotes. Honoring only straight
    ones would refuse exactly the quotations most likely to be real."""
    body = "The report reads “the lane looked clear — nothing ahead” at 24:7.\n"
    assert find_draft_violations(body) == []


def test_a_template_gets_no_quote_carve_out() -> None:
    """A skeleton has no quotations of its own, so exempting spans there would
    only create a way past house style."""
    body = 'A skeleton line with "a quoted — passage" in it.\n'
    assert "em-dash" in [v.rule for v in find_violations(body)]


# ---- Markers survive the render, visibly -----------------------------------


def test_markers_render_verbatim_and_visible() -> None:
    """Read back through the same extractor ``read_document`` uses, so this
    asserts what the attorney would actually see in the filed document."""
    text = _docx_text(render_markdown_to_docx(_FILLED_DRAFT))
    assert "{{ATTORNEY: decision reserved" in text
    assert "{{NOT IN RECORD: final DHCS itemization" in text
    assert "$25,430.00" in text
    assert "Date of loss November 2, 2025." in text


def test_a_marker_containing_emphasis_characters_survives_intact() -> None:
    markdown = "Body {{FILL: the *styled* caption | matter record}} tail.\n"
    text = _docx_text(render_markdown_to_docx(markdown))
    assert "{{FILL: the *styled* caption | matter record}}" in text


# ---- The tool ---------------------------------------------------------------


def _mock_client(handler) -> object:
    from smokeball_connector.client import SmokeballClient

    client = SmokeballClient(
        region="us",
        environment="staging",
        client_id="cid",
        client_secret="sec",
        api_key="apikey",
    )
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if request.method == "POST" and path.endswith("/documents/files"):
            return httpx.Response(202, json={"fileId": "file-77", "uploadUrl": _UPLOAD_URL})
        if str(request.url) == _UPLOAD_URL:
            return httpx.Response(200)
        return httpx.Response(200, json={"ok": True})

    return handler


def _stub_record_check(monkeypatch, *, passed: bool = True, disposition: str = "pass") -> None:
    """Hold the RECORD check still so these tests measure the upload shape.

    The record check has its own suite (test_record_check.py) which runs the
    real 1574-line checker. Exercising it again here would make an upload-shape
    failure and a gate failure indistinguishable in the output.
    """
    from smokeball_connector import record_check as rc

    monkeypatch.setattr(server, "_collect_matter_sources", lambda _m: ([("Src", "text")], [], []))
    # Patch the MODULE attribute, not a name on `server`. render_docx_draft does
    # `from .record_check import run_record_check` inside the function body, so
    # the binding is resolved from the module on every call — a stub set on
    # `server` would be silently ignored and the real checker would run, which is
    # exactly what happened the first time this was written.
    monkeypatch.setattr(
        rc,
        "run_record_check",
        lambda *a, **k: rc.RecordCheckResult(
            passed=passed,
            disposition=disposition,
            refusals=[] if passed else ["[2a] quoted passage is not contiguous in any source"],
            checked_sources=1,
        ),
    )


def test_tool_files_the_rendered_draft(monkeypatch) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured)))
    _stub_record_check(monkeypatch)

    out = server.render_docx_draft("m-104", "Demand Letter Draft", _FILLED_DRAFT)

    assert out["fileId"] == "file-77"
    assert out["fileName"] == "Demand Letter Draft.docx"
    assert out["refusals"] == []
    put = next(r for r in captured if r.method == "PUT")
    assert hashlib.sha256(put.content).hexdigest() == out["sha256"]
    assert len(put.content) == out["sizeBytes"]
    assert "{{ATTORNEY: decision reserved" in _docx_text(put.content)


def test_tool_refuses_without_uploading_anything(monkeypatch) -> None:
    """A refusal must not leave a partial artifact on the matter. The attorney
    would have no way to tell it apart from a finished one."""
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured)))

    out = server.render_docx_draft("m-104", "Bad Draft", "Body — dash.\n\n<!-- hidden -->\n")

    assert out["fileId"] is None
    assert out["sha256"] is None
    assert len(out["refusals"]) == 2
    assert not [r for r in captured if r.method == "PUT"]
    assert not [r for r in captured if r.method == "POST" and r.url.path.endswith("/documents/files")]


def test_tool_adds_the_docx_suffix(monkeypatch) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured)))
    out = server.render_docx_draft("m-104", "No Suffix", _FILLED_DRAFT)
    assert out["fileName"] == "No Suffix.docx"


def test_tool_sends_base64_it_computed_itself(monkeypatch) -> None:
    """``add_file`` bans model-composed base64 (#2055) and names the renderer as
    the carve-out. The bytes on the wire must be the ones this tool encoded."""
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured)))
    _stub_record_check(monkeypatch)
    server.render_docx_draft("m-104", "Draft", _FILLED_DRAFT)
    put = next(r for r in captured if r.method == "PUT")
    assert base64.b64encode(put.content)  # round-trips; bytes are real docx bytes
    assert put.content[:2] == b"PK", "a .docx is a zip container"


def test_tool_refuses_and_uploads_nothing_when_the_record_check_fails(monkeypatch) -> None:
    """The reachability property this whole phase exists for. A draft whose
    quotations do not trace must not reach the matter, and the refusal carries
    the checker's own finding so the model is told which rule it broke.

    FALSIFIER: the tool must make NO POST and NO PUT. A refusal that still filed
    a partial artifact would be indistinguishable, to the attorney, from a
    finished one.
    """
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured)))
    _stub_record_check(monkeypatch, passed=False, disposition="fail_findings")

    out = server.render_docx_draft("m-104", "Bad Draft", _FILLED_DRAFT)

    assert out["fileId"] is None
    assert out["recordCheck"] == "fail_findings"
    assert any("not contiguous" in r for r in out["refusals"])
    assert not [r for r in captured if r.method == "PUT"]
    assert not [r for r in captured if r.method == "POST" and r.url.path.endswith("/documents/files")]


def test_the_content_gate_runs_BEFORE_the_record_check(monkeypatch) -> None:
    """Ordering, asserted because it is cheap and the reverse would be wasteful
    and confusing: a draft with an em dash should be told about the em dash, not
    handed a record-check verdict it will have to re-earn after fixing it.

    The record check is the expensive step (it downloads and extracts every
    document on the matter), so a content violation must short-circuit it.
    """
    called: list[str] = []
    monkeypatch.setattr(server, "_collect_matter_sources", lambda _m: called.append("collected") or ([], [], []))
    out = server.render_docx_draft("m-104", "Bad", "Body — dash.\n")
    assert out["fileId"] is None
    assert any("em dash" in r for r in out["refusals"])
    assert not called, "the record check ran despite a content-gate refusal"
    assert "recordCheck" not in out


# ---- A list of documents (2026-11-24) ---------------------------------------
#
# An attorney asked for the supplemental production list for the documents in a
# folder, leaving out a withheld statement. The list had one entry more than the
# folder had documents, named the withheld statement, and when the Word version
# was refused the reply told the attorney why. Every name below is invented.

_FOLDER = "fld-supp"
_SUB = "fld-supp-sub"
_FOLDER_FILES = [
    {"id": "f1", "name": "Harbor Clinic Records.pdf", "folder": {"id": _FOLDER}},
    {"id": "f2", "name": "Harbor Clinic Billing.pdf", "folder": {"id": _FOLDER}},
    {"id": "f3", "name": "Quill Imaging Report.pdf", "folder": {"id": _SUB}},
    {"id": "f4", "name": "Neighbor Witness Statement.pdf", "folder": {"id": _FOLDER}},
    {"id": "f6", "name": "Old Production List.docx", "folder": {"id": _FOLDER}},
    {"id": "f7", "name": "Scratch.txt", "folder": {"id": _FOLDER}},
    {"id": "f5", "name": "Elsewhere.pdf", "folder": {"id": "fld-other"}},
]
_TREE = {
    "value": [
        {
            "folders": [
                {"id": _FOLDER, "name": "Supplemental Production", "folders": [{"id": _SUB, "name": "Imaging"}]},
                {"id": "fld-other", "name": "Other"},
            ]
        }
    ]
}
_HELD = ["Neighbor Witness Statement.pdf"]

_LIST = """# SUPPLEMENTAL PRODUCTION LIST

1. Harbor Clinic records, dates of service 03/27/2025 through 04/15/2025
   1. Visit notes
   2. Intake form
2. Harbor Clinic billing, dates of service 03/27/2025 through 04/15/2025
3. Quill Imaging report dated 04/02/2025
"""


def _tree_handler(captured: list[httpx.Request]):
    base = _handler(captured)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/documents/folders"):
            return httpx.Response(200, json=_TREE)
        return base(request)

    return handler


def _setup(monkeypatch, *, passed: bool = True, disposition: str = "pass") -> list[httpx.Request]:
    from smokeball_connector import list_check

    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_tree_handler(captured)))
    _stub_record_check(monkeypatch, passed=passed, disposition=disposition)
    monkeypatch.setattr(list_check, "list_matter_files", lambda _c, _m: list(_FOLDER_FILES))
    return captured


def _list(draft: str = _LIST, held=tuple(_HELD), folder: str | None = _FOLDER):
    return server.render_docx_draft(
        "m-104", "Supp List", draft, held_out_file_names=list(held), listed_folder_id=folder
    )


def test_a_list_naming_a_held_out_document_is_refused_and_nothing_is_filed(monkeypatch) -> None:
    captured = _setup(monkeypatch)
    out = _list(_LIST + "4. Neighbor Witness Statement (withheld)\n")
    assert out["fileId"] is None
    assert out["status"] == "refused" and out["reason"] == "held_out_named"
    assert not [r for r in captured if r.method == "PUT"]


@pytest.mark.parametrize(
    "draft",
    [
        _LIST + "\nAlso produced: Neighbor Witness Statement.pdf\n",
        _LIST + "- Neighbor Witness Statement\n",
    ],
)
def test_a_held_out_document_is_named_by_its_full_file_name_or_an_entry(draft) -> None:
    from smokeball_connector import list_check

    assert list_check.withheld_named(draft, _HELD) == _HELD


@pytest.mark.parametrize(
    ("draft", "held"),
    [
        # A one-word stem would catch ordinary words in an entry.
        ("1. Progress Notes 3/2/25\n", ["Notes.pdf"]),
        # A person's name in a caption is not the file.
        ("| JANE DOE, Plaintiff | Case No. 1 |\n\nJane Doe was seen on 3/2/25.\n", ["Jane Doe.pdf"]),
        # The draft's own held-out part may name it, by heading or by a Withheld: line.
        (_LIST + "\n## Held out\n\nNeighbor Witness Statement.pdf\n", _HELD),
        (_LIST + "\nWithheld: Neighbor Witness Statement.pdf\n", _HELD),
        (_LIST + "\n**Held out:** Neighbor Witness Statement.pdf\n", _HELD),
    ],
)
def test_held_out_names_raise_no_false_positives(draft, held) -> None:
    from smokeball_connector import list_check

    assert list_check.withheld_named(draft, held) == []


def test_a_list_with_more_entries_than_the_folder_has_documents_is_refused(monkeypatch) -> None:
    captured = _setup(monkeypatch)
    out = _list(_LIST + "4. Harbor Clinic records (duplicate)\n")
    assert out["status"] == "refused" and out["reason"] == "list_count_mismatch"
    assert "4 numbered entries" in out["refusals"][0] and "holds 3" in out["refusals"][0]
    assert not [r for r in captured if r.method == "PUT"]


def test_a_held_out_name_matching_no_file_is_refused_on_its_own(monkeypatch) -> None:
    captured = _setup(monkeypatch)
    out = _list(held=["Parents Statement.pdf"])
    assert out["status"] == "refused" and out["reason"] == "held_out_unmatched"
    assert "Parents Statement.pdf" in out["refusals"][0]
    assert not [r for r in captured if r.method == "PUT"]


def test_a_list_counts_subfolders_and_skips_held_out_and_earlier_lists(monkeypatch) -> None:
    """Three to list: two in the folder, one in its subfolder; the held-out
    statement, the earlier list and a .txt are not counted, nor are nested entries."""
    captured = _setup(monkeypatch)
    out = _list()
    assert out["fileId"] == "file-77" and out["refusals"] == []
    assert out["fileName"] == "Supp List.docx"
    put = next(r for r in captured if r.method == "PUT")
    assert put.content[:2] == b"PK"
    assert "Neighbor" not in _docx_text(put.content)


def test_folder_ids_walk_the_folder_tree_down_only(monkeypatch) -> None:
    from smokeball_connector import list_check

    _setup(monkeypatch)
    client = server._get_client()
    assert list_check.folder_ids(client, "m-104", _SUB) == {_SUB}
    assert list_check.folder_ids(client, "m-104", _FOLDER) == {_FOLDER, _SUB}


@pytest.mark.parametrize(
    ("draft", "disposition", "status", "reason"),
    [
        ("Body — dash.\n", "pass", "revise", "content_gate"),
        (_FILLED_DRAFT, "fail_findings", "revise", "record_check"),
        (_FILLED_DRAFT, "source_unextractable", "refused", "record_check"),
        (_FILLED_DRAFT, "checker_absent", "refused", "record_check"),
    ],
)
def test_refusal_status_pages_smd_only_on_a_final_outcome(monkeypatch, draft, disposition, status, reason) -> None:
    """A finding the Operator fixes by redrafting is ``revise`` (not a shortfall,
    so each iteration of meet-and-confer-drafter does not page SMD); a refusal a
    redraft cannot clear is ``refused`` (a shortfall: SMD is paged). Before
    this, no refusal carried a status and the audit scored every one ``ok``."""
    _setup(monkeypatch, passed=disposition == "pass", disposition=disposition)
    out = server.render_docx_draft("m-104", "Draft", draft)
    assert out["fileId"] is None
    assert out["status"] == status and out["reason"] == reason
    assert "Let me look into that. I'll reply in this thread." in out["note"]


def test_template_refusals_carry_the_same_shape(monkeypatch) -> None:
    _setup(monkeypatch)
    out = server.render_docx_template("m-104", "Skeleton", "Matter 2026-PI-104, $6,240.00 billed.\n")
    assert out["fileId"] is None
    assert out["status"] == "revise" and out["reason"] == "content_gate"
    assert "Let me look into that. I'll reply in this thread." in out["note"]
