"""Files over the Graph send verb (A&P's seat sends mail through Microsoft Graph).

The Graph verb carries the same set the AgentMail verb does
(``agentmail_attachments.validated``): up to three files, PDFs and at most one
workbook, under Graph's own caps (25 MiB per PDF and together). Files that fit
one ``sendMail`` request go inline, exactly as before; anything larger goes
through a draft, upload sessions, a verifying re-read and ``/send``
(``msgraph_upload``). The draft path is tested against a MODELLED mailbox: the
fake stores the draft, its attachments and the uploaded ranges, and answers the
verifying read from that store, so a tamper between build and send is visible
the way it would be on Graph.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from workspace_broker import msgraph_attachments, msgraph_upload
from workspace_broker.msgraph_attachments import (
    MAX_INLINE_TOTAL_BYTES,
    MAX_PDF_BYTES,
    MAX_SEND_REQUEST_BYTES,
    MAX_TOTAL_BYTES,
    PDF_CONTENT_TYPE,
)
from workspace_broker.msgraph_ops import MsGraphRefused, MsGraphTransportError
from workspace_broker.tests import test_msgraph_send as mg

MiB = 1024 * 1024


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


class _Resp:
    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None) -> None:
        self._body, self.status, self.headers = body, status, headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _http_error(url: str, status: int, code: str = "") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"code": code}}).encode() if code else b""
    return urllib.error.HTTPError(url, status, "no", {}, io.BytesIO(body))  # type: ignore[arg-type]


class MailboxFake(mg.FakeGraph):
    """FakeGraph plus a modelled Drafts folder for the draft path."""

    UPLOAD = "https://outlook.office.com/api/v2.0/Users('x')/Messages('D1')/AttachmentSessions('S{n}')?authtoken=t"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.drafts: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.sent: list[dict] = []
        self.deleted: list[str] = []
        self.draft_auth: list[tuple[str, str, str]] = []
        #: Called with the draft after the files are on it and before the
        #: verifying read: the agent's read credential PATCHing it, modelled.
        self.tamper = None
        #: The same, landing before the verifier's first attachment listing.
        self.tamper_files_early = None
        self.upload_url_override: str | None = None
        self.send_status: int | None = None
        self.send_code = ""

    def __call__(self, request, timeout=None):
        url, method = request.full_url, request.method
        m = re.search(
            r"/messages(?:/(?!send\b)([^/?]+))?(/attachments(?:/(?!createUploadSession)([^/?]+))?(/createUploadSession|/\$value)?)?(/send)?",
            url,
        )
        is_session = "AttachmentSessions" in url
        if not is_session and (m is None or url.endswith("/sendMail") or "mailFolders" in url):
            return super().__call__(request, timeout)
        auth = request.get_header("Authorization") or ""
        if is_session:
            return self._put(url, request)
        draft_id, attachments, attachment_id, tail, send = m.groups()
        self.draft_auth.append((method, url, auth))
        body = json.loads(request.data.decode()) if request.data else None
        if method == "POST" and draft_id is None:
            new_id = f"DRAFT{len(self.drafts) + 1}"
            self.drafts[new_id] = {"message": body, "files": []}
            return _Resp(json.dumps({"id": new_id}).encode(), 201)
        draft = self.drafts.get(draft_id or "")
        if draft is None:
            raise _http_error(url, 404, "ErrorItemNotFound")
        if method == "POST" and tail == "/createUploadSession":
            sid = f"S{len(self.sessions) + 1}"
            item = body["AttachmentItem"]
            self.sessions[sid] = {"draft": draft_id, "name": item["name"], "size": item["size"], "data": b""}
            upload = self.upload_url_override or self.UPLOAD.replace("S{n}", sid).replace("D1", draft_id)
            return _Resp(json.dumps({"uploadUrl": upload}).encode(), 201)
        if method == "POST" and send:
            self.draft_auth.append(("SEND", draft_id, auth))
            if self.send_status is not None:
                raise _http_error(url, self.send_status, self.send_code)
            self.sent.append(self.drafts.pop(draft_id))
            self.transmitted_headers.append(list(self.sent[-1]["message"].get("internetMessageHeaders") or []))
            return _Resp(b"", 202)
        if method == "POST" and attachments:
            draft["files"].append({"name": body["name"], "data": base64.b64decode(body["contentBytes"])})
            return _Resp(b"{}", 201)
        if method == "DELETE":
            self.deleted.append(draft_id)
            self.drafts.pop(draft_id, None)
            return _Resp(b"", 204)
        if method == "GET" and tail == "/$value":
            return _Resp(next(f["data"] for f in draft["files"] if str(id(f)) == attachment_id))
        if method == "GET" and attachments:
            if self.tamper_files_early is not None:
                self.tamper_files_early(draft)
                self.tamper_files_early = None
            # Ids are per stored object: replacing a file (Graph's only way to
            # change one) mints a new id, as on Graph.
            files = [{"id": str(id(f)), "name": f["name"], "size": len(f["data"]) + 174} for f in draft["files"]]
            return _Resp(json.dumps({"value": files}).encode())
        if method == "GET":
            # The verifying read starts here; a tamper lands just before it.
            if self.tamper is not None:
                self.tamper(draft)
                self.tamper = None
            return _Resp(json.dumps({**draft["message"], "isDraft": True}).encode())
        raise AssertionError(f"unmodelled {method} {url}")

    def _put(self, url: str, request) -> _Resp:
        sid = re.search(r"AttachmentSessions\('([^']+)'\)", url).group(1)
        session = self.sessions[sid]
        assert request.get_header("Authorization") is None, "an uploadUrl PUT must carry no token"
        start, end, total = map(int, re.match(r"bytes (\d+)-(\d+)/(\d+)", request.get_header("Content-range")).groups())
        assert start == len(session["data"]) and total == session["size"]
        assert end - start + 1 <= msgraph_upload.CHUNK_BYTES
        session["data"] += request.data
        if len(session["data"]) < total:
            return _Resp(json.dumps({"nextExpectedRanges": [f"{end + 1}-"]}).encode(), 200)
        self.drafts[session["draft"]]["files"].append({"name": session["name"], "data": session["data"]})
        location = (
            f"https://outlook.office.com/api/v2.0/Users('x')/Messages('{session['draft']}')/Attachments('ATT{sid}')"
        )
        return _Resp(b"", 201, {"Location": location})


def _send(tmp_path: Path, http, files: list[dict], **extra):
    broker = mg._broker(tmp_path, http)
    response = broker.handle(
        {"action": "msgraph_send", "payload": {**mg._attach_payload(files), **extra}},
        peer_pid=mg.GATEWAY_PID,
        peer_uid=mg.AGENT_UID,
    )
    return broker, response


# ---------------------------------------------------------------------------
# Inline: what fits one sendMail request
# ---------------------------------------------------------------------------


def test_two_pdfs_and_a_workbook_go_inline_and_the_row_hashes_them(tmp_path: Path) -> None:
    http = MailboxFake()
    files = [_pdf("A"), _pdf("B"), mg._attachment()]
    broker, _ = _send(tmp_path, http, files)
    [post] = [p for p in http.graph_posts() if p[1].endswith("/sendMail")]
    sent = post[2]["message"]["attachments"]
    assert [a["name"] for a in sent] == ["Record A.pdf", "Record B.pdf", "Statute watch October 2026.xlsx"]
    assert all(a["@odata.type"] == "#microsoft.graph.fileAttachment" for a in sent)
    assert base64.b64decode(sent[0]["contentBytes"]) == _pdf_bytes("A")
    assert http.drafts == {} and http.sent == []
    meta = mg._meta(broker)
    assert meta["attachment_sha256"] == ",".join(f["sha256"] for f in files)
    assert "graph_draft_id" not in meta


def test_exactly_the_inline_total_still_goes_inline(tmp_path: Path) -> None:
    http = MailboxFake()
    half = MAX_INLINE_TOTAL_BYTES // 2
    mg._ops(tmp_path, http).send(mg._attach_payload([_pdf("A", half), _pdf("B", half)]))
    assert [p for p in http.graph_posts() if p[1].endswith("/sendMail")] and http.sent == []


# ---------------------------------------------------------------------------
# Refusals: the same set rules as AgentMail, under Graph's caps
# ---------------------------------------------------------------------------

_BAD_SETS = {
    "a fourth file": lambda: [_pdf("A"), _pdf("B"), _pdf("C"), _pdf("D")],
    "a second workbook beside a pdf": lambda: [_pdf("A"), mg._attachment(), mg._attachment(name="Second.xlsx")],
    "a second pdf with the same name": lambda: [_pdf("A"), _pdf("B", name="Record A.pdf")],
    "a non-pdf type": lambda: [{**_pdf("A"), "content_type": "application/msword"}],
    "pdf name on non-pdf bytes": lambda: [mg._attachment(name="Record.pdf", content_type=PDF_CONTENT_TYPE)],
    "a pdf naming javascript": lambda: [_js_pdf()],
    "a pdf sha mismatch": lambda: [{**_pdf("A"), "sha256": "0" * 64}],
    "a pdf over 25 MiB": lambda: [_pdf("A", MAX_PDF_BYTES + 1)],
    "over 25 MiB together": lambda: [_pdf("A", 13 * MiB), _pdf("B", 13 * MiB)],
}


@pytest.mark.parametrize("case", sorted(_BAD_SETS))
def test_a_set_outside_the_limits_refuses_the_whole_graph_send(tmp_path: Path, case: str) -> None:
    http = MailboxFake()
    with pytest.raises(MsGraphRefused) as excinfo:
        mg._ops(tmp_path, http).send(mg._attach_payload(_BAD_SETS[case]()))
    assert http.graph_posts() == [] and http.drafts == {}
    assert str(excinfo.value).startswith("attachment refused: ")


def test_the_caps_are_25_mib() -> None:
    assert MAX_PDF_BYTES == MAX_TOTAL_BYTES == 25 * MiB
    assert MAX_SEND_REQUEST_BYTES == 4_000_000


# ---------------------------------------------------------------------------
# The draft path: larger than one request
# ---------------------------------------------------------------------------


def test_a_7_mb_scan_goes_by_draft_upload_session_and_send(tmp_path: Path) -> None:
    """The real shape: one 6.67 MB scan plus a small PDF."""
    http = MailboxFake()
    big, small = _pdf("SCAN", 6_670_000), _pdf("B")
    broker, response = _send(tmp_path, http, [big, small])
    assert response["ok"]
    [sent] = http.sent
    names = [f["name"] for f in sent["files"]]
    assert names == ["Record SCAN.pdf", "Record B.pdf"]
    assert sent["files"][0]["data"] == _pdf_bytes("SCAN", 6_670_000)
    assert [p for p in http.graph_posts() if p[1].endswith("/sendMail")] == []
    assert "attachments" not in sent["message"]
    # Built and read on the READ app; sent on the SEND app.
    create_auth = next(a for m, u, a in http.draft_auth if m == "POST" and u.endswith("/messages"))
    send_auth = next(a for m, _d, a in http.draft_auth if m == "SEND")
    assert create_auth and send_auth and create_auth != send_auth
    meta = mg._meta(broker)
    assert meta["graph_draft_id"] == "DRAFT1"
    assert meta["attachment_sha256"] == f"{big['sha256']},{small['sha256']}"
    assert "graph_draft_id" not in response, "the draft id never travels back to the agent"
    assert meta.get("vendor_message_id"), "the Sent Items lookup still runs"


def test_three_files_near_25_mib_reach_graph(tmp_path: Path) -> None:
    http = MailboxFake()
    files = [_pdf("A", 10 * MiB), _pdf("B", 10 * MiB), _pdf("C", 5 * MiB - 100)]
    _send(tmp_path, http, files)
    [sent] = http.sent
    assert [hashlib.sha256(f["data"]).hexdigest() for f in sent["files"]] == [f["sha256"] for f in files]
    assert len(http.sessions) == 3


def test_a_large_html_body_with_small_files_takes_the_draft_path(tmp_path: Path) -> None:
    """Within the inline total, but the whole request is over Graph's bound."""
    http = MailboxFake()
    half = MAX_INLINE_TOTAL_BYTES // 2
    payload = {**mg._attach_payload([_pdf("A", half), _pdf("B", half)]), "html": "<p>" + "x" * 600_000 + "</p>"}
    mg._ops(tmp_path, http).send(payload)
    assert len(http.sent) == 1 and http.sessions == {}


