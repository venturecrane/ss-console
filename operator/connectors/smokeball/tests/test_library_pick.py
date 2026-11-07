"""The chat document tool asks the library resolver which Word file a document
starts from (design 2026-11-06, piece 2; ``library_pick.py``).

Three claims, each a group below:

* NOTHING CHANGES ON A SEAT WITH NOTHING ACCEPTED (every seat today). With no
  ledger at all, a manifest that names none, or an empty ledger, the filed
  document is the one today's code makes: compared, member by member, with the
  render today's code path produces for the same input (the firm's file by
  name, or the starter). Member comparison because a .docx is a zip whose
  entry headers carry the save time (``test_library_resolution._docx_members``).
* AN ACCEPTED FILE IS THE BASE, and today's by-name lookup is never made.
* EVERY OTHER ANSWER REFUSES, files nothing, and never falls back.

Every case also checks the record in the result (``libraryResolution``) and
that the bytes the renderer opened are the ones the record names.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from operator_connector_sdk.library import MARKED, STYLES, Entry, Ledger, MemoryLibrary, accept, copies_dir, dump_ledger
from operator_connector_sdk.library.caller import record_problems
from smokeball_connector import docx_format, library, library_pick, server
from smokeball_connector.docx_format import FormatReport, load_firm_identity, render_document

from .test_library_resolution import _authored, _docx_members, _handler, _mock_client, _stub_record_check
from .test_render_document import make_firm_template

KIND = "memo"
MD = "# I. Heading\n\nBody."
WHEN = "2026-11-01T00:00:00Z"


def _entry(lib: MemoryLibrary, file_id: str, data: bytes, *, attorney=None, contract=STYLES) -> Entry:
    version = lib.add(
        file_id, f"Template - Memo{' - ' + attorney if attorney else ''}.docx", KIND, data, attorney=attorney
    )
    _BYTES[hashlib.sha256(data).hexdigest()] = data
    return Entry(
        KIND, attorney, file_id, version, hashlib.sha256(data).hexdigest(), contract, "admin@firm.example", WHEN
    )


_BYTES: dict[str, bytes] = {}


def _install(spec: Path, *entries: Entry, copies: bool = True) -> None:
    """The ledger, and unless ``copies`` is False the seat's copy of each file."""
    for e in entries if copies else ():
        copy = copies_dir(spec) / f"{e.sha256}.docx"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(_BYTES[e.sha256])
    ledger = Ledger()
    for e in entries:
        ledger = accept(ledger, e)
    blob = dump_ledger(ledger)
    (spec / "library").mkdir(parents=True, exist_ok=True)
    (spec / "library" / "accepted.json").write_bytes(blob)
    manifest = {"library_ledger": {"library/accepted.json": {"sha256": hashlib.sha256(blob).hexdigest()}}}
    (spec / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def spec(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    monkeypatch.setenv(library_pick.SPEC_DIR_ENV, str(d))
    return d


@pytest.fixture
def opened(monkeypatch) -> list[bytes | None]:
    """Every base the renderer opened, in order."""
    seen: list[bytes | None] = []
    real = docx_format.render_document

    def spy(markdown, document_class, base_bytes, *a, **k):
        seen.append(base_bytes)
        return real(markdown, document_class, base_bytes, *a, **k)

    monkeypatch.setattr(docx_format, "render_document", spy)
    return seen


def _uploaded(captured: list[httpx.Request]) -> bytes | None:
    puts = [r for r in captured if r.method == "PUT"]
    return puts[0].content if puts else None


def _today(base: bytes | None) -> bytes:
    """What today's code makes for MD: render_document on today's base."""
    report = FormatReport(document_class=KIND)
    return render_document(MD, KIND, base, report, firm_identity=load_firm_identity())[0]


def _draft(monkeypatch, captured, **handler_kw) -> dict:
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured, **handler_kw)))
    _stub_record_check(monkeypatch)
    return server.render_docx_draft("m-1", "Draft", MD, document_class=KIND)


def _ledger_state(spec: Path, state: str) -> None:
    if state == "manifest_only":
        (spec / "manifest.json").write_text(json.dumps({"layouts": {}}))
    elif state == "empty":
        _install(spec)


# ---- nothing accepted: the same document --------------------------------------------------


@pytest.mark.parametrize("state", ["absent", "manifest_only", "empty"])
def test_with_nothing_accepted_the_firms_file_by_name_is_used_as_today(spec, state, monkeypatch, tmp_path, opened):
    _ledger_state(spec, state)
    _authored(tmp_path, monkeypatch)
    template = make_firm_template(header_text="EXAMPLE LAW")
    listing = [{"id": "tpl-1", "name": "Template - Memo.docx", "folderId": "f-lib"}]
    captured: list[httpx.Request] = []
    out = _draft(monkeypatch, captured, template=template, listing_files=listing)
    assert out["fileId"] == "file-88" and out["refusals"] == []
    assert _docx_members(_uploaded(captured)) == _docx_members(_today(template))
    assert opened == [template]
    rec = out["libraryResolution"]
    assert record_problems(rec) == []
    assert rec["resolution"]["status"] == "none_accepted"
    assert rec["base"] == {
        "source": "library_file_by_name",
        "sha256": hashlib.sha256(template).hexdigest(),
        "name": "Template - Memo.docx",
        "contract": "",
    }
    # No lookup of the matter's attorney: a seat with nothing accepted makes no new call.
    assert not [r for r in captured if r.url.path in ("/matters/m-1", "/staff/s-1")]


@pytest.mark.parametrize("state", ["absent", "manifest_only", "empty"])
def test_with_nothing_accepted_and_no_library_the_starter_is_used_as_today(spec, state, monkeypatch, tmp_path, opened):
    _ledger_state(spec, state)
    monkeypatch.setenv(library.CUSTOMER_YAML_ENV, str(tmp_path / "absent.yaml"))
    captured: list[httpx.Request] = []
    out = _draft(monkeypatch, captured)
    assert _docx_members(_uploaded(captured)) == _docx_members(_today(None))
    assert opened == [None]
    rec = out["libraryResolution"]
    assert rec["base"] == {"source": "starter", "sha256": None, "name": "", "contract": ""}
    assert out["formatApplied"]["templateUsed"] is None


def test_the_record_is_complete_and_stable(spec, monkeypatch, tmp_path):
    monkeypatch.setenv(library.CUSTOMER_YAML_ENV, str(tmp_path / "absent.yaml"))
    out = _draft(monkeypatch, [])
    assert out["libraryResolution"] == {
        "schema_version": 1,
        "path": "chat_tool",
        "kind": KIND,
        "resolution": {
            "schema_version": 1,
            "status": "none_accepted",
            "kind": KIND,
            "reason": "no file is accepted for memo",
            "signer": None,
            "file": None,
            "newer_version": None,
        },
        "base": {"source": "starter", "sha256": None, "name": "", "contract": ""},
        "overridden": None,
    }


# ---- an accepted file is the base ---------------------------------------------------------


def _accepted_library(monkeypatch, spec: Path, data: bytes, **kw) -> MemoryLibrary:
    lib = MemoryLibrary()
    _install(spec, _entry(lib, "acc-1", data, **kw))
    monkeypatch.setattr(library_pick, "open_store", lambda *_a: lib)
    return lib


def test_an_accepted_styles_file_is_the_base_and_the_name_lookup_is_not_made(spec, monkeypatch, tmp_path, opened):
    accepted = make_firm_template(header_text="ACCEPTED LAW")
    _accepted_library(monkeypatch, spec, accepted)
    _authored(tmp_path, monkeypatch)

    def no_lookup(*_a, **_k):
        raise AssertionError("the by-name lookup ran although a file is accepted")

    monkeypatch.setattr(library, "resolve_template", no_lookup)
    other = make_firm_template(header_text="NEWEST BY NAME")  # in the library under the class's name: not used
    listing = [{"id": "tpl-1", "name": "Template - Memo.docx", "folderId": "f-lib"}]
    captured: list[httpx.Request] = []
    out = _draft(monkeypatch, captured, template=other, listing_files=listing)
    assert out["refusals"] == []
    assert _docx_members(_uploaded(captured)) == _docx_members(_today(accepted))
    assert _docx_members(_uploaded(captured)) != _docx_members(_today(other))
    assert opened == [accepted]
    rec = out["libraryResolution"]
    assert rec["resolution"]["status"] == "resolved"
    assert rec["base"]["source"] == "accepted_library_file"
    assert rec["base"]["sha256"] == hashlib.sha256(accepted).hexdigest() == rec["resolution"]["file"]["sha256"]
    assert out["formatApplied"]["templateUsed"]["source"] == "accepted_library_file"


def _staff_handler(captured, fail: bool = False):
    inner = _handler(captured)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/matters/m-1":
            captured.append(request)
            if fail:
                return httpx.Response(503, json={"message": "unavailable"})
            return httpx.Response(200, json={"id": "m-1", "personResponsibleStaffId": "s-1"})
        if request.url.path == "/staff/s-1":
            captured.append(request)
            return httpx.Response(200, json={"firstName": "Dana", "lastName": "Example"})
        return inner(request)

    return handler


def test_the_matter_attorneys_own_file_is_used_when_one_is_accepted(spec, monkeypatch, opened):
    firm_file, own = make_firm_template(header_text="FIRM"), make_firm_template(header_text="DANA'S OWN")
    lib = MemoryLibrary()
    _install(spec, _entry(lib, "firm-1", firm_file), _entry(lib, "own-1", own, attorney="dana example"))
    monkeypatch.setattr(library_pick, "open_store", lambda *_a: lib)
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_staff_handler(captured)))
    _stub_record_check(monkeypatch)
    out = server.render_docx_draft("m-1", "Draft", MD, document_class=KIND)
    assert opened == [own]
    rec = out["libraryResolution"]["resolution"]
    assert rec["file"]["scope"] == "attorney" and rec["signer"]["name"] == "Dana Example"


# ---- every other answer refuses -----------------------------------------------------------


def _refused(out: dict, captured: list[httpx.Request], status: str) -> None:
    assert out["fileId"] is None and len(out["refusals"]) == 1
    assert out["refusals"][0].startswith("Not made and nothing filed:")
    assert chr(0x2014) not in out["refusals"][0]
    assert _uploaded(captured) is None
    assert not [r for r in captured if r.method == "POST" and r.url.path.endswith("/documents/files")]
    rec = out["libraryResolution"]
    assert record_problems(rec) == []
    assert rec["resolution"]["status"] == status
    assert rec["base"]["source"] == "not_started"


def test_a_seat_copy_whose_bytes_changed_refuses(spec, monkeypatch, opened):
    accepted = make_firm_template()
    _accepted_library(monkeypatch, spec, accepted)
    (copies_dir(spec) / f"{hashlib.sha256(accepted).hexdigest()}.docx").write_bytes(make_firm_template(header_text="X"))
    captured: list[httpx.Request] = []
    _refused(_draft(monkeypatch, captured), captured, "integrity_failure")
    assert opened == []


def test_an_accepted_file_the_seat_holds_no_copy_of_refuses(spec, monkeypatch, opened):
    lib = MemoryLibrary()
    _install(spec, _entry(lib, "acc-1", make_firm_template()), copies=False)
    monkeypatch.setattr(library_pick, "open_store", lambda *_a: lib)
    captured: list[httpx.Request] = []
    out = _draft(monkeypatch, captured)
    _refused(out, captured, "copy_missing")
    assert "no copy" in out["refusals"][0]
    assert opened == []


def test_the_accepted_bytes_come_from_the_seat_copy_after_the_firm_saves_again(spec, monkeypatch, opened):
    accepted = make_firm_template(header_text="ACCEPTED LAW")
    lib = _accepted_library(monkeypatch, spec, accepted)
    lib.save("acc-1", make_firm_template(header_text="SAVED AGAIN"))
    out = _draft(monkeypatch, [])
    assert out["refusals"] == [] and opened == [accepted]
    assert out["libraryResolution"]["resolution"]["newer_version"]["newest_version"] == "acc-1-v2"


