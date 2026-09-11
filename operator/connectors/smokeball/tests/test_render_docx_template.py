"""Unit coverage for ``render_docx_template`` — the .docx template producer.

Three contracts, no network (``httpx.MockTransport``, same convention as
test_document_writes.py):

1. The content gate REFUSES and lists every violation. A template that carries
   case content, a broken marker, an em dash, or an HTML comment is not
   repaired, not partially rendered, and not uploaded.
2. The render round-trips: what goes in as markdown comes out of the .docx as
   text, verified through ``extract._docx_text`` — the same extractor
   ``read_document`` uses, so this asserts what the agent would actually read
   back off the matter. Markers survive VERBATIM and render-visible.
3. The upload is the existing two-stage ``add_file`` path, and the bytes on the
   wire are exactly the rendered bytes the tool hashed.
"""

from __future__ import annotations

import base64
import hashlib
import tomllib
from pathlib import Path

import httpx
import pytest

from smokeball_connector import server
from smokeball_connector.extract import _docx_text
from smokeball_connector.render import (
    TemplateContentRefused,
    check_template_content,
    find_violations,
    render_markdown_to_docx,
)

_UPLOAD_URL = "https://s3.example.com/apiuploads/tpl-1?X-Amz-Signature=deadbeef"

_CLEAN_SKELETON = """# SKELETON: Demand Letter

Firm template. Structure is fixed; case content is filled from the matter record.

## I. Introduction

This firm represents {{FILL: claimant name | matter record}} for injuries
sustained on {{FILL: date of loss | traffic collision report}}.

{{NOT IN RECORD: carrier claim number, searched claim correspondence}}

### A. Reserved

{{ATTORNEY: confirm settlement authority before transmission}}

- {{FILL: enclosed records by provider | the documents attached}}
- Every figure traces to a **bill** or a *lien* in the file.
"""


def _rules(markdown: str) -> list[str]:
    return [v.rule for v in find_violations(markdown)]


# ---- The gate: refusals -----------------------------------------------------


def test_gate_passes_a_clean_skeleton() -> None:
    assert find_violations(_CLEAN_SKELETON) == []
    check_template_content(_CLEAN_SKELETON)  # does not raise


@pytest.mark.parametrize(
    "content",
    [
        "2024-03-01",  # ISO date
        "3/1/2024",  # numeric date
        "March 1, 2024",  # month-name date
        "Mar. 1 2024",  # abbreviated, no comma
        "$4,500.00",  # dollar figure
        "$250",  # dollar figure, bare
        "ZZ-9999-0001",  # the sentinel identifier class
        "2026-PI-102",  # a matter number
        "000123-000456",  # a bates range
        "4471902",  # a bare claim number
    ],
)
def test_gate_refuses_each_case_content_shape(content: str) -> None:
    """A case fact in a template is a case fact in every matter the template is
    ever filled for."""
    with pytest.raises(TemplateContentRefused) as exc:
        check_template_content(f"# Demand\n\nRecorded: {content}.\n")
    (violation,) = exc.value.violations
    assert violation.rule == "case-content"
    assert violation.line == 3
    assert content in violation.detail


@pytest.mark.parametrize(
    "structure",
    [
        "Code of Civil Procedure section 999",
        "sections 999 through 999.5",
        "not fewer than 30 days from transmission",
        "not fewer than 33 days if sent by ordinary mail",
        "no impermissible subparts (CCP 2030.060(f))",
        "Vehicle Code sections 22350, 21703, 21801, 22107, and 21453",
        "Evidence Code section 669",
        "Civil Code section 3333 is the measure-of-damages authority",
        "Howell v. Hamilton Meats (2011) 52 Cal.4th 541",
        "Pebley v. Santa Clara Organics (2018) 22 Cal.App.5th 1266",
        "a 998 offer",
    ],
)
def test_gate_passes_statutory_structure(structure: str) -> None:
    """The falsifier in the other direction, and the reason this is a SHAPE gate
    rather than a digit gate: a skeleton is full of legitimate numbers. If code
    sections and statutory periods refused, the gate would refuse every real
    skeleton and would be measuring nothing."""
    assert find_violations(f"Confirm: {structure}.\n") == []


