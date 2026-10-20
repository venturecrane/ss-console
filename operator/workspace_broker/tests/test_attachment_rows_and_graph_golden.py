"""Two controls on the file-attachment change (ss-console #3359, overlay #532).

1. **The rows carry attachment metadata only.** Every broker send writes a
   ``CONFIRM_SEND_INTENT`` row before the vendor call and a confirm row after
   it (send_intent.py). On a send that carries files, neither row may hold the
   file bytes, their base64 or the filename: the same rule as the body. The
   checker is proven able to fail. A falsifier writes the filename into the row
   and the same checker catches it.

2. **The Graph send to A&P is byte-identical.** A golden of the exact
   ``sendMail`` body the broker posts for a statute-watch workbook send. The
   golden was captured on main BEFORE #3359 (65594275) and is unchanged on main
   after it. Only the per-send ULID in ``X-SMD-Audit-Row`` is normalized, since
   it is minted fresh on every send.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from workspace_broker import transmit_verbs
from workspace_broker.tests import test_agentmail_send as am
from workspace_broker.tests import test_msgraph_send as mg

#: Canonical JSON (sorted keys, no spaces) on one line. No ``.json`` extension
#: on purpose: the repo's prettier would reformat it and break the bytes.
GOLDEN = Path(__file__).parent / "fixtures" / "msgraph_workbook_send.golden"

# Distinctive, so a substring search cannot pass by accident.
_NAMES = ("Brokery-Flyer-Oct26-QZX.pdf", "Brokery-Flyer-Nov2-QZX.pdf")


def _pdf(tag: bytes) -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% UNIQUEPDFBODY-" + tag + b"\n%%EOF\n"


def _descriptor(data: bytes, name: str) -> dict[str, str]:
    return {
        "name": name,
        "content_type": "application/pdf",
        "content_b64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


_FILES = [(_pdf(b"oct26"), _NAMES[0]), (_pdf(b"nov2"), _NAMES[1])]


def _leaks(rows: list[dict]) -> list[str]:
    """Every forbidden fragment found in ``rows``: a filename (any case, with
    or without its extension), the base64, a long base64 prefix, or the raw
    file body. Rows are searched as serialized and with their metadata decoded,
    so a value escaped inside the JSON metadata string is still found."""
    texts = [json.dumps(rows)]
    for row in rows:
        try:
            texts.append(json.dumps(json.loads(row.get("metadata") or "{}")))
        except ValueError:
            texts.append(str(row.get("metadata")))
    haystack = "\n".join(texts)
    lowered = haystack.lower()
    found = []
    for data, name in _FILES:
        b64 = base64.b64encode(data).decode("ascii")
        for needle in (name.lower(), name.rsplit(".", 1)[0].lower()):
            if needle in lowered:
                found.append(f"filename {name}")
        for needle in (b64, b64[:48]):
            if needle in haystack:
                found.append(f"base64 of {name}")
        if data.decode("latin-1") in haystack or "UNIQUEPDFBODY" in haystack:
            found.append(f"bytes of {name}")
    return found


def _send_with_files(tmp_path: Path, request_extra: dict | None = None) -> am.Broker:
    broker = am._broker(tmp_path, am.FakeHTTP())
    response = broker.handle(
        {
            "action": "agentmail_send",
            "payload": {
                "to": ["scott@smd.services"],
                "subject": "Flyers",
                "text": "Both attached.",
                "attachments": [_descriptor(d, n) for d, n in _FILES],
            },
            **(request_extra or {}),
        },
        peer_pid=am.GATEWAY_PID,
        peer_uid=am.AGENT_UID,
    )
    assert response["ok"]
    return broker


def test_intent_and_confirm_rows_carry_no_file_bytes_base64_or_filename(tmp_path: Path) -> None:
    broker = _send_with_files(tmp_path)
    kinds = [row["action_type"] for row in broker.ledger.rows]
    assert kinds == ["CONFIRM_SEND_INTENT", "CONFIRM_SEND_DISPATCHED"]
    assert _leaks(broker.ledger.rows) == []
    # What the confirm row does carry: the broker's hash of each file, in order.
    meta = am._meta(broker)
    assert meta["attachment_sha256"] == ",".join(hashlib.sha256(d).hexdigest() for d, _ in _FILES)


def test_a_refused_file_send_rows_carry_none_of_it_either(tmp_path: Path) -> None:
    broker = am._broker(tmp_path, am.FakeHTTP())
    bad = [{**_descriptor(d, n), "sha256": "0" * 64} for d, n in _FILES]
    with pytest.raises(am.AgentMailRefused):
        broker.handle(
            {"action": "agentmail_send", "payload": {"to": ["scott@smd.services"], "text": "x", "attachments": bad}},
            peer_pid=am.GATEWAY_PID,
            peer_uid=am.AGENT_UID,
        )
    assert broker.ledger.rows, "a refusal still writes its row"
    assert _leaks(broker.ledger.rows) == []


def test_falsifier_a_filename_written_into_the_row_is_caught(tmp_path: Path, monkeypatch) -> None:
    """The checker above can fail: put the filename into every row the broker
    writes (intent and confirm alike) and it reports it on both."""
    real = transmit_verbs.append_send_row

    def leaky(broker, action_type, verb, metadata, **kwargs):
        return real(broker, action_type, verb, {**metadata, "file": _NAMES[0]}, **kwargs)

    monkeypatch.setattr(transmit_verbs, "append_send_row", leaky)
    broker = _send_with_files(tmp_path)
    for row in broker.ledger.rows:
        assert f"filename {_NAMES[0]}" in _leaks([row]), row["action_type"]


def test_falsifier_the_base64_written_into_the_row_is_caught(tmp_path: Path, monkeypatch) -> None:
    real = transmit_verbs.append_send_row
    b64 = base64.b64encode(_FILES[1][0]).decode("ascii")

    def leaky(broker, action_type, verb, metadata, **kwargs):
        return real(broker, action_type, verb, {**metadata, "blob": b64}, **kwargs)

    monkeypatch.setattr(transmit_verbs, "append_send_row", leaky)
    broker = _send_with_files(tmp_path)
    assert all(f"base64 of {_NAMES[1]}" in _leaks([row]) for row in broker.ledger.rows)


# ---------------------------------------------------------------------------
# The Graph golden (A&P's statute-watch workbook send)
# ---------------------------------------------------------------------------


def _normalized(body: dict) -> dict:
    out = json.loads(json.dumps(body))
    for header in (out.get("message") or {}).get("internetMessageHeaders") or []:
        if header.get("name") == "X-SMD-Audit-Row":
            header["value"] = "<audit-row-ulid>"
    return out


def _graph_workbook_send(tmp_path: Path) -> bytes:
    """The sendMail body for a workbook send, as canonical JSON bytes."""
    http = mg.FakeGraph()
    broker = mg._broker(tmp_path, http)
    response = broker.handle(
        {
            "action": "msgraph_send",
            "payload": {
                "to": ["scott@smd.services"],
                "subject": "Statute watch October 2026",
                "body_text": "The full list is attached.",
                "attachments": [mg._attachment()],
            },
            "audit_extra": {"attachment_sha256": mg._XLSX_SHA},
        },
        peer_pid=mg.GATEWAY_PID,
        peer_uid=mg.AGENT_UID,
    )
    assert response["ok"]
    [(method, url, body)] = http.graph_posts()
    assert method == "POST" and url.endswith("/sendMail")
    return json.dumps(_normalized(body), sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_the_graph_workbook_send_is_byte_identical_to_the_golden(tmp_path: Path) -> None:
    golden = GOLDEN.read_bytes().rstrip(b"\n")
    actual = _graph_workbook_send(tmp_path)
    assert hashlib.sha256(actual).hexdigest() == hashlib.sha256(golden).hexdigest(), actual.decode()


def test_falsifier_the_golden_sees_a_changed_attachment(tmp_path: Path, monkeypatch) -> None:
    """The comparison can fail: one extra key on the fileAttachment the broker
    builds and the bytes no longer match."""
    from workspace_broker import msgraph_ops

    real = msgraph_ops.graph_attachments
    monkeypatch.setattr(
        msgraph_ops, "graph_attachments", lambda payload: [{**a, "isInline": False} for a in real(payload)]
    )
    assert _graph_workbook_send(tmp_path) != GOLDEN.read_bytes().rstrip(b"\n")
