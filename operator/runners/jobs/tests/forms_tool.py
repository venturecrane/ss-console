"""The DISC-001 catalogue build tool, loaded from ``tools/forms/`` (it is a
build tool beside the official PDF, outside the jobs package, so it is not
importable as ``jobs.*``)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

TOOL = Path(__file__).resolve().parents[1] / "tools" / "forms" / "build_catalog.py"


def build_catalog() -> ModuleType:
    name = "forms_build_catalog"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod
