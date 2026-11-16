"""The full suite's reach check: every test file that calls either decision
function is in ``tables.SELECTION``, so its record is in the corpus.

Hooked into the suite by ``tests/conftest.py``. It watches every test (one
``sys.monitoring`` callback per code object, then disabled), and when the run
was the whole suite (no file, ``-k`` or ``-m`` given) it fails the session
naming each file the selection is missing. A partial run cannot tell, so it
says nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from tests.decisions import tables

TOOL_ID = 4
_M = sys.monitoring
_REACHED: set[str] = set()
_CURRENT: list[str] = []
_TARGETS = {(d.name, d.file) for d in tables.DECISIONS}


def _on_start(code: Any, offset: int) -> Any:
    for name, file in _TARGETS:
        if code.co_name == name and code.co_filename.endswith(file):
            if _CURRENT:
                _REACHED.add(_CURRENT[0])
            return None
    return _M.DISABLE


def pytest_configure(config: Any) -> None:
    if _M.get_tool(TOOL_ID) is None:
        _M.use_tool_id(TOOL_ID, "smd-decision-reach")
        _M.register_callback(TOOL_ID, _M.events.PY_START, _on_start)
        _M.set_events(TOOL_ID, _M.events.PY_START)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: Any, nextitem: Any) -> Any:
    rel = Path(str(item.path)).resolve()
    root = Path(__file__).resolve().parents[2]
    _CURRENT[:] = [rel.relative_to(root).as_posix() if rel.is_relative_to(root) else str(rel)]
    yield
    _CURRENT.clear()


def missing() -> list[str]:
    selected = set(tables.SELECTION)
    return sorted(f for f in _REACHED if f not in selected and "/decisions/" not in f)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    config = session.config
    whole = (
        config.args_source == pytest.Config.ArgsSource.TESTPATHS
        and not config.option.keyword
        and not config.option.markexpr
        and session.testscollected > 1000
    )
    if _M.get_tool(TOOL_ID) is not None:
        _M.set_events(TOOL_ID, 0)
        _M.free_tool_id(TOOL_ID)
    gone = missing()
    if not whole or not gone:
        return
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    lines = [
        "decision corpus: these test files reach _relay_draft or _evaluate_tool_call and are",
        "not in tests/decisions/tables.py SELECTION, so their decisions are in no golden:",
        *(f"  {f}" for f in gone),
        "Add each to SELECTION and regenerate (tests/decisions/test_decision_corpus.py).",
    ]
    if reporter is not None:
        reporter.write_line("\n".join(lines), red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


__all__ = ["missing", "pytest_configure", "pytest_runtest_protocol", "pytest_sessionfinish"]
