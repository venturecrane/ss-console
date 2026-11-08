"""The logic-in-code switch reader: one file in three processes, one meaning.

CANONICAL SOURCE is ``operator/hermes-smd-overlay/shared/logic_in_code.py``. The
connector and the broker run in separate virtualenvs and import nothing from it,
so each carries a byte-identical copy. Edit the canonical and copy it over them.

The behaviour table runs against every copy, loaded by path with nothing else of
its process on ``sys.path``, which also proves each stands alone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from vendored_sync import OPERATOR_ROOT, assert_byte_identical

_CANONICAL = OPERATOR_ROOT / "hermes-smd-overlay" / "shared" / "logic_in_code.py"
_COPIES = [
    OPERATOR_ROOT / "connectors" / "smokeball" / "smokeball_connector" / "logic_in_code.py",
    OPERATOR_ROOT / "workspace_broker" / "logic_in_code.py",
]
_ALL = [_CANONICAL, *_COPIES]
_FOUR = (
    "printed_source_check",
    "post_record_derivation",
    "conflict_fence",
    "fixed_wording",
    "wording_rules",
    "library_setup",
    "library_edit_notice",
    "status_from_events",
    "layout_into_library",
)


def _load(path: Path) -> ModuleType:
    name = f"_logic_in_code_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses reads the module of a class it builds
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=_ALL, ids=lambda p: p.parent.name)
def reader(request: pytest.FixtureRequest) -> ModuleType:
    return _load(request.param)


def test_the_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        _COPIES,
        floor=len(_COPIES),
        restamp_hint=f"Edit {_CANONICAL.relative_to(OPERATOR_ROOT)} and copy it over the vendored files byte-for-byte.",
    )


def test_it_names_the_four_switches(reader: ModuleType) -> None:
    assert reader.KEYS == _FOUR
    assert reader.MODES == ("observe", "enforce")


@pytest.mark.parametrize(
    ("body", "mode", "has_error"),
    [
        # Absent, at every depth, is observe.
        ("", "observe", False),
        ("customer_id: x\n", "observe", False),
        ("logic_in_code:\n", "observe", False),
        ("logic_in_code: {}\n", "observe", False),
        ("logic_in_code:\n  conflict_fence: enforce\n", "observe", False),
        ("logic_in_code:\n  printed_source_check:\n", "observe", False),
        # The two values mean what they say.
        ("logic_in_code:\n  printed_source_check: observe\n", "observe", False),
        ("logic_in_code:\n  printed_source_check: enforce\n", "enforce", False),
        # Anything else present is enforce.
        ("logic_in_code:\n  printed_source_check: 'off'\n", "enforce", True),
        ("logic_in_code:\n  printed_source_check: Observe\n", "enforce", True),
        ("logic_in_code:\n  printed_source_check: false\n", "enforce", True),
        ("logic_in_code:\n  printed_source_check: [observe]\n", "enforce", True),
        ("logic_in_code: observe\n", "enforce", True),
        ("- a\n- list\n", "enforce", True),
        ("a: [unclosed\n", "enforce", True),
    ],
)
def test_the_value_decides(reader: ModuleType, tmp_path: Path, body: str, mode: str, has_error: bool) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text(body, encoding="utf-8")
    switch = reader.load_switch("printed_source_check", str(path))
    assert (switch.mode, switch.error is not None) == (mode, has_error)
    assert switch.enforcing is (mode == "enforce")


def test_a_missing_file_is_the_default(reader: ModuleType, tmp_path: Path) -> None:
    assert reader.load_switch("conflict_fence", str(tmp_path / "absent.yaml")).mode == "observe"
    assert reader.load_switch("conflict_fence", str(tmp_path / "absent.yaml" / "deeper")).mode == "observe"


def test_a_file_that_is_there_and_cannot_be_read_enforces(reader: ModuleType, tmp_path: Path) -> None:
    # A directory stands in for a permission fault: it exists, and open() fails.
    assert reader.load_switch("conflict_fence", str(tmp_path)).mode == "enforce"
    binary = tmp_path / "customer.yaml"
    binary.write_bytes(b"\xff\xfe\x00not text")
    assert reader.load_switch("conflict_fence", str(binary)).mode == "enforce"


def test_a_switch_nobody_named_enforces_rather_than_raising(reader: ModuleType, tmp_path: Path) -> None:
    switch = reader.load_switch("printed_sauce_check", str(tmp_path / "absent.yaml"))
    assert switch.enforcing and switch.error


def test_it_is_read_fresh_on_every_call(reader: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text("logic_in_code:\n  fixed_wording: observe\n", encoding="utf-8")
    assert reader.load_switch("fixed_wording", str(path)).mode == "observe"
    path.write_text("logic_in_code:\n  fixed_wording: enforce\n", encoding="utf-8")
    assert reader.load_switch("fixed_wording", str(path)).mode == "enforce"


@pytest.mark.parametrize("env", ["SMD_CUSTOMER_YAML_PATH", "SMD_CUSTOMER_YAML"])
def test_either_environment_name_reaches_the_file(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text("logic_in_code:\n  fixed_wording: enforce\n", encoding="utf-8")
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH", raising=False)
    monkeypatch.delenv("SMD_CUSTOMER_YAML", raising=False)
    assert reader.live_path() == "/var/lib/smd-config/customer.yaml"
    monkeypatch.setenv(env, str(path))
    assert reader.load_switch("fixed_wording").mode == "enforce"