@pytest.mark.parametrize(
    "tamper, field",
    [
        (lambda d: d["message"]["toRecipients"].append({"emailAddress": {"address": "x@evil.example"}}), "to"),
        (lambda d: d["message"].__setitem__("bccRecipients", [{"emailAddress": {"address": "x@evil.example"}}]), "bcc"),
        (lambda d: d["message"].__setitem__("subject", "changed"), "subject"),
        (lambda d: d["message"]["body"].__setitem__("content", "changed"), "body"),
        (lambda d: d["files"].append({"name": "extra.pdf", "data": b"%PDF-x"}), "files"),
        (lambda d: d["files"].__setitem__(0, {**d["files"][0], "data": d["files"][0]["data"][:-1] + b"!"}), "files"),
    ],
)
def test_a_draft_changed_before_send_is_refused_deleted_and_audited(tmp_path: Path, tamper, field: str) -> None:
    http = MailboxFake()
    http.tamper = tamper
    with pytest.raises(MsGraphRefused, match=rf"draft changed before it was sent \([^)]*{field}"):
        _send(tmp_path, http, [_pdf("SCAN", 4 * MiB)])
    assert http.sent == [] and http.deleted == ["DRAFT1"]


def test_a_file_replaced_before_it_is_read_is_caught_by_its_hash(tmp_path: Path) -> None:
    http = MailboxFake()
    http.tamper_files_early = lambda d: d["files"].__setitem__(0, {**d["files"][0], "data": b"%PDF-swapped"})
    with pytest.raises(MsGraphRefused, match=r"draft changed before it was sent \(files\)"):
        _send(tmp_path, http, [_pdf("SCAN", 4 * MiB)])
    assert http.sent == [] and http.deleted == ["DRAFT1"]


