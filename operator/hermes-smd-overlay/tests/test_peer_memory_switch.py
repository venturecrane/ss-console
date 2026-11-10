"""A seat can switch per-sender memory off: ``memory.peer_memory_enabled: false``.

WHY (ss-console #3946, gate 9). On a guide seat nothing one representative says
may shape another's answer, and nothing a removed representative said may stay
behind. The per-sender memory plugin was active on every seat with no switch.
Off means off: the real ``register`` attaches no tool, no hook (so nothing is
added to any prompt) and creates no table. Absent keeps today's behaviour, so
no other seat changes (``test_authored_seat_registration_unchanged.py`` pins
that seat by seat).

Shown to fail first: on origin/main ``register`` ignored the key and the
switched-off case registered ``record_peer_preference`` and four hooks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from tests.conftest import FakePluginContext, load_plugin

HOOKS = {"pre_llm_call", "pre_tool_call", "post_tool_call", "on_session_end"}


def _seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: dict) -> Path:
    doc = {
        "customer_id": "guide-test",
        "memory": {
            "d1_namespace": "guide-test",
            "r2_vault_path": "vaults/guide-test/",
            "vectorize_index": "hermes-guide-test-vault",
            **memory,
        },
    }
    path = tmp_path / "customer.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    monkeypatch.setenv("SMD_CUSTOMER_SLUG", "guide-test")
    state = tmp_path / "agent-state.db"
    monkeypatch.setenv("SMD_D1_AGENT_STATE_BINDING", str(state))
    return state


def _tables(db: Path) -> set[str]:
    if not db.exists():
        return set()
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_switched_off_registers_nothing(tmp_path, monkeypatch) -> None:
    state = _seat(tmp_path, monkeypatch, {"peer_memory_enabled": False})
    ctx = FakePluginContext()
    plugin = load_plugin("hermes-smd-peer-memory")
    plugin.register(ctx)
    assert ctx.tools == {}
    assert ctx.registered == {}
    assert "peer_preferences" not in _tables(state)


@pytest.mark.parametrize("memory", [{}, {"peer_memory_enabled": True}])
def test_absent_or_on_registers_what_it_always_did(tmp_path, monkeypatch, memory) -> None:
    state = _seat(tmp_path, monkeypatch, memory)
    ctx = FakePluginContext()
    plugin = load_plugin("hermes-smd-peer-memory")
    plugin.register(ctx)
    assert "record_peer_preference" in ctx.tools
    assert set(ctx.registered) == HOOKS
    assert "peer_preferences" in _tables(state)


def test_an_unreadable_config_keeps_it_on(tmp_path, monkeypatch) -> None:
    _seat(tmp_path, monkeypatch, {})
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "missing.yaml"))
    ctx = FakePluginContext()
    load_plugin("hermes-smd-peer-memory").register(ctx)
    assert "record_peer_preference" in ctx.tools
