"""A Graph reply carrying a firm's own matter files to its own staff (ADR 0102).

Pins: the broker's own spool (chunked intake, hashed by the broker, owner-only,
no traversal), the reply verb's file fences (authored, firm staff only, no
device redirect, copies filtered, PDFs only, re-hashed, active content
refused), and the draft path (createReply on the read credential, files
attached, the draft read back and refused on any mismatch, deleted when
refused, sent on the send credential).
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import attachment_intake as ai
from workspace_broker import msgraph_reply_files as mrf
from workspace_broker.msgraph_ops import MsGraphOps, MsGraphRefused

MAILBOX = "operator@firm.example"
STAFF = "attorney@firm.example"
YAML = f"""
connectors:
  Email:
    adapter: msgraph
    enabled: true
    msgraph_auth:
      tenant_id: 't'
      client_id: 'c'
      mailbox: {MAILBOX}
scope:
  inbound_allow_from:
    - '@firm.example'
    - outsider@else.example
  outbound_roster:
    - address: client@firm.example
      class: client
matter_file_delivery:
  recipients:
    - '@firm.example'
"""

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\nstream\n/JS junk inside image bytes\nendstream\n%%EOF"


@pytest.fixture
def spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "broker-spool"
    monkeypatch.setenv("SMD_BROKER_ATTACHMENT_SPOOL", str(base))
    return base


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _put_whole(base: Path, data: bytes, chunk: int = 7) -> dict:
    handle = None
    offset = 0
    while True:
        piece = data[offset : offset + chunk]
        final = offset + len(piece) >= len(data)
        payload = {"offset": offset, "chunk_b64": _b64(piece), "final": final}
        if handle:
            payload["handle"] = handle
        if final:
            payload["sha256"] = hashlib.sha256(data).hexdigest()
        out = ai.put(base, payload)
        handle = out["handle"]
        offset += len(piece)
        if final:
            return out


# ---- the broker's spool -----------------------------------------------------


def test_chunks_assemble_and_the_broker_hashes_the_whole(spool: Path) -> None:
    out = _put_whole(spool, PDF)
    assert out["sha256"] == hashlib.sha256(PDF).hexdigest() and out["size"] == len(PDF)
    assert ai.read_handle(spool, out["handle"], out["sha256"]) == PDF
    assert (spool / f"{out['handle']}.bin").stat().st_mode & 0o077 == 0
    assert spool.stat().st_mode & 0o077 == 0


def test_a_close_with_the_wrong_hash_keeps_nothing(spool: Path) -> None:
    first = ai.put(spool, {"offset": 0, "chunk_b64": _b64(PDF), "final": False})
    with pytest.raises(ai.IntakeRefused, match="does not match"):
        ai.put(
            spool,
            {"handle": first["handle"], "offset": len(PDF), "chunk_b64": _b64(b"x"), "final": True, "sha256": "0" * 64},
        )
    assert list(spool.glob(f"{first['handle']}*")) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"offset": 3, "chunk_b64": _b64(b"abc"), "final": False},  # a new file starts at 0
        {"offset": 0, "chunk_b64": "not base64!", "final": False},
        {"offset": 0, "chunk_b64": "", "final": False},
        {"offset": -1, "chunk_b64": _b64(b"a"), "final": False},
        {"offset": 0, "chunk_b64": _b64(b"a"), "final": True},  # final with no sha256
        {"handle": "../../etc/passwd", "offset": 0, "chunk_b64": _b64(b"a"), "final": False},
        "not an object",
    ],
)
def test_malformed_chunks_are_refused(spool: Path, payload) -> None:
    with pytest.raises(ai.IntakeRefused):
        ai.put(spool, payload)


def test_chunks_go_in_order_once_each(spool: Path) -> None:
    first = ai.put(spool, {"offset": 0, "chunk_b64": _b64(b"abc"), "final": False})
    with pytest.raises(ai.IntakeRefused, match="in order"):
        ai.put(spool, {"handle": first["handle"], "offset": 0, "chunk_b64": _b64(b"abc"), "final": False})


def test_the_file_bound_is_enforced(spool: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai, "MAX_FILE_BYTES", 5)
    with pytest.raises(ai.IntakeRefused, match="over"):
        ai.put(spool, {"offset": 0, "chunk_b64": _b64(b"123456"), "final": False})


def test_a_symlinked_entry_is_never_read(spool: Path, tmp_path: Path) -> None:
    out = _put_whole(spool, PDF)
    path = spool / f"{out['handle']}.bin"
    path.unlink()
    (tmp_path / "x").write_bytes(PDF)
    path.symlink_to(tmp_path / "x")
    with pytest.raises(ai.IntakeRefused):
        ai.read_handle(spool, out["handle"], out["sha256"])


def test_write_and_read_spooled_for_root_side_callers(spool: Path) -> None:
    out = ai.write_spooled(PDF, "flyer.pdf")
    assert ai.read_spooled(out["handle"]) == PDF and out["sha256"] == hashlib.sha256(PDF).hexdigest()
    with pytest.raises(ai.IntakeRefused):
        ai.read_spooled("f" * 32)


def test_a_handle_whose_bytes_changed_is_refused(spool: Path) -> None:
    out = _put_whole(spool, PDF)
    (spool / f"{out['handle']}.bin").write_bytes(PDF + b"x")
    with pytest.raises(ai.IntakeRefused, match="does not match"):
        ai.read_handle(spool, out["handle"], out["sha256"])


# ---- the reply verb ---------------------------------------------------------


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._d = data

    def read(self) -> bytes:
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


class DraftGraph:
    """createReply, PATCH, attachments, the read-back, send and Sent Items."""

    def __init__(self, sender: str = STAFF, cc: list[str] | None = None) -> None:
        self.sender = sender
        self.source_cc = cc or []
        self.calls: list[tuple[str, str, dict | None, str]] = []
        self.draft: dict = {}
        self.attachments: list[dict] = []
        self.deleted = False
        self.sent = False
        self.tamper: str = ""

    def __call__(self, request, timeout=None):
        url, method = request.full_url, request.method
        body = json.loads(request.data.decode()) if request.data and not url.endswith("/token") else None
        auth = request.get_header("Authorization") or ""
        self.calls.append((method, url, body, auth))
        if url.endswith("/token"):
            cid = "read" if b"cid-read" in request.data else "send"
            return _Resp(json.dumps({"access_token": f"tok-{cid}", "expires_in": 3600}).encode())
        if "/createReply" in url:
            self.draft = {
                "id": "DRAFT1",
                "toRecipients": [{"emailAddress": {"address": self.sender}}],
                "ccRecipients": [],
                "bccRecipients": [],
                "body": {
                    "content": (body.get("message", {}).get("body", {}) or {}).get("content") or body.get("comment", "")
                },
            }
            return _Resp(json.dumps(self.draft).encode())
        if method == "PATCH":
            self.draft.update(body)
            if self.tamper == "cc":
                self.draft["ccRecipients"] = [{"emailAddress": {"address": "evil@else.example"}}]
            return _Resp(b"{}")
        if method == "POST" and url.endswith("/attachments"):
            raw = base64.b64decode(body["contentBytes"])
            if self.tamper == "bytes":
                raw += b"x"
            self.attachments.append(
                {
                    "id": f"A{len(self.attachments)}",
                    "name": body["name"],
                    "contentType": body["contentType"],
                    "raw": raw,
                }
            )
            return _Resp(b"{}")
        if method == "GET" and url.endswith("/$value"):
            aid = url.rsplit("/attachments/", 1)[1].split("/")[0]
            return _Resp(next(a["raw"] for a in self.attachments if a["id"] == aid))
        if method == "GET" and "/messages/DRAFT1/attachments" in url:
            return _Resp(
                json.dumps({"value": [{k: v for k, v in a.items() if k != "raw"} for a in self.attachments]}).encode()
            )
        if method == "GET" and "/messages/DRAFT1" in url:
            return _Resp(json.dumps(self.draft).encode())
        if method == "DELETE":
            self.deleted = True
            return _Resp(b"")
        if method == "POST" and url.endswith("/DRAFT1/send"):
            self.sent = True
            return _Resp(b"")
        if method == "GET" and "/mailFolders/sentitems/messages" in url:
            return _Resp(json.dumps({"value": []}).encode())
        if method == "GET" and "/messages/" in url:  # the source message
            return _Resp(
                json.dumps(
                    {
                        "from": {"emailAddress": {"address": self.sender}},
                        "toRecipients": [{"emailAddress": {"address": MAILBOX}}],
                        "ccRecipients": [{"emailAddress": {"address": a}} for a in self.source_cc],
                    }
                ).encode()
            )
        if method == "POST" and url.endswith("/reply"):
            raise AssertionError("a files reply must never use the bare /reply verb")
        raise AssertionError(f"unexpected {method} {url}")


def _ops(tmp_path: Path, graph: DraftGraph, yaml_text: str = YAML) -> MsGraphOps:
    customer = tmp_path / "customer.yaml"
    customer.write_text(yaml_text)
    send = tmp_path / "send.json"
    send.write_text(json.dumps({"tenant_id": "t", "client_id": "cid-send", "client_secret": "s"}))
    read = tmp_path / "read.json"
    read.write_text(json.dumps({"tenant_id": "t", "client_id": "cid-read", "client_secret": "r"}))
    return MsGraphOps(send, customer, read_credential_path=read, opener=graph, sleep=lambda _s: None)


def _files(spool: Path, *datas: bytes) -> list[dict]:
    out = []
    for i, data in enumerate(datas):
        put = _put_whole(spool, data, chunk=7 if len(data) < 4096 else 1024 * 1024)
        out.append(
            {"name": f"R{i}.pdf", "content_type": "application/pdf", "handle": put["handle"], "sha256": put["sha256"]}
        )
    return out


def _reply(ops: MsGraphOps, attachments: list[dict], **extra) -> dict:
    return ops.reply({"message_id": "SRC1", "comment": "Here are the records.", "attachments": attachments, **extra})


def test_staff_get_their_files_through_a_verified_draft(tmp_path: Path, spool: Path) -> None:
    graph = DraftGraph(cc=["paralegal@firm.example", "client@firm.example", "outsider@else.example"])
    out = _reply(_ops(tmp_path, graph), _files(spool, PDF, PDF + b"2"))
    assert graph.sent and not graph.deleted
    assert [a["raw"] for a in graph.attachments] == [PDF, PDF + b"2"]
    # Only firm staff are copied: the typed client and the outsider are left off.
    assert [c["emailAddress"]["address"] for c in graph.draft["ccRecipients"]] == ["paralegal@firm.example"]
    # The row's hashes ride the caller's audit_extra, which the broker accepts
    # only because it refused to send unless its own copy hashed to them.
    assert out["recipients"] == [STAFF, "paralegal@firm.example"]
    # The draft is built and read on the READ app; only the send uses the SEND app.
    send_calls = [c for c in graph.calls if c[3] == "Bearer tok-send"]
    assert [c[1].rsplit("/", 1)[-1] for c in send_calls] == ["send"]


@pytest.mark.parametrize(
    ("sender", "fragment"),
    [("outsider@else.example", "firm's own staff"), ("client@firm.example", "firm's own staff")],
)
def test_files_never_go_outside_the_firm(tmp_path: Path, spool: Path, sender: str, fragment: str) -> None:
    graph = DraftGraph(sender=sender)
    with pytest.raises(MsGraphRefused, match=fragment):
        _reply(_ops(tmp_path, graph), _files(spool, PDF))
    assert not graph.sent and not graph.draft


def test_an_unauthored_seat_sends_no_file(tmp_path: Path, spool: Path) -> None:
    graph = DraftGraph()
    with pytest.raises(MsGraphRefused, match="not authored"):
        _reply(_ops(tmp_path, graph, YAML.split("matter_file_delivery")[0]), _files(spool, PDF))
    assert not graph.calls


def test_a_device_redirect_never_carries_files(tmp_path: Path, spool: Path) -> None:
    yaml_text = YAML.replace(
        "  outbound_roster:",
        "  admins:\n    - office@firm.example\n  device_senders:\n    - address: scanner@firm.example\n      replies_to: office@firm.example\n  outbound_roster:",
    )
    graph = DraftGraph(sender="scanner@firm.example")
    with pytest.raises(MsGraphRefused, match="stand-in"):
        _reply(_ops(tmp_path, graph, yaml_text), _files(spool, PDF), to="office@firm.example")
    assert not graph.sent


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda a: a.update(content_type="application/zip"), "only plainly named PDFs"),
        (lambda a: a.update(name="../x.pdf"), "only plainly named PDFs"),
        (lambda a: a.update(sha256="0" * 64), "does not match"),
        (lambda a: a.update(handle="0" * 32), "spool"),
        (lambda a: a.update(extra="x"), "exactly name"),
    ],
)
def test_malformed_descriptors_refuse_the_whole_reply(tmp_path: Path, spool: Path, mutate, fragment: str) -> None:
    files = _files(spool, PDF)
    mutate(files[0])
    graph = DraftGraph()
    with pytest.raises(MsGraphRefused, match=fragment):
        _reply(_ops(tmp_path, graph), files)
    assert not graph.calls


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        (b"PK\x03\x04 not a pdf", "not a PDF"),
        (b"%PDF-1.4 /Encrypt << >>", "encrypted"),
        (b"%PDF-1.4 << /OpenAction << /S /JavaScript /JS (x) >> >>", "scripts"),
    ],
)
def test_the_broker_wall_refuses_bad_pdfs(tmp_path: Path, spool: Path, data: bytes, fragment: str) -> None:
    with pytest.raises(MsGraphRefused, match=fragment):
        _reply(_ops(tmp_path, DraftGraph()), _files(spool, data))


def test_a_name_inside_stream_data_is_not_active_content(tmp_path: Path, spool: Path) -> None:
    """A scanned page's image bytes are arbitrary; the wall reads outside streams."""
    graph = DraftGraph()
    _reply(_ops(tmp_path, graph), _files(spool, PDF))
    assert graph.sent


