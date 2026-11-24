"""An email with an attachment that will not read is named unreadable, never trimmed.

Before the .msg reader moved into the connector (2026-11-24), an attachment
whose payload raised inside extract_msg made the whole email unreadable, and the
demand pull named it in ``emails_unreadable``. The shared reader records the
attachment's error instead of raising, so ``_open_msg`` must still answer None
for that email: otherwise the email is kept with the attachment silently
dropped, and a demand is built without a record nobody was told was missing.

FALLBACK: remove the attachment-error check in ``pull._open_msg`` and both
tests fail.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from jobs.demand import pull


class _Att:
    def __init__(self, name: str, data: bytes | None) -> None:
        self.longFilename, self.shortFilename = name, name
        self._data = data

    @property
    def data(self) -> bytes:
        if self._data is None:
            raise ValueError("payload will not decompress")
        return self._data


def _fake(attachments: list[_Att]) -> types.ModuleType:
    mod = types.ModuleType("extract_msg")

    class Message:
        def __init__(self, path: str, **_kw: Any) -> None:
            self.subject, self.sender, self.to, self.cc, self.bcc = (
                "Invented: records",
                "Pat <pat@example.test>",
                "sam@example.test",
                "",
                "",
            )
            self.date, self.body, self.attachments = "2026-03-04", "An invented body.", attachments

        def close(self) -> None:
            pass

    mod.Message = Message  # type: ignore[attr-defined]
    return mod


def test_an_attachment_that_will_not_read_makes_the_email_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "extract_msg", _fake([_Att("ok.pdf", b"%PDF x"), _Att("bad.pdf", None)]))
    assert pull._open_msg(tmp_path / "m.msg") is None


def test_split_emails_names_it_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "extract_msg", _fake([_Att("bad.pdf", None)]))
    p = tmp_path / "raw" / "m1.msg"
    p.parent.mkdir()
    p.write_bytes(b"x")
    rows = [{"id": "m1", "name": "Invented records email", "ext": ".msg", "ok": True, "path": str(p), "sha256": "s"}]
    firm = types.SimpleNamespace(firm_domains=("firm.example",), consumer_domains=())
    out = pull.split_emails(rows, tmp_path, firm, set())  # type: ignore[arg-type]
    assert out["unreadable"] == ["Invented records email"]
    assert out["kept"] == [] and out["walled"] == []


def test_a_readable_email_still_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "extract_msg", _fake([_Att("ok.pdf", b"%PDF x")]))
    m = pull._open_msg(tmp_path / "m.msg")
    assert m is not None and m["attachments"] == [("ok.pdf", b"%PDF x")]
