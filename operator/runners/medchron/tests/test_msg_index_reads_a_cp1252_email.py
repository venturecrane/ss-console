"""An email whose text is in a legacy code page is read, not fatal to the job.

2026-10-23, A&P matter 202098: one email's strings carried a cp1252 en dash
(byte 0x96). extract_msg raised UnicodeDecodeError on the subject, the error
escaped index_containers, and the chronology job died at index_msg 18 minutes
in. The indexer now reads each email whole, retries it in cp1252 when it does
not decode, and records any email it still cannot read as that email's error.

FALLBACK: remove the cp1252 retry and the first test's attachment is never
indexed; remove the outer handler and the second test raises.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from medchron.stages import msg


class _Att:
    def __init__(self, name: str, data: bytes) -> None:
        self.longFilename, self.shortFilename, self.data = name, name, data


def _fake_extract_msg(behaviour: str) -> types.ModuleType:
    mod = types.ModuleType("extract_msg")

    class Message:
        def __init__(self, path: str, overrideEncoding: str | None = None) -> None:
            self.enc = overrideEncoding

        @property
        def subject(self) -> str:
            if behaviour == "always" or (behaviour == "cp1252" and self.enc != "cp1252"):
                raise UnicodeDecodeError("utf-8", b"\x96", 0, 1, "invalid start byte")
            return "Records – Dr. Lee"

        @property
        def attachments(self) -> list[_Att]:
            return [_Att("records.pdf", b"%PDF-1.4 " + b"x" * 300_000)]

        def close(self) -> None:
            pass

    mod.Message = Message  # type: ignore[attr-defined]
    return mod


def _sr(tmp_path: Path) -> Any:
    return types.SimpleNamespace(slug_dir=tmp_path, log=lambda _m: None)


def _pulled(tmp_path: Path, *names: str) -> list[dict[str, Any]]:
    out = []
    for n in names:
        p = tmp_path / f"{n}.msg"
        p.write_bytes(b"ole2")
        out.append({"ok": True, "path": str(p), "name": n, "ext": ".msg"})
    return out


def test_a_cp1252_email_is_read_on_the_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "extract_msg", _fake_extract_msg("cp1252"))
    ix, opened = msg.index_containers(_sr(tmp_path), _pulled(tmp_path, "a"), {})
    assert opened == 1 and ix.errors == []
    assert sum(len(r["names"]) for r in ix.by_hash.values()) == 1  # indexed once, not twice


def test_an_unreadable_email_is_its_own_error_and_the_rest_are_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = _fake_extract_msg("always")
    good = _fake_extract_msg("none")
    pulled = _pulled(tmp_path, "bad", "good")

    class Switch:
        def __init__(self, path: str, overrideEncoding: str | None = None) -> None:
            src = bad if path.endswith("bad.msg") else good
            self._m = src.Message(path, overrideEncoding)

        def __getattr__(self, k: str) -> Any:
            return getattr(self._m, k)

    mod = types.ModuleType("extract_msg")
    mod.Message = Switch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "extract_msg", mod)
    ix, opened = msg.index_containers(_sr(tmp_path), pulled, {})
    assert opened == 2
    assert [e["email"] for e in ix.errors] == ["bad"]
    assert len(ix.by_hash) == 1
