"""The resolution gate: a write that targets a matter the model chose opens only
with a resolution code minted for that matter.

WHY IT LIVES IN THE SDK. Before this module, two Smokeball tools refused a write
without a resolution token, each in its own body (``stage_vendor_invoice`` and
``file_attachment_pages_to_matter``). Every other create or render tool took a
bare ``matter_id``, so a model that settled on a matter in its own head could
write onto it, which is the 2026-09-18 defect (an invoice staged on one of the
two matters a client had open) on every other lane. A rule that each tool body
must remember is a rule the next tool forgets. Declared ONCE, at registration
(``ConnectorServer.tool(requires_resolution=...)``), it cannot be forgotten by a
body, and the tool modules stay unchanged.

WHAT THE DECLARATION DOES, on the MCP-registered wrapper only (Python callers of
the decorated name, the runner and the tests, keep the plain function):

* the tool's input schema gains a ``matter_resolution`` string, and while the
  gate enforces it is listed as required and the description gains one sentence
  saying where to get one (see THE LISTING FOLLOWS THE MODE below);
* a call's ``matter_resolution`` is popped before the tool body runs, so no
  body ever sees it;
* the target matter is read from the call by the per-tool resolver (for most
  tools ``matter_arg()``, the ``matter_id`` argument);
* the token is CLAIMED for that matter before the body runs, put back if the
  body raises, and spent when it returns. Claiming is verify and spend in one
  locked step, so two concurrent calls holding one token cannot both write,
  while a body that raised before writing leaves it usable for the corrected
  call (the ``vendor_invoice`` order: checked at the door, spent by the write).

TWO MODES, read per call from the connector (``ResolutionGate.mode``):

* ``observe`` -- a call the gate would refuse is RECORDED (tool, matter, reason
  code; never an argument's text) and dispatched anyway. This is how the gate
  ships: one daily cycle of rows shows every lane that would have been refused,
  before any lane is.
* ``enforce`` -- the call is refused with the token's own sentence, and the body
  never runs.

THE LISTING FOLLOWS THE MODE, READ AT LIST TIME. The parameter carries a default
in the signature, so a call without it still reaches the gate in observe mode and
is recorded (``missing``) rather than rejected by argument validation before
anything could see it. What the model is shown depends on the mode:

* ``enforce`` (or a mode that cannot be read, or no gate): the parameter is
  REQUIRED and the description carries the sentence saying where to get one,
  because that is the contract the gate holds the call to;
* ``observe``: the parameter is optional and the sentence is gone. A gated
  registration can reach a seat before the skills that pass a resolution, and the
  model reads a listing as instructions: "without it nothing is written" on a seat
  whose gate writes anyway would change the model's behaviour for nothing, and
  could stop it writing at all. Observe means nothing changes for the firm.

The listing and the gate read the same mode (``enforcing`` below), so they cannot
disagree inside the connector. A client that holds an earlier listing sees the new
one when it reconnects; the mode is rebuild-class on a seat, so a change to it
arrives with a release, which restarts the connector.

The SDK knows nothing about Smokeball. A connector supplies the gate: how a
token is claimed and put back, which exception means "refused", the mode, and
where an observation is recorded.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("operator_connector_sdk")

#: The argument every gated tool gains. One name across every connector, so a
#: skill learns it once.
PARAM = "matter_resolution"

MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
MODES = (MODE_OBSERVE, MODE_ENFORCE)

#: The reason code recorded when no matter could be read from the call.
CODE_NO_MATTER = "no_matter"


def doc_sentence(resolvers: tuple[str, ...]) -> str:
    """The sentence appended to a gated tool's description, so the model reads
    the rule where it reads the tool. ``resolvers`` are the connector's tools
    that mint a resolution, named so the model knows which to call."""
    named = " or ".join(resolvers) if resolvers else "the connector's matter resolver"
    return (
        f"{PARAM} is required: pass the {PARAM} that a unique verdict of {named} returned for "
        "this same matter, unchanged; it is single use and short lived, and without it nothing is written."
    )


class MatterArg:
    """The common resolver: the target matter is one named argument of the call.

    A class rather than a lambda so the refusal can name the argument a call left
    out, and so a test can read which argument a tool resolves from.

    ``optional`` is for a tool whose matter argument is itself optional, because
    the vendor record can stand without a matter (a firm-general task or
    calendar event). A call that leaves the argument out then names NO matter,
    so there is no matter for it to be wrong about: the door opens without a
    resolution and records nothing. A call that names a matter is gated exactly
    as a required one is. A required argument (the default) that is absent is
    refused in enforce mode and recorded in observe mode."""

    def __init__(self, argument: str = "matter_id", *, optional: bool = False) -> None:
        self.argument = argument
        self.optional = optional

    def __call__(self, arguments: dict[str, Any]) -> str | None:
        value = arguments.get(self.argument)
        text = value.strip() if isinstance(value, str) else ""
        return text or None

    def __repr__(self) -> str:
        suffix = ", optional=True" if self.optional else ""
        return f"matter_arg({self.argument!r}{suffix})"


def matter_arg(argument: str = "matter_id", *, optional: bool = False) -> MatterArg:
    """The resolver for a tool whose target matter is ``argument``. Pass
    ``optional=True`` only where the tool's own signature makes the matter
    optional (see :class:`MatterArg`)."""
    return MatterArg(argument, optional=optional)


@dataclass(frozen=True)
class ResolutionGate:
    """What a connector supplies so the SDK can gate its writes.

    ``claim(token, matter_id)`` verifies the token is live and bound to that
    matter and takes it out of circulation, returning a handle; it raises an
    instance of ``refusal`` (whose ``code`` attribute names the reason and whose
    text is the sentence the model reads) when it cannot. ``restore(handle)``
    puts a claimed token back. ``mode()`` returns ``observe`` or ``enforce``.
    ``observe(tool, matter_id, code)`` records a call the gate would refuse.
    ``missing_matter(argument)`` is the sentence for a call that named no
    matter."""

    claim: Callable[[object, str], object]
    restore: Callable[[object], None]
    refusal: type[Exception]
    mode: Callable[[], str]
    observe: Callable[[str, str | None, str], None]
    missing_matter: Callable[[str], str]


class GateRefused(Exception):
    """Raised by :func:`admit` in enforce mode. The text is the model's sentence."""