def test_too_many_or_too_big_refuse(tmp_path: Path, spool: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MsGraphRefused, match="at most 3"):
        _reply(_ops(tmp_path, DraftGraph()), _files(spool, PDF, PDF + b"1", PDF + b"2", PDF + b"3"))
    monkeypatch.setattr(mrf, "MAX_TOTAL_BYTES", len(PDF) + 1)
    with pytest.raises(MsGraphRefused, match="together"):
        _reply(_ops(tmp_path, DraftGraph()), _files(spool, PDF, PDF + b"1"))
    files = _files(spool, PDF)
    files.append(dict(files[0]))
    monkeypatch.setattr(mrf, "MAX_TOTAL_BYTES", 10**9)
    with pytest.raises(MsGraphRefused, match="share a name"):
        _reply(_ops(tmp_path, DraftGraph()), files)


@pytest.mark.parametrize(("tamper", "fragment"), [("cc", "copies"), ("bytes", "files did not read back")])
def test_a_draft_that_reads_back_wrong_is_deleted_not_sent(
    tmp_path: Path, spool: Path, tamper: str, fragment: str
) -> None:
    graph = DraftGraph()
    graph.tamper = tamper
    with pytest.raises(mrf.FilesRefused, match=fragment):
        _reply(_ops(tmp_path, graph), _files(spool, PDF))
    assert graph.deleted and not graph.sent


