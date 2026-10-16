"""The minted AgentMail key classes keep their capability split (ss#2258)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mint_agentmail_keys", Path(__file__).resolve().parents[1] / "mint-agentmail-keys.py"
)
assert _SPEC and _SPEC.loader
mint = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mint)

_TRANSMIT = {"message_send", "message_reply", "draft_send"}
_WRITE = {k for k in ("list_entry_create", "list_entry_delete", "draft_create", "draft_update", "draft_delete")}


def _granted(perms: dict) -> set[str]:
    return {k for k, v in perms.items() if v}


def test_the_lists_key_reads_list_entries_and_can_do_nothing_else():
    """FALSIFIER: brokery's vaulted seat key had no list_entry_read and 403'd on
    its own inbox lists every day 2026-10-14..16. The CI key must carry it, and
    must not be able to transmit or write anything."""
    granted = _granted(mint.LISTS_PERMISSIONS)
    assert "list_entry_read" in granted
    assert not granted & (_TRANSMIT | _WRITE)
    assert granted <= {"inbox_read", "list_entry_read"}


def test_only_the_send_key_can_transmit():
    assert not _granted(mint.NOSEND_PERMISSIONS) & _TRANSMIT
    assert "message_send" in _granted(mint.SEND_PERMISSIONS)