def test_citation_exemption_is_scoped_to_the_bare_run_shape() -> None:
    """The one collision in the rule set: California code sections run to five
    digits, so a bare long run is a claim number only when nothing cites it as
    law. The exemption is therefore narrow, and these are its edges."""
    # cited as law -> structure
    assert find_violations("Vehicle Code section 22350 (basic speed law).\n") == []
    assert find_violations("Confirm compliance with §§ 999, 999.5.\n") == []
    # the same run, uncited -> case content
    assert _rules("Our reference is 22350 on the file.\n") == ["case-content"]
    # a hyphen is not a citation joiner: a bates range in a citation's clothes
    assert _rules("See sections 000123-000456 of the production.\n") == ["case-content"]
    # the exemption never reaches another shape
    assert _rules("Paid under section 4 the sum of $4,500.00.\n") == ["case-content"]


def test_gate_allows_case_content_inside_a_marker() -> None:
    """A marker names its own source, so what is inside it is structure."""
    markdown = (
        "# Interrogatories\n\n"
        "{{FILL: date of loss, e.g. 2024-03-01 | traffic collision report}}\n"
        "{{FILL: policy limits, e.g. $250,000 | carrier disclosure}}\n"
        "{{NOT IN RECORD: claim number 4471902, searched correspondence}}\n"
    )
    assert find_violations(markdown) == []


def test_gate_reports_one_violation_per_offending_line_not_per_match() -> None:
    """A row of figures is one thing to fix; 40 entries would bury the other
    rules."""
    violations = find_violations("Billed $1,234.56 paid $789.01 on 2024-03-01\n")
    assert [v.rule for v in violations] == ["case-content"]


def test_gate_refuses_an_unclosed_marker() -> None:
    assert "marker-syntax" in _rules("{{FILL: claimant name | matter record\n")


def test_gate_refuses_a_stray_closing_marker() -> None:
    violations = find_violations("claimant name | matter record}}\n")
    assert [v.rule for v in violations] == ["marker-syntax"]
    assert "no matching" in violations[0].detail


def test_gate_refuses_an_empty_marker() -> None:
    violations = find_violations("Dear {{}}:\n")
    assert [v.rule for v in violations] == ["marker-syntax"]
    assert "empty marker" in violations[0].detail


def test_gate_refuses_a_nested_marker() -> None:
    violations = find_violations("{{FILL: {{FILL: name}} | record}}\n")
    assert "marker-syntax" in [v.rule for v in violations]


def test_unclosed_marker_does_not_hide_the_case_content_after_it() -> None:
    """An unterminated '{{' yields no span. If it swallowed the rest of the
    document as one giant marker, the defect being reported would conceal every
    case figure behind it."""
    rules = _rules("{{FILL: name\n\nClaim number 4471902.\n")
    assert "marker-syntax" in rules
    assert "case-content" in rules


def test_gate_refuses_an_em_dash() -> None:
    violations = find_violations("The demand is open for acceptance — then it lapses.\n")
    assert [v.rule for v in violations] == ["em-dash"]


def test_gate_refuses_an_html_comment() -> None:
    """Drafting gate 9: an HTML comment renders as nothing, so a reservation
    hidden in one is a reservation the reviewing attorney never sees."""
    violations = find_violations("# Liability\n\n<!-- GUIDANCE: build from the report. -->\n")
    assert [v.rule for v in violations] == ["html-comment"]
    assert violations[0].line == 3


def test_case_content_inside_a_comment_is_not_reported_twice() -> None:
    """The comment is already a violation and is destined for deletion. Scanning
    inside it would report the same removal twice and overstate how much is
    wrong. It can never turn a refusal into a pass: the document is refused
    either way."""
    violations = find_violations("<!-- GUIDANCE: e.g. 2024-03-01, claim 4471902 -->\n")
    assert [v.rule for v in violations] == ["html-comment"]


def test_gate_lists_every_violation_not_just_the_first() -> None:
    markdown = (
        "# Demand\n"
        "<!-- GUIDANCE: fill this in -->\n"
        "Date of loss: 2024-03-01.\n"
        "The offer — open for 30 days.\n"
        "Dear {{}}:\n"
        "{{FILL: unclosed\n"
    )
    with pytest.raises(TemplateContentRefused) as exc:
        check_template_content(markdown)
    rules = sorted({v.rule for v in exc.value.violations})
    assert rules == ["case-content", "em-dash", "html-comment", "marker-syntax"]
    # the raised message carries the whole list, so one call tells the whole truth
    assert str(exc.value).count("line ") == len(exc.value.violations)
    assert len(exc.value.violations) >= 5


# ---- The gate, against the real authored skeletons -------------------------


_SKELETON_DIR = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "skeletons"