def test_falsifier_an_untampered_draft_sends(tmp_path: Path) -> None:
    http = MailboxFake()
    http.tamper = lambda d: None
    _send(tmp_path, http, [_pdf("SCAN", 4 * MiB)])
    assert len(http.sent) == 1 and http.deleted == []


def test_a_refused_draft_writes_a_refusal_row(tmp_path: Path) -> None:
    http = MailboxFake()
    http.tamper = lambda d: d["message"].__setitem__("subject", "changed")
    broker = mg._broker(tmp_path, http)
    with pytest.raises(MsGraphRefused):
        broker.handle(
            {"action": "msgraph_send", "payload": mg._attach_payload([_pdf("SCAN", 4 * MiB)])},
            peer_pid=mg.GATEWAY_PID,
            peer_uid=mg.AGENT_UID,
        )
    meta = mg._meta(broker)
    assert meta["outcome"] == "refused" and meta["reason"].startswith("attachment refused: ")


def test_an_upload_url_on_another_host_is_refused_before_any_bytes_move(tmp_path: Path) -> None:
    http = MailboxFake()
    http.upload_url_override = "https://attacker.example/upload"
    with pytest.raises(MsGraphRefused, match="unexpected host"):
        _send(tmp_path, http, [_pdf("SCAN", 4 * MiB)])
    assert http.sent == [] and http.deleted == ["DRAFT1"]