def augment(fn: Callable[..., Any], wrapper: Callable[..., Any], sentence: str) -> None:
    """Give ``wrapper`` the tool's signature plus a keyword-only
    ``matter_resolution: str``, the matching annotation, and the description
    sentence.

    The signature is evaluated in the ORIGINAL function's globals
    (``eval_str=True``): the tool modules use ``from __future__ import
    annotations``, so their annotations are strings that only resolve there. A
    ``__signature__`` on the wrapper stops ``inspect.signature`` unwrapping to
    the original, which is how MCPServer comes to see the extra parameter."""
    sig = inspect.signature(fn, eval_str=True)
    if PARAM in sig.parameters:
        raise ValueError(
            f"{getattr(fn, '__name__', '?')} already takes {PARAM}; a tool that checks its own "
            "resolution must not also declare requires_resolution"
        )
    params = list(sig.parameters.values())
    extra = inspect.Parameter(PARAM, inspect.Parameter.KEYWORD_ONLY, default="", annotation=str)
    at = next((i for i, p in enumerate(params) if p.kind is inspect.Parameter.VAR_KEYWORD), len(params))
    params.insert(at, extra)
    wrapper.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]
    annotations = {name: p.annotation for name, p in sig.parameters.items() if p.annotation is not p.empty}
    if sig.return_annotation is not sig.empty:
        annotations["return"] = sig.return_annotation
    annotations[PARAM] = str
    wrapper.__annotations__ = annotations
    doc = (fn.__doc__ or "").rstrip()
    wrapper.__doc__ = f"{doc}\n\n{sentence}" if doc else sentence