@pytest.mark.parametrize(
    "skeleton",
    ["demand-skeleton.md", "discovery-response-shell.md", "mediation-brief-skeleton.md"],
)
def test_shipped_skeletons_carry_no_case_content(skeleton: str) -> None:
    """The falsifier that shaped this rule. The three authored skeletons are
    dense in statutory citations, code sections, and statutory periods; an
    any-digit rule refused all three (68 case-content hits across them) and would
    have made the gate unusable against the firm's own templates. Every one of
    those hits must now be gone.

    Their HTML-comment violations are correct and stay: GUIDANCE that renders as
    nothing is exactly what gate 9 bans, so these files need a comment pass
    before they can be rendered as .docx templates."""
    path = _SKELETON_DIR / skeleton
    assert path.exists(), f"skeleton moved: {path}"
    violations = find_violations(path.read_text())
    case_content = [v for v in violations if v.rule == "case-content"]
    assert case_content == [], "\n".join(str(v) for v in case_content)
    assert {v.rule for v in violations} <= {"html-comment"}


# ---- The renderer: round-trip ----------------------------------------------


def test_render_round_trips_headings_paragraphs_bullets_and_markers() -> None:
    text = _docx_text(render_markdown_to_docx(_CLEAN_SKELETON))
    lines = [ln.strip() for ln in text.splitlines()]

    # headings keep their text, without the markdown hashes
    assert "SKELETON: Demand Letter" in lines
    assert "I. Introduction" in lines
    assert "A. Reserved" in lines
    assert not any(ln.startswith("#") for ln in lines)

    # markers survive VERBATIM and render-visible
    assert "{{NOT IN RECORD: carrier claim number, searched claim correspondence}}" in lines
    assert "{{ATTORNEY: confirm settlement authority before transmission}}" in lines
    assert "{{FILL: enclosed records by provider | the documents attached}}" in lines
    assert "{{FILL: claimant name | matter record}}" in text
    assert "{{FILL: date of loss | traffic collision report}}" in text

    # inline emphasis is applied, not printed
    assert "Every figure traces to a bill or a lien in the file." in lines


def test_render_applies_heading_and_bullet_styles() -> None:
    import io

    from docx import Document

    doc = Document(io.BytesIO(render_markdown_to_docx(_CLEAN_SKELETON)))
    styles = {p.text: p.style.name for p in doc.paragraphs if p.text.strip()}
    assert styles["SKELETON: Demand Letter"] == "Heading 1"
    assert styles["I. Introduction"] == "Heading 2"
    assert styles["A. Reserved"] == "Heading 3"
    assert styles["{{FILL: enclosed records by provider | the documents attached}}"] == "List Bullet"


def test_render_never_lets_emphasis_parsing_eat_a_marker() -> None:
    """A marker containing an asterisk must survive byte-for-byte: the emphasis
    pass runs only outside marker spans."""
    markdown = "{{FILL: caption *as styled* in the file | matter record}} and **bold** after.\n"
    text = _docx_text(render_markdown_to_docx(markdown))
    assert "{{FILL: caption *as styled* in the file | matter record}}" in text
    assert "bold after." in text
    assert "**" not in text


def test_render_shows_unsupported_constructs_rather_than_dropping_them() -> None:
    """A construct this renderer does not understand is shown to the reader
    verbatim. Silent loss in a document an attorney reviews is the one
    unacceptable failure."""
    markdown = "#### Deep heading\n\n| Provider | Dates |\n\n---\n\n> quoted line\n"
    text = _docx_text(render_markdown_to_docx(markdown))
    for construct in ("#### Deep heading", "| Provider | Dates |", "---", "> quoted line"):
        assert construct in text


# ---- The tool: upload shape + return shape ---------------------------------


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
            return httpx.Response(202, json={"fileId": "file-42", "uploadUrl": _UPLOAD_URL})
        if str(request.url) == _UPLOAD_URL:
            return httpx.Response(200)
        return httpx.Response(200, json={"ok": True})

    return handler