def test_graph_refusing_the_send_for_size_is_a_plain_refusal(tmp_path: Path) -> None:
    http = MailboxFake()
    http.send_status, http.send_code = 413, "ErrorMessageSizeExceeded"
    with pytest.raises(MsGraphRefused, match="Graph refused the send: HTTP 413 ErrorMessageSizeExceeded"):
        _send(tmp_path, http, [_pdf("SCAN", 4 * MiB)])
    assert http.deleted == ["DRAFT1"]


def test_a_5xx_on_send_is_an_unknown_outcome_and_the_draft_is_left(tmp_path: Path) -> None:
    http = MailboxFake()
    http.send_status = 503
    with pytest.raises(MsGraphTransportError):
        mg._ops(tmp_path, http).send(mg._attach_payload([_pdf("SCAN", 4 * MiB)]))
    assert http.deleted == [] and "DRAFT1" in http.drafts


def test_the_draft_path_refuses_without_a_read_credential(tmp_path: Path) -> None:
    http = MailboxFake()
    with pytest.raises(MsGraphRefused, match="attachment refused"):
        mg._ops(tmp_path, http, with_read_credential=False).send(mg._attach_payload([_pdf("SCAN", 4 * MiB)]))
    assert http.drafts == {} and http.sent == []


def test_send_as_staff_never_builds_a_draft(tmp_path: Path) -> None:
    http = MailboxFake()
    with pytest.raises(MsGraphRefused, match="inline bound"):
        mg._ops(tmp_path, http).send_as_staff(
            mg._attach_payload([_pdf("SCAN", 4 * MiB)]), "christa@examplefirm.example"
        )
    assert http.drafts == {} and http.graph_posts() == []