def call_arguments(fn: Callable[..., Any], args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """The call's arguments by name, defaults applied, as the resolver reads them."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        return dict(kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def _mode(gate: ResolutionGate) -> str:
    """The gate's mode. A mode that cannot be read, or reads as anything but the
    two values, is ``enforce``: an unreadable switch must not open the door."""
    try:
        value = gate.mode()
    except Exception:  # noqa: BLE001 - an unreadable mode fails closed, never raises into the call
        logger.warning("resolution gate: mode unreadable; enforcing", exc_info=True)
        return MODE_ENFORCE
    return value if value in MODES else MODE_ENFORCE


def enforcing(gate: ResolutionGate | None) -> bool:
    """Does a call that fails the gate get refused? The same answer :func:`admit`
    acts on, so what the model is told and what happens are one decision. No gate
    refuses in both modes (see :func:`admit`), so it counts as enforcing."""
    return gate is None or _mode(gate) == MODE_ENFORCE


def _record(gate: ResolutionGate, tool: str, matter_id: str | None, code: str) -> None:
    """Record an observation. Never raises: in observe mode the call proceeds
    whether or not its row landed, and a lost row is logged loudly instead."""
    try:
        gate.observe(tool, matter_id, code)
    except Exception:  # noqa: BLE001 - observation must never break the write it observes
        logger.warning("resolution gate: would_refuse row for %s not recorded (%s)", tool, code, exc_info=True)


@dataclass
class Admission:
    """The outcome of the door: the claimed handle to put back if the body
    raises (None when nothing was claimed, i.e. an observed refusal)."""

    handle: object | None = None


def admit(
    gate: ResolutionGate | None,
    tool: str,
    resolver: Callable[[dict[str, Any]], str | None],
    arguments: dict[str, Any],
    token: object,
) -> Admission:
    """Decide the door for one call. Raises :class:`GateRefused` to refuse.

    A connector that declared a gated tool but installed no gate refuses in
    both modes: there is nothing to check a token against, and the absence of a
    check is never permission."""
    if gate is None:
        raise GateRefused(f"{tool} needs a matter resolution, and this connector has no resolution gate installed")
    mode = _mode(gate)
    faulted = False
    try:
        matter_id = resolver(arguments)
    except Exception:  # noqa: BLE001 - a resolver fault is no matter, refused or recorded below
        logger.warning("resolution gate: resolver for %s raised", tool, exc_info=True)
        matter_id, faulted = None, True
    if not matter_id:
        if not faulted and getattr(resolver, "optional", False) is True:
            # The call names no matter (a firm-general task): nothing to resolve.
            return Admission()
        argument = getattr(resolver, "argument", "matter_id")
        if mode == MODE_OBSERVE:
            _record(gate, tool, None, CODE_NO_MATTER)
            return Admission()
        raise GateRefused(gate.missing_matter(argument))
    try:
        return Admission(handle=gate.claim(token, matter_id))
    except gate.refusal as exc:
        if mode == MODE_OBSERVE:
            _record(gate, tool, matter_id, str(getattr(exc, "code", "refused")))
            return Admission()
        raise GateRefused(str(exc)) from exc


def release(gate: ResolutionGate | None, admission: Admission) -> None:
    """The body raised: put a claimed token back so the corrected call can use
    it. Never raises over the body's own exception."""
    if gate is None or admission.handle is None:
        return
    try:
        gate.restore(admission.handle)
    except Exception:  # noqa: BLE001 - the body's exception is the one the caller must see
        logger.warning("resolution gate: claimed resolution not restored", exc_info=True)


__all__ = [
    "CODE_NO_MATTER",
    "MODES",
    "MODE_ENFORCE",
    "MODE_OBSERVE",
    "PARAM",
    "Admission",
    "GateRefused",
    "MatterArg",
    "ResolutionGate",
    "admit",
    "augment",
    "call_arguments",
    "doc_sentence",
    "enforcing",
    "matter_arg",
    "release",
]
