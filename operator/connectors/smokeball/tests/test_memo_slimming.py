"""get_memos_on_matter drops the redundant RTF ``text`` rendering when
``plainText`` is present — LOSSLESS (Smokeball returns both copies of the same
content) and fail-safe (keep ``text`` whenever plainText is absent/empty, so a
memo can never lose its only body). This is the single biggest per-turn
context reduction on the seat (~half a ~20k-token memo list is RTF markup).
"""

from __future__ import annotations


from smokeball_connector import server as srv

_RTF = "{\\rtf1\\ansi\\ansicpg1252\\deff0 \\fs17 Scope-fix verification memo.\\par}"


def _memo(**over):
    base = {
        "id": "memo-1",
        "matterId": "m-1",
        "text": _RTF,
        "plainText": "Scope-fix verification memo.",
        "createdDate": "2026-07-05T00:00:00Z",
    }
    base.update(over)
    return base


def test_slim_memo_drops_rtf_when_plaintext_present() -> None:
    out = srv._slim_memo(_memo())
    assert "text" not in out
    assert out["plainText"] == "Scope-fix verification memo."
    assert out["id"] == "memo-1"  # metadata untouched


def test_slim_memo_keeps_text_when_plaintext_empty() -> None:
    out = srv._slim_memo(_memo(plainText="   "))
    assert out["text"] == _RTF  # fail-safe: never drop the only body


def test_slim_memo_keeps_text_when_plaintext_missing() -> None:
    m = _memo()
    del m["plainText"]
    assert srv._slim_memo(m)["text"] == _RTF


def test_slim_memos_maps_over_envelope() -> None:
    env = {"value": [_memo(id="a"), _memo(id="b")], "size": 2, "offset": 0}
    out = srv._slim_memos(env)
    assert all("text" not in m for m in out["value"])
    assert out["size"] == 2 and out["offset"] == 0  # envelope metadata preserved


def test_slim_memos_passthrough_unexpected_shape() -> None:
    assert srv._slim_memos({"unexpected": True}) == {"unexpected": True}
    assert srv._slim_memos(None) is None


def test_get_memos_on_matter_applies_slimming(monkeypatch) -> None:
    env = {"value": [_memo(id="a"), _memo(id="b", plainText="")], "size": 2}

    class _FakeClient:
        def get(self, path, **params):
            assert path.endswith("/memos")
            return env

    monkeypatch.setattr(srv, "_client", _FakeClient())
    out = srv.get_memos_on_matter("m-1")
    assert "text" not in out["value"][0]  # slimmed (plainText present)
    assert out["value"][1]["text"] == _RTF  # kept (plainText empty -> fail-safe)