def test_upload_large_attachment_chunks_and_returns_the_attachment_id(tmp_path: Path) -> None:
    """The shared helper, called directly as the reply path calls it."""
    http = MailboxFake()
    ops = mg._ops(tmp_path, http)
    http.drafts["D9"] = {"message": {}, "files": []}
    raw = _pdf_bytes("Z", 9 * MiB)
    assert msgraph_upload.upload_large_attachment(ops, "D9", "Z.pdf", PDF_CONTENT_TYPE, raw) == "ATTS1"
    assert http.drafts["D9"]["files"] == [{"name": "Z.pdf", "data": raw}]
    with pytest.raises(MsGraphRefused, match="under 3 MiB"):
        msgraph_upload.upload_large_attachment(ops, "D9", "s.pdf", PDF_CONTENT_TYPE, b"%PDF-small")


# ---------------------------------------------------------------------------
# Spooled files: {name, content_type, handle, sha256}
# ---------------------------------------------------------------------------


def _handle(tag: str, size: int) -> tuple[dict, bytes]:
    raw = _pdf_bytes(tag, size)
    return {
        "name": f"Record {tag}.pdf",
        "content_type": PDF_CONTENT_TYPE,
        "handle": hashlib.md5(raw).hexdigest(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }, raw


def test_a_spooled_file_is_read_rehashed_and_sent(tmp_path: Path, monkeypatch) -> None:
    entry, raw = _handle("SPOOL", 7 * MiB)
    monkeypatch.setattr(msgraph_attachments, "_read_spooled", lambda h: raw if h == entry["handle"] else b"")
    http = MailboxFake()
    _send(tmp_path, http, [entry])
    assert http.sent[0]["files"][0]["data"] == raw


def test_a_spooled_file_whose_bytes_do_not_match_refuses(tmp_path: Path, monkeypatch) -> None:
    entry, raw = _handle("SPOOL", 4 * MiB)
    monkeypatch.setattr(msgraph_attachments, "_read_spooled", lambda h: raw + b"tampered")
    http = MailboxFake()
    with pytest.raises(MsGraphRefused, match="sha256 does not match"):
        _send(tmp_path, http, [entry])
    assert http.drafts == {} and http.sent == []


def test_a_handle_on_a_broker_with_no_intake_refuses_by_name(tmp_path: Path) -> None:
    entry, _raw = _handle("SPOOL", 4 * MiB)
    http = MailboxFake()
    with pytest.raises(MsGraphRefused, match="spool"):
        _send(tmp_path, http, [entry])
    assert http.drafts == {}


def test_a_malformed_handle_refuses(tmp_path: Path, monkeypatch) -> None:
    entry, raw = _handle("SPOOL", 4 * MiB)
    monkeypatch.setattr(msgraph_attachments, "_read_spooled", lambda h: raw)
    http = MailboxFake()
    with pytest.raises(MsGraphRefused, match="not a spool handle"):
        _send(tmp_path, http, [{**entry, "handle": "../../etc/passwd"}])