def test_a_large_file_goes_through_the_shared_upload_helper(
    tmp_path: Path, spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mrf, "INLINE_MAX_BYTES", 10)
    calls: list[tuple] = []
    graph = DraftGraph()

    def fake_upload(ops, draft_id, name, content_type, raw):
        calls.append((draft_id, name, content_type, len(raw)))
        graph.attachments.append({"id": "U0", "name": name, "contentType": content_type, "raw": raw})

    fake = type(sys)("workspace_broker.msgraph_upload")
    fake.upload_large_attachment = fake_upload
    monkeypatch.setitem(sys.modules, "workspace_broker.msgraph_upload", fake)
    _reply(_ops(tmp_path, graph), _files(spool, PDF))
    assert calls == [("DRAFT1", "R0.pdf", "application/pdf", len(PDF))] and graph.sent


def test_a_refused_send_deletes_the_draft(tmp_path: Path, spool: Path) -> None:
    graph = DraftGraph()
    real = graph.__call__

    def refuse_send(request, timeout=None):
        if request.full_url.endswith("/DRAFT1/send"):
            raise urllib.error.HTTPError(request.full_url, 403, "no", {}, None)  # type: ignore[arg-type]
        return real(request, timeout)

    ops = _ops(tmp_path, graph)
    ops._opener = refuse_send
    with pytest.raises(Exception):
        _reply(ops, _files(spool, PDF))
    assert graph.deleted


