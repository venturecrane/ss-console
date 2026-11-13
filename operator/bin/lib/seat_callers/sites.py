"""How to reach the sites whose parameters args.py cannot name by itself.

Keyed `<repo path>::<qualname>`. Each entry is one of:

  kwargs:   (ctx, module) -> dict    extra keyword arguments (args.py fills the rest)
  init:     (ctx, module) -> dict    keyword arguments for the class, for a method site
  instance: (ctx, module) -> object  the instance a method site runs on
  argv:     (ctx, module) -> list    sys.argv[1:] for a script-entry site
  skip:     str                      why the site cannot run without a side effect

An entry says how to CALL a site, never what it does. Where a site takes the
parsed document rather than the file, `parsed` hands it the input as the seat's
own loader would have parsed it, and an input that does not parse is not
applicable to that site.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import yaml

from .args import Ctx, NoValue


def parsed(ctx: Ctx) -> Any:
    if ctx.text is None:
        raise NoValue("cfg (this input has no text)", inapplicable=True)
    try:
        return yaml.safe_load(ctx.text)
    except yaml.YAMLError:
        raise NoValue("cfg (this input does not parse)", inapplicable=True) from None


def _profile_config(ctx: Ctx) -> Any:
    path = ctx.tmp / "profile-config.yaml"
    path.write_text("{}\n", encoding="utf-8")
    return path


def _skill(ctx: Ctx, name: str) -> Any:
    return ctx.repo / "operator" / "skills" / name


OVERRIDES: dict[str, dict[str, Any]] = {
    "operator/bin/seat-readiness.py::check_initiation_card": {
        "kwargs": lambda c, m: {"rep": m.Report(slug=c.slug), "cfg": parsed(c)},
    },
    "operator/bin/seat-readiness.py::coverage_rows": {"kwargs": lambda c, m: {"cfg": parsed(c)}},
    "operator/bin/webhook_reconcile.py::reconcile_all": {
        "kwargs": lambda c, m: {"customer_yaml": str(c.path), "trigger": "boot"}
    },
    "operator/hermes-smd-overlay/bootstrap/role_persona.py::load_role_persona": {
        "kwargs": lambda c, m: {"customer": parsed(c)}
    },
    "operator/hermes-smd-overlay/bootstrap/translate.py::translate_customer_yaml": {
        "kwargs": lambda c, m: {"customer_yaml_path": str(c.path), "hermes_home": str(c.tmp / "hermes")},
    },
    "operator/hermes-smd-overlay/bootstrap/webhook_toolsets.py::reassert": {
        "kwargs": lambda c, m: {"customer_yaml": c.path, "profile_config": _profile_config(c)},
    },
    "operator/hermes-smd-overlay/plugins/hermes-smd-initiation/seat_facts.py::_skill_frontmatter": {
        "kwargs": lambda c, m: {"slug": "statute-watch", "roots": (c.repo / "operator" / "skills",)},
    },
    "operator/hermes-smd-overlay/shared/logic_in_code.py::load_switch": {
        "kwargs": lambda c, m: {"key": sorted(m.KEYS)[0], "path": str(c.path)},
    },
    "operator/hermes-smd-overlay/skills_applier/sync.py::refresh_profiles": {
        "kwargs": lambda c, m: {"hermes_home": c.tmp / "hermes"},
    },
    "operator/hermes-smd-overlay/tests_e2e/_seat.py::build_seat_home": {
        "kwargs": lambda c, m: {
            "root": c.tmp / "seat",
            "model_base_url": "http://127.0.0.1:9",
            "extra_plugins": [],
            "customer_yaml_text": c.text,
        },
    },
    "operator/runners/jobs/jobs/lane_memory.py::main": {"kwargs": lambda c, m: {"argv": [str(c.path)]}},
    "operator/templates/ensure-disabled-skills.py::_frontmatter_name": {
        "kwargs": lambda c, m: {"skill_md": _skill(c, "statute-watch") / "SKILL.md"},
    },
    "operator/templates/pre_run_gate.py::routine_label": {"kwargs": lambda c, m: {"skill": "statute-watch"}},
    "operator/workspace_broker/establishment_acts.py::ActProposals._seat_config": {
        # The method reads one attribute of its store; a full broker is not needed to reach it.
        "instance": lambda c, m: _bare(m.ActProposals, _store=SimpleNamespace(customer_path=c.path)),
    },
    "operator/safety_substrate/invariants/invariant_7.py::_emit_boot_failure_audit": {
        "kwargs": lambda c, m: {"violation": m.Invariant7Violation(customer_slug=c.slug), "broker_socket": c.sock},
    },
    "operator/skills/operator-help/pre_run.py::read_week": {
        "kwargs": lambda c, m: {"path": c.tmp / "week.db", "now": 1768478400.0},
    },
    "operator/templates/skill_helpers.py::writer_factory": {
        "kwargs": lambda c, m: {"writer_cls": lambda sock, slug: {"socket": sock, "slug": slug}},
    },
    "operator/hermes-smd-overlay/plugins/hermes-smd-jobs/__init__.py::_start_background_job": {
        "kwargs": lambda c, m: {"args": {"brief": "synthetic brief", "deliver_to": ""}},
    },
    "operator/hermes-smd-overlay/shared/heartbeat.py::emitter_from_env": {
        "kwargs": lambda c, m: {"audit_db_path_fn": lambda: str(c.tmp / "audit.db")},
    },
    "operator/hermes-smd-overlay/webhook_gate.py::_Handler._handle_runtime": {
        "skip": "an HTTP request handler method: it runs only on a live request object the gate's server builds"
    },
    "operator/hermes-smd-overlay/shared/cost_breaker.py::pin_hard_stops": {
        "kwargs": lambda c, m: {"path": str(c.tmp / "hard-stops.json")},
    },
    "operator/runners/jobs/jobs/note_by_smd.py::_lane": {
        "kwargs": lambda c, m: {"client": m.BrokerClient(c.sock)},
    },
}


def _bare(cls: type, **attrs: Any) -> Any:
    instance = object.__new__(cls)
    for name, value in attrs.items():
        setattr(instance, name, value)
    return instance
