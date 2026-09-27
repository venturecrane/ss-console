"""Every write to the firm's Smokeball is recorded first, or it is not sent.

A&P, 2026-09-26: 52 documents filed on 31 matters by hand on 09-25 left no audit
row, because the script that filed them used this client directly. These tests
drive the real client against a fake Smokeball and a fake broker socket, and each
has a falsifier: remove the record.begin() call from client.request and the
refusal tests send the write anyway.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from typing import Any

import httpx
import pytest

from smokeball_connector import write_record as wr
from smokeball_connector.client import SmokeballClient


class _Broker:
    """A Unix-socket stand-in for the workspace broker that records rows."""

    def __init__(self, ok: bool = True) -> None:
        self.rows: list[dict[str, Any]] = []
        self.ok = ok
        self.path = os.path.join(tempfile.mkdtemp(prefix="wr", dir="/tmp"), "b.sock")
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            with conn:
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65_536)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf)
                self.rows.append(req["row"])
                reply = {"ok": True, "id": "r1"} if self.ok else {"ok": False, "error": "refused"}
                conn.sendall(json.dumps(reply).encode() + b"\n")


def _client(sent: list[httpx.Request]) -> SmokeballClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        return httpx.Response(200, json={"id": "task-123"})

    client = SmokeballClient.__new__(SmokeballClient)
    client.api_host = "https://api.test"
    client._account_id = None
    client._api_key = "k"
    client._token = "t"
    client._token_deadline = float("inf")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (wr.SOCKET_ENV, wr.ACTOR_ENV):
        monkeypatch.delenv(name, raising=False)
    # Off a seat: the fixed seat path must not exist for these tests to mean anything.
    monkeypatch.setattr(wr, "DEFAULT_SOCKET", "/tmp/wr-no-seat-here.sock")
    # This test process's parent is not a gateway.
    monkeypatch.setattr(wr, "_proc_cmdline", lambda pid: "python -m pytest")


def test_a_write_with_no_named_writer_is_refused_and_never_sent(monkeypatch):
    broker = _Broker()
    monkeypatch.setenv(wr.SOCKET_ENV, broker.path)
    sent: list[httpx.Request] = []
    with pytest.raises(wr.WriteNotRecorded, match="does not name who"):
        _client(sent).request("POST", "/tasks", json={"subject": "x"})
    assert sent == []
    assert broker.rows == []


def test_a_write_whose_row_the_broker_refuses_is_never_sent(monkeypatch):
    broker = _Broker(ok=False)
    monkeypatch.setenv(wr.SOCKET_ENV, broker.path)
    monkeypatch.setenv(wr.ACTOR_ENV, "seat-probe:scott@laptop")
    sent: list[httpx.Request] = []
    with pytest.raises(wr.WriteNotRecorded, match="Nothing was sent"):
        _client(sent).request("POST", "/tasks", json={"subject": "x"})
    assert sent == []


def test_a_write_with_an_unreachable_broker_is_never_sent(monkeypatch):
    monkeypatch.setenv(wr.SOCKET_ENV, "/tmp/wr-nothing-listens.sock")
    monkeypatch.setenv(wr.ACTOR_ENV, "seat-probe:scott@laptop")
    sent: list[httpx.Request] = []
    with pytest.raises(wr.WriteNotRecorded):
        _client(sent).request("DELETE", "/matters/0d3fffa2-03aa-4cf5-8e8c-2f77cb3c9d46/documents/files/f1")
    assert sent == []


def test_a_named_write_is_recorded_before_and_after(monkeypatch):
    broker = _Broker()
    monkeypatch.setenv(wr.SOCKET_ENV, broker.path)
    monkeypatch.setenv(wr.ACTOR_ENV, "seat-probe:scott@laptop")
    sent: list[httpx.Request] = []
    mid = "0d3fffa2-03aa-4cf5-8e8c-2f77cb3c9d46"
    result = _client(sent).request("POST", f"/matters/{mid}/documents/folders", json={"name": "MAIL"})
    assert result == {"id": "task-123"}
    assert len(sent) == 1
    intent, done = broker.rows
    assert intent["phase"] == "intent" and done["phase"] == "result"
    assert intent["actor"] == "seat-probe:scott@laptop"
    assert intent["matter_id"] == mid
    assert intent["body_sha256"] == wr.body_digest({"name": "MAIL"})
    assert intent["write_id"] == done["write_id"]
    assert done["status"] == 200 and done["returned_id"] == "task-123"


def test_reads_are_never_recorded_or_refused(monkeypatch):
    broker = _Broker()
    monkeypatch.setenv(wr.SOCKET_ENV, broker.path)
    sent: list[httpx.Request] = []
    _client(sent).request("GET", "/tasks")
    assert len(sent) == 1
    assert broker.rows == []


def test_off_a_seat_nothing_is_recorded_or_refused():
    sent: list[httpx.Request] = []
    _client(sent).request("POST", "/tasks", json={"subject": "x"})
    assert len(sent) == 1


def test_only_a_child_of_the_running_gateway_is_the_operator(monkeypatch):
    """At boot the gateway's pid still belongs to bootstrap.sh. A child of it is
    not the Operator, so it must name itself like any other writer."""
    monkeypatch.setattr(wr, "_proc_cmdline", lambda pid: "bash /app/bootstrap.sh")
    assert wr.resolve_actor() is None
    monkeypatch.setattr(wr, "_proc_cmdline", lambda pid: "/opt/hermes/.venv/bin/python /opt/hermes/.venv/bin/hermes -p operator gateway run")
    assert wr.resolve_actor() == "operator"


def test_the_operators_mcp_server_is_recorded_with_no_smd_environment(monkeypatch):
    """Hermes starts MCP servers with none of the SMD_* variables (pilot probe,
    2026-09-26). The seat's fixed socket path and /proc still find both the
    broker and the Operator. Falsifier: key either on the environment and this
    write goes out unrecorded."""
    broker = _Broker()
    monkeypatch.setattr(wr, "DEFAULT_SOCKET", broker.path)
    monkeypatch.setattr(wr, "_proc_cmdline", lambda pid: "hermes -p operator gateway run")
    sent: list[httpx.Request] = []
    _client(sent).request("POST", "/tasks", json={"subject": "x"})
    assert len(sent) == 1
    assert [r["phase"] for r in broker.rows] == ["intent", "result"]
    assert broker.rows[0]["actor"] == "operator"


def test_the_medchron_user_is_exempt_only_on_a_daemon_launched_job(monkeypatch):
    """A person running as medchron by hand has no marker and is refused like
    anyone unnamed. Falsifier: exempt on the uid alone and the hand-run write
    goes out with no row."""
    broker = _Broker()
    monkeypatch.setenv(wr.SOCKET_ENV, broker.path)
    monkeypatch.setattr(wr, "_user", lambda: "medchron")
    sent: list[httpx.Request] = []
    with pytest.raises(wr.WriteNotRecorded):
        _client(sent).request("POST", "/tasks", json={"subject": "x"})
    assert sent == []
    monkeypatch.setenv(wr.MEDCHRON_JOB_MARKER_ENV, "01JOB")
    _client(sent).request("POST", "/tasks", json={"subject": "x"})
    assert len(sent) == 1
    assert broker.rows == []


def test_a_document_upload_records_the_bytes_leg_too(monkeypatch):
    """The filing is the byte PUT to the presigned URL, not the metadata POST.
    Falsifier: drop the upload record in add_file and only two rows land."""
    broker = _Broker()
    monkeypatch.setenv(wr.SOCKET_ENV, broker.path)
    monkeypatch.setenv(wr.ACTOR_ENV, "seat-probe:scott@laptop")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"fileId": "f-9", "uploadUrl": "https://s3.test/up"})
        return httpx.Response(200)

    client = _client([])
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    mid = "0d3fffa2-03aa-4cf5-8e8c-2f77cb3c9d46"
    client.add_file(mid, "scan", b"PDFBYTES", folder_id="fold-1")
    phases = [(r["method"], r["phase"]) for r in broker.rows]
    assert phases == [("POST", "intent"), ("POST", "result"), ("PUT", "intent"), ("PUT", "result")]
    put_intent = broker.rows[2]
    assert put_intent["path"].endswith("/documents/files/f-9/content")
    import hashlib

    assert put_intent["body_sha256"] == hashlib.sha256(b"PDFBYTES").hexdigest()
    assert broker.rows[3]["returned_id"] == "f-9"


@pytest.mark.parametrize("key", ["fileId", "folderId", "documentId", "id"])
def test_the_result_row_names_the_object_smokeball_returned(key):
    assert wr._returned_id({key: "abc-123"}) == "abc-123"


def test_an_unreachable_broker_never_reads_as_a_smokeball_outage(monkeypatch):
    """The overlay pages "Smokeball down" and opens the MCP circuit on these
    phrases (shared/connector_signatures.py). An audit-broker problem must not."""
    monkeypatch.setenv(wr.SOCKET_ENV, "/tmp/wr-nothing-listens.sock")
    monkeypatch.setenv(wr.ACTOR_ENV, "seat-probe:scott@laptop")
    with pytest.raises(wr.WriteNotRecorded) as err:
        _client([]).request("POST", "/tasks", json={"subject": "x"})
    text = str(err.value)
    assert "audit broker unreachable" in text
    for marker in ("timed out", "Connection refused", "All connection attempts failed", "Errno"):
        assert marker not in text
