"""llm.build_params_marked: a dict-valued ``effort`` (the negotiation read's
structured output, #3122) must not move any other lane's request by a byte.

Every caller in the demand, drafting, chronology and litigation lanes passes
``effort`` as a string, "" or None (``git grep effort=`` over the runner). For
each such value and every stage with a table default, the params are compared,
as canonical JSON, with what the code before #3122 built: ``output_config`` is
``{"effort": e}`` exactly when ``e`` is truthy, and absent otherwise. A dict is
the only new input, and only the negotiation read passes one."""

from __future__ import annotations

import json

import pytest

from medchron.llm import EFFORT_DEFAULTS, build_params_marked

MSGS = [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
STAGES = sorted({*EFFORT_DEFAULTS, "compose", "vision", "billing", "drafting_compose", "demand_compose"})
EFFORTS = (None, "", "low", "medium", "high")


def _pre_3122(stage, effort, **kw):
    """The pre-#3122 shape, written out: build with no effort, then add the one
    key the old code added."""
    params, markers = build_params_marked(stage, effort="", **kw)
    params.pop("output_config", None)
    e = effort if effort is not None else EFFORT_DEFAULTS.get(stage)
    if e:
        params["output_config"] = {"effort": e}
    return params, markers


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"system": "sys", "cache_blocks": ("system", "user:0")},
        {"tools": [{"name": "t", "input_schema": {"type": "object"}}], "tool_choice": {"type": "auto"}},
        {"thinking": {"type": "adaptive"}, "caching": False},
    ],
)
def test_string_and_default_efforts_build_byte_identical_requests(stage, effort, extra):
    new = build_params_marked(stage, model="claude-opus-5", messages=MSGS, max_tokens=100, effort=effort, **extra)
    old = _pre_3122(stage, effort, model="claude-opus-5", messages=MSGS, max_tokens=100, **extra)
    assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True)


def test_only_a_dict_effort_carries_a_format():
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    params, _ = build_params_marked(
        "negotiation_read", model="m", messages=MSGS, max_tokens=1, effort={"effort": "medium", "format": fmt}
    )
    assert params["output_config"] == {"effort": "medium", "format": fmt}
    for e in EFFORTS:
        p, _ = build_params_marked("compose", model="m", messages=MSGS, max_tokens=1, effort=e)
        assert "format" not in (p.get("output_config") or {})