def test_tool_runs_the_two_stage_upload_with_the_rendered_bytes(monkeypatch) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _mock_client(_handler(captured)))

    out = server.render_docx_template("m-1", "Demand Template", _CLEAN_SKELETON, folder_id="fld-3")

    import json as _json

    post = next(r for r in captured if r.method == "POST" and r.url.path.endswith("/documents/files"))
    assert post.url.path == "/matters/m-1/documents/files"
    assert _json.loads(post.content) == {"fileName": "Demand Template.docx", "folderId": "fld-3"}
    assert post.headers.get("x-api-key") == "apikey"  # metadata call IS authenticated
    assert post.headers.get("authorization") == "Bearer tok"

    put = next(r for r in captured if r.method == "PUT")
    assert str(put.url) == _UPLOAD_URL
    # the presigned PUT must carry an EMPTY content-type and NO app/bearer auth
    assert put.headers.get("content-type") == ""
    assert "x-api-key" not in put.headers
    assert "authorization" not in put.headers

    # the bytes on the wire are the exact bytes the tool rendered and hashed
    assert hashlib.sha256(put.content).hexdigest() == out["sha256"]
    assert len(put.content) == out["sizeBytes"]
    assert put.content.startswith(b"PK\x03\x04")  # a real docx zip
    assert "{{ATTORNEY: confirm settlement authority before transmission}}" in _docx_text(put.content)


def test_tool_base64_decodes_to_the_exact_rendered_bytes(monkeypatch) -> None:
    """The base64 is computed in TOOL code from bytes the model never saw (the
    #2055 carve-out). Prove the encode step is lossless end to end."""
    seen: dict = {}

    class _RecordingClient:
        def add_file(self, matter_id, file_name, data, *, folder_id=None):
            seen.update(matter_id=matter_id, file_name=file_name, data=data, folder_id=folder_id)
            return {"fileId": "f-9", "matterId": matter_id, "fileName": file_name, "uploaded": True}

    monkeypatch.setattr(server, "_get_client", lambda: _RecordingClient())
    out = server.render_docx_template("m-2", "Template.docx", _CLEAN_SKELETON)

    data = seen["data"]
    assert base64.b64decode(base64.b64encode(data), validate=True) == data
    assert hashlib.sha256(data).hexdigest() == out["sha256"]
    assert len(data) == out["sizeBytes"]
    assert _docx_text(data).splitlines()[0].strip() == "SKELETON: Demand Letter"
    assert seen["file_name"] == "Template.docx"  # already suffixed: not doubled
    assert seen["folder_id"] is None


def test_tool_return_shape(monkeypatch) -> None:
    class _FakeClient:
        def add_file(self, matter_id, file_name, data, *, folder_id=None):
            return {"fileId": "f-1", "matterId": matter_id, "fileName": file_name, "uploaded": True}

    monkeypatch.setattr(server, "_get_client", lambda: _FakeClient())
    out = server.render_docx_template("m-3", "Intake Checklist", _CLEAN_SKELETON)
    assert out["fileId"] == "f-1"
    assert out["fileName"] == "Intake Checklist.docx"
    assert out["refusals"] == []
    assert out["sizeBytes"] > 0
    assert len(out["sha256"]) == 64


def test_tool_refuses_without_building_a_client_or_uploading(monkeypatch) -> None:
    """A refusal reaches nothing. The gate runs before the client is built, so a
    bad template cannot leave a partial artifact on the matter."""

    def _boom():
        raise AssertionError("client must not be built when the gate refuses")

    monkeypatch.setattr(server, "_get_client", _boom)
    out = server.render_docx_template(
        "m-1",
        "Bad Template.docx",
        "# Demand\n\n<!-- GUIDANCE -->\nDate of loss: 2024-03-01.\n",
    )
    assert out["fileId"] is None
    assert out["sha256"] is None
    assert out["sizeBytes"] is None
    assert len(out["refusals"]) == 2
    assert any("html-comment" in r for r in out["refusals"])
    assert any("case-content" in r for r in out["refusals"])


# ---- Classification --------------------------------------------------------


def test_manifest_classifies_the_renderer_as_an_internal_write() -> None:
    """It writes a template into the firm's own record and sends nothing outside
    the firm. The overlay's shared/action_classes.py must carry the matching
    mcp_smokeball_render_docx_template entry (coordinated change, noted in the
    manifest header) or the tool is unreachable at runtime."""
    manifest = tomllib.loads((Path(__file__).resolve().parents[1] / "manifest.toml").read_text())
    assert manifest["connector"]["tool_classes"]["render_docx_template"] == "internal_write"


def test_tool_is_on_the_served_surface() -> None:
    tool = next(t for t in server.server.tool_surface() if t.name == "render_docx_template")
    assert set(tool.inputSchema.get("required", [])) == {
        "matter_id",
        "file_name",
        "skeleton_markdown",
    }
    # the model only ever sees the description, so the refusal contract has to be
    # THERE: it must know the gate refuses rather than repairs, and that
    # materialization is async.
    description = tool.description or ""
    assert "refuses" in description
    assert "ASYNCHRONOUS" in description
