"""The handlers read the broker through ``BrokerContext``, and ``Broker`` is one.

WHAT THIS PINS (code review 2026-09-25, Code Quality 1). The verb-table
refactor typed 53 handler parameters ``broker: Any``; pyright raises nothing on
``Any``, so the pyright ratchet could not see that the send-as lane had lost its
type checking. Two halves keep it closed:

* STATIC, in non-test code pyright does check: ``Broker`` passes ``self`` to
  ``verbs.dispatch`` and ``MedchronVerbs.build``, both typed ``BrokerContext``,
  so a ``Broker`` that stops satisfying the Protocol is a pyright error in
  ``server.py`` and a red ratchet. (It was, while the Protocol said ``medchron``
  could not be ``None``; the class default says it can.)
* HERE, what pyright cannot see: that no handler parameter slides back to
  ``Any``, and that every Protocol member is something ``Broker`` actually
  defines, read from its AST rather than from an instance (``__init__`` needs
  the Machine's environment).

WHAT WOULD MAKE IT FALSE (Law 12). Type a new handler ``broker: Any`` and the
first test names the file. Add a member to the Protocol that ``Broker`` never
assigns and the second test names it.

Run::

    cd operator && python3 -m pytest workspace_broker/tests/test_broker_context.py -q
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WB = Path(__file__).resolve().parents[1]


def _non_test_modules() -> list[Path]:
    return sorted(p for p in WB.glob("*.py") if p.name != "__init__.py")


def test_no_handler_takes_an_untyped_broker():
    offenders = [
        f"{p.name}:{n}"
        for p in _non_test_modules()
        if p.name != "broker_context.py"  # its docstring quotes the old annotation
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\bbroker\s*:\s*Any\b", line)
    ]
    assert offenders == [], f"type these BrokerContext, not Any: {offenders}"


def _protocol_members() -> set[str]:
    tree = ast.parse((WB / "broker_context.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BrokerContext")
    return {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}


def _broker_members() -> set[str]:
    tree = ast.parse((WB / "server.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Broker")
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for node in ast.walk(cls):
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                names.add(t.attr)
    return names


def test_every_protocol_member_is_defined_by_broker():
    members = _protocol_members()
    assert members, "BrokerContext declares nothing"
    missing = sorted(members - _broker_members())
    assert missing == [], f"BrokerContext names members Broker never defines: {missing}"


def test_the_protocol_covers_every_attribute_a_handler_reads():
    """The Protocol is the list of what handlers depend on. A ``broker.<name>``
    read in a handler module that the Protocol does not declare would be a
    pyright error; this names it without needing node."""
    members = _protocol_members()
    read: set[str] = set()
    for p in _non_test_modules():
        if p.name in {"server.py", "broker_context.py"}:
            continue
        read.update(re.findall(r"\bbroker\.([A-Za-z_]\w*)", p.read_text(encoding="utf-8")))
    # getattr(broker, "send_as_now", None) is a test seam read through getattr on
    # purpose, so it never appears as broker.<name> and needs no declaration.
    assert sorted(read - members) == []
