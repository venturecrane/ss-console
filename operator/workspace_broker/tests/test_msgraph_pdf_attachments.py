"""PDFs over the Graph send verb (A&P's seat sends mail through Microsoft Graph).

The Graph verb carries the same set the AgentMail verb does
(``agentmail_attachments.validated``): up to three files, PDFs and at most one
workbook. It differs only in the total (``msgraph_attachments.MAX_TOTAL_BYTES``)
and in the inline request bound Graph enforces. Each refusal here has a
falsifier beside it, so the refusal is the rule and not some other one.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from workspace_broker.msgraph_attachments import (
    MAX_SEND_REQUEST_BYTES,
    MAX_TOTAL_BYTES,
    PDF_CONTENT_TYPE,
)
from workspace_broker.msgraph_ops import MsGraphRefused
from workspace_broker.tests import test_msgraph_send as mg


def _pdf_bytes(tag: str, size: int | None = None) -> bytes:
    head = b"%PDF-1.4\n% " + tag.encode() + b"\n"
    return head if size is None else head + b"x" * (size - len(head))


def _pdf(tag: str, size: int | None = None, name: str | None = None) -> dict:
    raw = _pdf_bytes(tag, size)
    return {
        "name": name or f"Record {tag}.pdf",
        "content_type": PDF_CONTENT_TYPE,
        "content_b64": base64.b64encode(raw).decode("ascii"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _js_pdf() -> dict:
    raw = b"%PDF-1.4 /JS (x)"
    return {
        **_pdf("A"),
        "content_b64": base64.b64encode(raw).decode("ascii"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def test_two_pdfs_and_a_workbook_reach_graph_and_the_row_hashes_them(tmp_path: Path) -> None:
    http = mg.FakeGraph()
    broker = mg._broker(tmp_path, http)
    files = [_pdf("A"), _pdf("B"), mg._attachment()]
    broker.handle(
        {"action": "msgraph_send", "payload": mg._attach_payload(files)},
        peer_pid=mg.GATEWAY_PID,
        peer_uid=mg.AGENT_UID,
    )
    sent = http.graph_posts()[0][2]["message"]["attachments"]
    assert [a["name"] for a in sent] == ["Record A.pdf", "Record B.pdf", "Statute watch October 2026.xlsx"]
    assert all(a["@odata.type"] == "#microsoft.graph.fileAttachment" for a in sent)
    assert [a["contentType"] for a in sent[:2]] == [PDF_CONTENT_TYPE, PDF_CONTENT_TYPE]
    assert base64.b64decode(sent[0]["contentBytes"]) == _pdf_bytes("A")
    # The broker's own hash of each file, in order, with no caller stamp given.
    assert mg._meta(broker)["attachment_sha256"] == ",".join(f["sha256"] for f in files)


_BAD_SETS = {
    "a fourth file": lambda: [_pdf("A"), _pdf("B"), _pdf("C"), _pdf("D")],
    "a second workbook beside a pdf": lambda: [_pdf("A"), mg._attachment(), mg._attachment(name="Second.xlsx")],
    "a second pdf with the same name": lambda: [_pdf("A"), _pdf("B", name="Record A.pdf")],
    "a non-pdf type": lambda: [{**_pdf("A"), "content_type": "application/msword"}],
    "pdf name on non-pdf bytes": lambda: [mg._attachment(name="Record.pdf", content_type=PDF_CONTENT_TYPE)],
    "a pdf naming javascript": lambda: [_js_pdf()],
    "a pdf sha mismatch": lambda: [{**_pdf("A"), "sha256": "0" * 64}],
    "over the graph total": lambda: [_pdf("A", 1_400_000), _pdf("B", 1_400_000)],
}


@pytest.mark.parametrize("case", sorted(_BAD_SETS))
def test_a_set_outside_the_limits_refuses_the_whole_graph_send(tmp_path: Path, case: str) -> None:
    http = mg.FakeGraph()
    with pytest.raises(MsGraphRefused) as excinfo:
        mg._ops(tmp_path, http).send(mg._attach_payload(_BAD_SETS[case]()))
    assert http.graph_posts() == []
    assert str(excinfo.value).startswith("attachment refused: ")


def test_the_graph_total_is_inclusive(tmp_path: Path) -> None:
    """Falsifier for "over the graph total": exactly the bound sends."""
    http = mg.FakeGraph()
    half = MAX_TOTAL_BYTES // 2
    mg._ops(tmp_path, http).send(mg._attach_payload([_pdf("A", half), _pdf("B", half)]))
    assert len(http.graph_posts()) == 1


def test_a_body_that_pushes_the_request_over_graphs_bound_is_refused(tmp_path: Path) -> None:
    """Files within the total plus a large html body, over the ~4 MB inline
    request: refused by name before the POST, never left for Graph to 413."""
    half = MAX_TOTAL_BYTES // 2
    payload = {
        **mg._attach_payload([_pdf("A", half), _pdf("B", half)]),
        "html": "<p>" + "x" * 600_000 + "</p>",
    }
    http = mg.FakeGraph()
    with pytest.raises(MsGraphRefused, match="inline request bound") as excinfo:
        mg._ops(tmp_path, http).send(payload)
    assert str(excinfo.value).startswith("attachment refused: ")
    assert http.graph_posts() == []
    # The same body with no files is not this check's business.
    http = mg.FakeGraph()
    mg._ops(tmp_path, http).send({k: v for k, v in payload.items() if k != "attachments"})
    assert len(http.graph_posts()) == 1
    assert MAX_SEND_REQUEST_BYTES == 4_000_000