def test_a_ledger_the_seat_cannot_trust_refuses(spec, monkeypatch, opened):
    _accepted_library(monkeypatch, spec, make_firm_template())
    (spec / "library" / "accepted.json").write_text("{}")  # no longer the bytes the manifest vouches for
    captured: list[httpx.Request] = []
    _refused(_draft(monkeypatch, captured), captured, "cannot_look")
    assert opened == []


def test_a_library_that_cannot_be_reached_refuses(spec, monkeypatch, opened):
    lib = _accepted_library(monkeypatch, spec, make_firm_template())
    lib.unavailable = "the case system timed out"
    captured: list[httpx.Request] = []
    _refused(_draft(monkeypatch, captured), captured, "cannot_look")


def test_a_marked_file_is_refused_never_written_into_as_a_styles_file(spec, monkeypatch, opened):
    _accepted_library(monkeypatch, spec, make_firm_template(), contract=MARKED)
    captured: list[httpx.Request] = []
    out = _draft(monkeypatch, captured)
    _refused(out, captured, "refused")
    assert "marked file" in out["refusals"][0]


def test_a_matter_attorney_that_cannot_be_read_refuses_when_it_would_matter(spec, monkeypatch, opened):
    lib = MemoryLibrary()
    _install(spec, _entry(lib, "own-1", make_firm_template(), attorney="dana example"))
    monkeypatch.setattr(library_pick, "open_store", lambda *_a: lib)
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_staff_handler(captured, fail=True)))
    _stub_record_check(monkeypatch)
    out = server.render_docx_draft("m-1", "Draft", MD, document_class=KIND)
    _refused(out, captured, "cannot_look")


def test_the_template_tool_takes_the_same_answer(spec, monkeypatch, opened):
    accepted = make_firm_template(header_text="ACCEPTED LAW")
    _accepted_library(monkeypatch, spec, accepted)
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured)))
    out = server.render_docx_template(
        "m-lib", "Template - Memo.docx", "# Heading\n\n{{FILL: body | the record}}", document_class=KIND
    )
    assert out["refusals"] == [] and opened == [accepted]
    assert out["libraryResolution"]["base"]["source"] == "accepted_library_file"


# ---- the firm's attorney roster says who is an attorney ------------------------------------


def _roster(tmp_path: Path, monkeypatch, doc) -> None:
    from operator_connector_sdk.library import roster

    folder = tmp_path / "roster"
    folder.mkdir()
    (folder / roster.FILE_NAME).write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(roster.DIR_ENV, str(folder))


def _own_file_draft(spec, monkeypatch) -> tuple[dict, list[httpx.Request]]:
    lib = MemoryLibrary()
    _install(spec, _entry(lib, "own-1", make_firm_template(header_text="DANA'S OWN"), attorney="dana example"))
    monkeypatch.setattr(library_pick, "open_store", lambda *_a: lib)
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_staff_handler(captured)))
    _stub_record_check(monkeypatch)
    return server.render_docx_draft("m-1", "Draft", MD, document_class=KIND), captured


def test_an_attorney_on_the_roster_gets_their_own_file_as_without_one(spec, monkeypatch, tmp_path, opened):
    _roster(tmp_path, monkeypatch, {"schema_version": 1, "attorneys": [{"name": "Dana  Example", "emails": []}]})
    out, _ = _own_file_draft(spec, monkeypatch)
    assert out["refusals"] == [] and out["libraryResolution"]["resolution"]["signer"]["name"] == "Dana Example"


@pytest.mark.parametrize(
    "doc,status",
    [
        ({"schema_version": 1, "attorneys": [{"name": "Someone Else", "emails": []}]}, "refused"),
        ("[]", "cannot_look"),
    ],
)
def test_a_matter_attorney_the_roster_does_not_list_is_never_signed_for(
    spec, monkeypatch, tmp_path, opened, doc, status
):
    """FALSIFIER: drop the roster check in ``chat_base`` and Dana's own file is used."""
    _roster(tmp_path, monkeypatch, doc)
    out, captured = _own_file_draft(spec, monkeypatch)
    _refused(out, captured, status)
    assert opened == []
