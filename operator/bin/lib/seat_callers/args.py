"""How a site is called: its arguments, synthesised from the input.

A required parameter is filled by its name (a customer.yaml path, its text, a
slug, a customers directory, a socket path, a request payload). A site whose
parameters are not all nameable here gets an entry in sites.OVERRIDES saying
how to call it, or, when it cannot be called without a side effect, why not.
Nothing here decides what a site does; it only decides how to reach it.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PATH_NAMES = {
    "path",
    "customer_yaml",
    "customer_yaml_path",
    "customer_path",
    "explicit",
    "yaml_path",
    "config_path",
    "customer_file",
}
TEXT_NAMES = {"text", "yaml_text", "source_text", "customer_yaml_text"}
BYTES_NAMES = {"new_bytes"}
SLUG_NAMES = {"slug", "customer_slug"}
DIR_NAMES = {"customers_dir", "customers_root"}
SOCK_NAMES = {"socket_path", "sock_path", "broker_socket"}
PAYLOAD_NAMES = {"payload", "request", "event", "row"}
ENCODED_NAMES = {"encoded"}
TIMEOUT_NAMES = {"timeout"}
# Labels a heartbeat, page or audit row carries: the payload's text, so a
# non-ASCII payload reaches the wire through them too.
LABEL_NAMES = {
    "verb",
    "action_type",
    "skill_name",
    "basis",
    "decision_basis",
    "status",
    "reason",
    "channel",
    "component",
    "proposal_id",
    "output_class",
    "actor_id",
    "space_key",
    "detail",
    "next_scheduled_at",
}
DICT_NAMES = {"extra", "extra_metadata", "pre_run_inputs"}


class NoValue(Exception):
    """A required parameter this module cannot name a value for.

    `inapplicable` marks an input the site cannot take at all (a site that is
    handed text has no "missing file" case), as opposed to a site this module
    does not know how to call."""

    def __init__(self, name: str, inapplicable: bool = False) -> None:
        super().__init__(name)
        self.name = name
        self.inapplicable = inapplicable


@dataclass
class Ctx:
    """What one input gives a call."""

    path: Path
    text: str | None
    tmp: Path
    slug: str
    customers_dir: Path
    sock: str
    repo: Path
    payload: dict[str, Any] = field(default_factory=dict)


def _wants_str(annotation: Any) -> bool:
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", str(annotation))
    return "str" in text and "Path" not in text


def value_for(name: str, annotation: Any, ctx: Ctx) -> Any:
    if name in PATH_NAMES:
        return str(ctx.path) if _wants_str(annotation) else ctx.path
    if name in TEXT_NAMES:
        if ctx.text is None:
            raise NoValue(f"{name} (this input has no text)", inapplicable=True)
        return ctx.text
    if name in BYTES_NAMES:
        if ctx.text is None:
            raise NoValue(f"{name} (this input has no text)", inapplicable=True)
        return ctx.text.encode("utf-8")
    if name in SLUG_NAMES:
        return ctx.slug
    if name in DIR_NAMES:
        return str(ctx.customers_dir) if _wants_str(annotation) else ctx.customers_dir
    if name in SOCK_NAMES:
        return ctx.sock
    if name in PAYLOAD_NAMES:
        return dict(ctx.payload)
    if name in ENCODED_NAMES:
        return json.dumps(ctx.payload).encode("utf-8")
    if name in TIMEOUT_NAMES:
        return 2.0
    if name in LABEL_NAMES:
        return str(ctx.payload.get("verb" if name == "verb" else "text", "ping"))
    if name in DICT_NAMES:
        wants_bytes = "bytes" in (annotation if isinstance(annotation, str) else getattr(annotation, "__name__", ""))
        return json.dumps(ctx.payload).encode("utf-8") if wants_bytes else {}
    raise NoValue(name)


def kwargs_for(fn: Any, ctx: Ctx, given: dict[str, Any], skip_first: bool = False) -> dict[str, Any]:
    """Every required parameter of `fn` not already in `given`, synthesised by name."""
    out = dict(given)
    params = list(inspect.signature(fn).parameters.values())
    if skip_first and params:
        params = params[1:]
    for p in params:
        if p.name in out or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is not p.empty:
            continue
        out[p.name] = value_for(p.name, p.annotation, ctx)
    return out