def test_a_reply_without_files_is_unchanged(tmp_path: Path) -> None:
    class Plain(DraftGraph):
        def __call__(self, request, timeout=None):
            if request.full_url.endswith("/reply"):
                self.calls.append((request.method, request.full_url, None, ""))
                return _Resp(b"")
            return super().__call__(request, timeout)

    graph = Plain()
    out = _ops(tmp_path, graph).reply({"message_id": "SRC1", "comment": "Hello."})
    assert "attachment_sha256" not in out and any(c[1].endswith("/reply") for c in graph.calls)


def test_the_attachment_put_verb_is_gateway_only_and_answers_ok(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from workspace_broker import verbs

    verb = verbs.TABLE["attachment_put"]
    assert verb.auth == frozenset({verbs.GATEWAY})
    broker = SimpleNamespace(audit_db_path=str(tmp_path / "audit.db"))
    data = b"%PDF-1.4 x"
    request = {
        "action": "attachment_put",
        "payload": {"offset": 0, "chunk_b64": _b64(data), "final": True, "sha256": hashlib.sha256(data).hexdigest()},
    }
    out = verb.handler(broker, "attachment_put", request, 1, 0)
    assert out["ok"] is True and ai.HANDLE_RE.fullmatch(out["handle"])
    assert (tmp_path / "attachment-spool" / f"{out['handle']}.bin").read_bytes() == data
    bad = {"payload": {"offset": 0, "chunk_b64": "", "final": False}}
    with pytest.raises(ValueError, match="attachment refused"):
        verb.handler(broker, "attachment_put", bad, 1, 0)


class _Upload:
    """A response with a status and headers, as Graph's upload PUT answers."""

    def __init__(self, status: int, headers: dict[str, str], data: bytes = b"") -> None:
        self.status, self.headers, self._d = status, headers, data

    def read(self) -> bytes:
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


def test_a_large_scan_goes_through_the_real_upload_session_and_is_verified(
    tmp_path: Path, spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 6.67 MB-record case: over 3 MiB, so ADR 0101's upload session carries it."""
    from workspace_broker import msgraph_upload

    monkeypatch.setattr(msgraph_upload, "CHUNK_BYTES", 1024 * 1024)
    big = (
        b"%PDF-1.4\n1 0 obj << /Length 9 >>\nstream\n" + b"\x00\xff/JS " * (700 * 1024) + b"\nendstream\nendobj\n%%EOF"
    )
    assert len(big) > 3 * 1024 * 1024
    graph = DraftGraph()
    received = bytearray()
    puts: list[str] = []
    real = graph.__call__

    def opener(request, timeout=None):
        url = request.full_url
        if url.endswith("/createUploadSession"):
            graph.calls.append(("POST", url, None, request.get_header("Authorization") or ""))
            return _Upload(201, {}, json.dumps({"uploadUrl": "https://outlook.office.com/up/1"}).encode())
        if url.startswith("https://outlook.office.com/up/"):
            assert request.get_header("Authorization") is None
            puts.append(request.get_header("Content-range") or "")
            received.extend(request.data)
            if len(received) < len(big):
                return _Upload(200, {})
            graph.attachments.append(
                {"id": "U1", "name": "R0.pdf", "contentType": "application/pdf", "raw": bytes(received)}
            )
            return _Upload(201, {"Location": "https://graph.microsoft.com/v1.0/x/Attachments('U1')"})
        return real(request, timeout)

    ops = _ops(tmp_path, graph)
    ops._opener = opener
    _reply(ops, _files(spool, big))
    assert bytes(received) == big and len(puts) == -(-len(big) // (1024 * 1024)) > 1
    assert graph.sent and not graph.deleted
