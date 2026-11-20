"""The overlay's side of work SMD owes (``shared/smd_owed.py``): the record the
help guide and the rules path ask for, and the seat's ``smd_owed`` runtime read
the hourly triage uses with no lookback (review 2026-11-19). Values invented."""

from __future__ import annotations

from shared import runtime_read, smd_owed


def test_an_unread_broker_is_reported_never_empty(monkeypatch) -> None:
    """FALSIFIER: answer an empty page and the triage reads "nothing owed"."""
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    assert smd_owed.open_page()["unavailable"] is True

    def down(_payload):
        raise OSError("socket closed")

    assert smd_owed.open_page(down)["unavailable"] is True


def test_the_runtime_read_serves_the_brokers_open_items(monkeypatch) -> None:
    sent = []

    def broker(payload):
        sent.append(payload)
        return {"ok": True, "owed": [{"id": "o-1"}], "owed_jobs": [{"id": "j-1"}]}

    monkeypatch.setattr(smd_owed, "_request", broker)
    monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", "/run/x.sock")
    page = runtime_read.read_runtime("smd_owed", db_path=None)
    assert sent == [{"action": "smd_owed_open"}]
    assert page == {"entries": [{"id": "o-1"}], "owed_jobs": [{"id": "j-1"}], "cursor": None}


def test_a_record_is_true_only_when_the_broker_says_so() -> None:
    assert smd_owed.record(
        "help_question", "<CAq@mail.firm.example>", send=lambda p: {"ok": True, "smd_told": True}
    )
    assert not smd_owed.record(
        "help_question", "<CAq@mail.firm.example>", send=lambda p: {"ok": True}
    )
    assert not smd_owed.record("help_question", "", send=lambda p: {"ok": True, "smd_told": True})
