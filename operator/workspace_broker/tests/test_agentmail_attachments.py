"""Files on an AgentMail send: re-validated broker-side, built here, audited.

The overlay validates and reads every file before it asks (its PDF reader gives
the send gate the text); the broker is the last hop before the wire and trusts
none of that. Pinned here: the AgentMail part shape, every refusal reason with
the "attachment refused:" prefix the overlay keys on, the broker-computed hashes
on the row, the frame size the files need, and that an invitation and files
never travel together.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from workspace_broker import agentmail_attachments, server
from workspace_broker.agentmail_ops import AgentMailRefused
from workspace_broker.tests.test_agentmail_send import (
    AGENT_UID,
    GATEWAY_PID,
    FakeHTTP,
    _broker,
    _meta,
    _ops,
)

TO = ["ap-client-standin@agentmail.to"]


def _pdf(text: bytes = b"Starts Monday, October 26.") -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% " + text + b"\n%%EOF\n"


def _descriptor(data: bytes, name: str = "Flyer.pdf", content_type: str = "application/pdf") -> dict[str, str]:
    return {
        "name": name,
        "content_type": content_type,
        "content_b64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _posts(http: FakeHTTP) -> list:
    return [c for c in http.calls if c[0] == "POST"]


def test_two_pdfs_go_as_agentmail_parts_with_their_hashes(tmp_path: Path) -> None:
    a, b = _pdf(b"October 26"), _pdf(b"November 2")
    http = FakeHTTP()
    result = _ops(tmp_path, http).send(
        {
            "to": TO,
            "subject": "Flyers",
            "text": "Both attached.",
            "attachments": [_descriptor(a, "A.pdf"), _descriptor(b, "B.pdf")],
        }
    )
    [post] = _posts(http)
    assert post[2]["attachments"] == [
        {
            "filename": "A.pdf",
            "content_type": "application/pdf",
            "content_disposition": "attachment",
            "content": base64.b64encode(a).decode(),
        },
        {
            "filename": "B.pdf",
            "content_type": "application/pdf",
            "content_disposition": "attachment",
            "content": base64.b64encode(b).decode(),
        },
    ]
    assert result["attachment_sha256"] == f"{hashlib.sha256(a).hexdigest()},{hashlib.sha256(b).hexdigest()}"


def _too_big_pair() -> list[dict[str, str]]:
    blob = 1_600_000
    return [_descriptor(_pdf() + b"\0" * blob, "A.pdf"), _descriptor(_pdf() + b"\1" * blob, "B.pdf")]


@pytest.mark.parametrize(
    "attachments, match",
    [
        pytest.param([], "non-empty", id="empty-list"),
        pytest.param("x", "non-empty", id="not-a-list"),
        pytest.param([_descriptor(_pdf(), f"{i}.pdf") for i in range(4)], "at most 3", id="four"),
        pytest.param(
            [
                _descriptor(b"PK1", "a.xlsx", agentmail_attachments.XLSX_CONTENT_TYPE),
                _descriptor(b"PK2", "b.xlsx", agentmail_attachments.XLSX_CONTENT_TYPE),
            ],
            "workbook",
            id="two-workbooks",
        ),
        pytest.param(_too_big_pair(), "together", id="over-the-total"),
        pytest.param([_descriptor(_pdf()), _descriptor(_pdf(b"x"))], "share a name", id="same-name"),
        pytest.param([{**_descriptor(_pdf()), "extra": "x"}], "exactly", id="extra-key"),
        pytest.param([{**_descriptor(_pdf()), "sha256": "0" * 64}], "sha256", id="sha-mismatch"),
        pytest.param([_descriptor(_pdf(), "flyer.exe")], "name", id="exe-name"),
        pytest.param([_descriptor(_pdf(), "flyer.xlsx")], "name", id="workbook-name-on-a-pdf"),
        pytest.param([_descriptor(_pdf(), "flyer.pdf", "application/x-msdownload")], "content_type", id="content-type"),
        pytest.param([_descriptor(b"GIF89a")], "not a PDF", id="not-a-pdf"),
        pytest.param([_descriptor(_pdf(b"/OpenAction << /S /JavaScript /JS (x) >>"))], "scripts", id="javascript"),
        pytest.param([_descriptor(_pdf(b"/A << /S /Launch /F (calc.exe) >>"))], "scripts", id="launch"),
        pytest.param([_descriptor(_pdf(b"/Names << /EmbeddedFiles 9 0 R >>"))], "scripts", id="embedded-files"),
        pytest.param([_descriptor(_pdf(b"/Encrypt 9 0 R"))], "encrypted", id="encrypted"),
        pytest.param([_descriptor(_pdf() + b"\0" * agentmail_attachments.MAX_PDF_BYTES)], "size", id="one-too-big"),
    ],
)
def test_every_bad_attachment_refuses_the_whole_send(tmp_path: Path, attachments: Any, match: str) -> None:
    http = FakeHTTP()
    with pytest.raises(AgentMailRefused, match=match) as excinfo:
        _ops(tmp_path, http).send({"to": TO, "text": "attached", "attachments": attachments})
    assert str(excinfo.value).startswith("attachment refused: ")
    assert _posts(http) == []


def test_a_name_that_merely_contains_an_active_word_is_not_refused(tmp_path: Path) -> None:
    """``/JS`` must be a whole PDF name: ``/JSmith`` in a font name is not a script."""
    http = FakeHTTP()
    _ops(tmp_path, http).send(
        {"to": TO, "text": "x", "attachments": [_descriptor(_pdf(b"/BaseFont /JSmithSans /AAA"))]}
    )
    assert len(_posts(http)) == 1


def test_files_never_ride_an_invitation(tmp_path: Path) -> None:
    http = FakeHTTP()
    with pytest.raises(AgentMailRefused, match="invitation"):
        _ops(tmp_path, http).send(
            {"to": TO, "text": "x", "attachments": [_descriptor(_pdf())], "calendar": {"method": "REQUEST", "ics": "x"}}
        )
    assert _posts(http) == []


def test_a_reply_still_carries_no_files(tmp_path: Path) -> None:
    http = FakeHTTP({"/messages/m1": {"from": "scott@smd.services"}})
    with pytest.raises(AgentMailRefused, match="^attachment refused: "):
        _ops(tmp_path, http).reply({"message_id": "m1", "text": "answer", "attachments": [_descriptor(_pdf())]})
    assert _posts(http) == []


# -- the verb: the row and the frame ------------------------------------------


def _send(broker, request: dict[str, Any]) -> dict[str, Any]:
    return broker.handle({"action": "agentmail_send", **request}, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def test_the_row_carries_the_broker_computed_hashes_never_the_files(tmp_path: Path) -> None:
    a, b = _pdf(b"October 26"), _pdf(b"November 2")
    broker = _broker(tmp_path, FakeHTTP())
    response = _send(
        broker,
        {
            "payload": {
                "to": ["scott@smd.services"],
                "text": "Both attached.",
                "attachments": [_descriptor(a, "A.pdf"), _descriptor(b, "B.pdf")],
            },
            # A caller's stamp is replaced by what the broker hashed.
            "audit_extra": {"attachment_sha256": "0" * 64},
        },
    )
    assert response["ok"]
    meta = _meta(broker)
    assert meta["outcome"] == "sent"
    assert meta["attachment_sha256"] == f"{hashlib.sha256(a).hexdigest()},{hashlib.sha256(b).hexdigest()}"
    ledger = json.dumps(broker.ledger.rows)
    assert base64.b64encode(a).decode() not in ledger and base64.b64encode(b).decode() not in ledger


def test_a_refused_attachment_is_audited_as_a_refusal(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeHTTP())
    with pytest.raises(AgentMailRefused):
        _send(
            broker,
            {
                "payload": {
                    "to": ["scott@smd.services"],
                    "text": "x",
                    "attachments": [{**_descriptor(_pdf()), "sha256": "0" * 64}],
                }
            },
        )
    assert broker.ledger.rows[-1]["action_type"] == "CONFIRM_SEND_FAILED"
    assert _meta(broker)["outcome"] == "refused"


def test_a_caller_list_of_hashes_rides_a_send_with_no_files_of_its_own_only_in_shape(tmp_path: Path) -> None:
    """The caller stamp keeps its closed shape: up to three hashes, comma-joined."""
    broker = _broker(tmp_path, FakeHTTP())
    three = ",".join(["a" * 64, "b" * 64, "c" * 64])
    _send(broker, {"payload": {"to": ["scott@smd.services"], "text": "x"}, "audit_extra": {"attachment_sha256": three}})
    assert _meta(broker)["attachment_sha256"] == three
    _send(
        broker,
        {
            "payload": {"to": ["scott@smd.services"], "text": "y"},
            "audit_extra": {"attachment_sha256": three + ",d" + "d" * 63},
        },
    )
    assert "attachment_sha256" not in _meta(broker, 1)


def test_the_frame_admits_two_flyers_and_the_vendor_bound_holds() -> None:
    """Three MiB decoded is four of base64: the old 1 MiB frame refused two
    flyers as ``request_too_large``. AgentMail's own bound is 6 MB."""
    b64_total = 4 * ((agentmail_attachments.MAX_TOTAL_BYTES + 2) // 3)
    assert b64_total < server.MAX_REQUEST_BYTES
    assert server.MAX_REQUEST_BYTES <= 6 * 1024 * 1024
