"""gen-image-capabilities.py: what the image tells `fleet release --skills` it provides.

A capability list that says too much lets a skill land on a seat that cannot
run it; one that says too little refuses good releases. These pin the three
sources: the broker's verb table (parsed exactly as the broker builds it), the
overlay's tool map minus banned tools, and a walk of the real tree minus the
volume mount, the skills tree and package internals.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from enum import Enum
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "gen-image-capabilities.py"
OPERATOR = Path(__file__).resolve().parents[2]


def _module():
    spec = importlib.util.spec_from_file_location("gen_image_capabilities", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_verbs_parse_equals_the_broker_table() -> None:
    mod = _module()
    parsed = mod.verbs_from_table((OPERATOR / "workspace_broker/verbs.py").read_text())
    from workspace_broker.verbs import TABLE

    assert parsed == sorted(TABLE)


def test_empty_verb_table_refuses() -> None:
    with pytest.raises(SystemExit):
        _module().verbs_from_table("nothing here")


def test_tools_exclude_banned_and_classes_are_the_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    class ActionClass(str, Enum):
        READ = "read"
        DESTRUCTIVE = "destructive"

    fake = types.ModuleType("shared.action_classes")
    fake.TOOL_ACTION_CLASS_MAP = {"email_search": ActionClass.READ, "terminal": ActionClass.DESTRUCTIVE}  # type: ignore[attr-defined]
    fake.BANNED_TOOLS = frozenset({"terminal"})  # type: ignore[attr-defined]
    fake.ActionClass = ActionClass  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shared", types.ModuleType("shared"))
    monkeypatch.setitem(sys.modules, "shared.action_classes", fake)
    tools, classes = _module().tools_and_classes()
    assert tools == ["email_search"]
    assert classes == ["destructive", "read"]


def test_walk_skips_volume_skills_and_internals_but_keeps_venv_bins(tmp_path: Path) -> None:
    mod = _module()
    app, opt = tmp_path / "app", tmp_path / "opt"
    for rel in (
        "app/operator/templates/drafting/drafting_gate_check.py",
        "app/skills/smd-note/SKILL.md",
        "app/adapter/__pycache__/x.pyc",
        "opt/data/customer.yaml",
        "opt/connectors/smokeball/.venv/bin/python",
        "opt/connectors/smokeball/.venv/lib/python3.13/site-packages/pkg/mod.py",
        "opt/hermes/run_agent.py",
        "opt/hermes/.venv/bin/hermes",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    skip = (str(opt / "data"), str(app / "skills"), str(opt / "hermes"))
    paths = mod.walk_paths([str(app), str(opt)], skip, (str(opt / "hermes/.venv/bin"),))
    rels = sorted(p.replace(str(tmp_path), "") for p in paths)
    assert rels == [
        "/app/operator/templates/drafting/drafting_gate_check.py",
        "/opt/connectors/smokeball/.venv/bin/python",
        "/opt/hermes/.venv/bin/hermes",
    ]


def test_document_shape(tmp_path: Path) -> None:
    doc = _module().build(["/app/a"], ["t"], ["v"], ["read"])
    assert json.loads(json.dumps(doc)) == {
        "schema_version": 1,
        "paths": ["/app/a"],
        "tools": ["t"],
        "verbs": ["v"],
        "action_classes": ["read"],
        "selftest": False,
    }


def test_selftest_capability_is_read_from_the_tree() -> None:
    # FALSIFIER: an image missing either file must not claim the self-test, or
    # `fleet release --verify selftest` would wait on a heartbeat field it never sends.
    mod = _module()
    assert mod.build(["/app/selftest.sh", "/app/seat-checks.sh"], ["t"], ["v"], ["read"])["selftest"] is True
    assert mod.build(["/app/selftest.sh"], ["t"], ["v"], ["read"])["selftest"] is False
