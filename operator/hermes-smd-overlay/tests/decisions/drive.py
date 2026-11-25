"""Run a synthetic case through the real plugin code, under the recorder.

FROZEN with ``recorder.py``. The relay runs from a rostered colleague's email
through both reply hooks, transform first (the pinned Hermes order), with the
real rate limiter and held-reply store on a temp directory; the wall runs
through ``evaluate_tool_call``, the casework wrapper included. The only things
replaced are the two that leave the process: the audit database client (a
class that keeps the rows it is handed) and the AgentMail transport.
"""

from __future__ import annotations

import builtins
import copy
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import load_plugin
from tests.decisions import cases as C
from tests.decisions import recorder, tables

SENDER = "greg@whitfield.example"
ROSTERED_YAML = (
    "customer_id: acme\n"
    "vertical: law-firm\n"
    "scope:\n"
    "  inbound_allow_from:\n"
    f"    - {SENDER}\n"
    "    - dana@whitfield.example\n"
)
DRAFT = {
    "to": [SENDER],
    "subject": "Re: New matter",
    "text": "Thanks for reaching out. We received your intake and will be in touch.",
    "html": "",
}
SEND_ARGS = {"to": ["pat@client.example"], "subject": "Hello", "text": "Thanks, see you Tuesday."}

#: Levers every wall case starts from: an exposure that allows the send, no
#: vertical floor, and the voice and spec gates (each pinned by its own suite)
#: out of the way, so the baseline reaches the allowed end.
WALL_BASE: dict[str, Any] = {
    "_resolve_persona_exposure": {
        C.Ref(f"ActionClass.{a}"): C.Ref("Ceiling.AUTONOMOUS")
        for a in (
            "INTERNAL_WRITE",
            "EXTERNAL_SEND",
            "EXTERNAL_SEND_INTERNAL",
            "EXTERNAL_SEND_CLIENT",
            "EXTERNAL_SEND_VENDOR",
        )
    },
    "_resolve_vertical_floors": {},
    "voice_gate.check_voice_gate": None,
    "spec_gate.check_spec_gate": None,
}


class Interrupt(BaseException):  # noqa: N818 - named for what it stands for
    """What a KeyboardInterrupt or SystemExit is to the decision: not an Exception."""


class AuditRows:
    """The audit database client's one method, keeping what it is handed."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, *params: Any) -> int:
        self.rows.append((sql, params))
        return 1


def agentmail_transport(*, message_id: str, text: str, html: str, **_kw: Any) -> str:
    """Where ``relay.send_reply`` would hand the reply to AgentMail."""
    return f"agentmail-sent-{message_id}"


_PLUGINS: dict[str, Any] = {}


def plugin(name: str) -> Any:
    if name not in _PLUGINS:
        _PLUGINS[name] = load_plugin(name)
    return _PLUGINS[name]


def obtain(path: str, globs: dict[str, Any]) -> Any:
    hit = recorder.resolve(path, globs)
    if hit is None:
        if hasattr(builtins, path):
            return getattr(builtins, path)
        raise LookupError(f"the corpus cannot resolve {path!r}")
    holder, attr = hit
    return holder[attr] if isinstance(holder, dict) else getattr(holder, attr)


def build(spec: Any, globs: dict[str, Any]) -> Any:
    if isinstance(spec, C.New):
        fn = obtain(spec.path, globs)
        return fn(
            *(build(a, globs) for a in spec.args), **{k: build(v, globs) for k, v in spec.kwargs}
        )
    if isinstance(spec, C.Ref):
        return obtain(spec.path, globs)
    if isinstance(spec, tuple):
        return tuple(build(x, globs) for x in spec)
    if isinstance(spec, list):
        return [build(x, globs) for x in spec]
    if isinstance(spec, frozenset):
        return frozenset(build(x, globs) for x in spec)
    if isinstance(spec, dict):
        return {build(k, globs): build(v, globs) for k, v in spec.items()}
    return spec


def control(
    case: C.Case, globs: dict[str, Any], base: dict[str, Any], raise_at: tuple[int, ...] = ()
) -> recorder.Control:
    force = {}
    for name, spec in {**base, **dict(case.force)}.items():
        if isinstance(spec, C.Seq):
            seen = [0]

            def factory(_a: tuple, _k: dict, s: C.Seq = spec, n: list[int] = seen) -> Any:
                value = s.values[min(n[0], len(s.values) - 1)]
                n[0] += 1
                return build(value, globs)

        else:

            def factory(_a: tuple, _k: dict, s: Any = spec) -> Any:
                return build(s, globs)

        force[name] = factory
    return recorder.Control(
        force=force,
        raise_at=raise_at,
        raise_with=(lambda: RuntimeError("raised by the decision corpus")) if raise_at else None,
    )


def _isolate(mp: pytest.MonkeyPatch, tmp: Path) -> None:
    mp.setenv("SMD_SENT_LINES_DIR", str(tmp / "sent_lines"))
    mp.setenv("SMD_REQUEST_INDEX_DB_PATH", str(tmp / "requests.db"))
    mp.setenv("SMD_REQUEST_CARDS_DB_PATH", str(tmp / "smd-gate" / "request_cards.db"))
    mp.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp / "customer.yaml"))
    mp.setenv("HERMES_ACTIVE_PROFILE", "operator")
    # Nobody is enrolled unless a case says the recipient is a client champion.
    mp.setenv("SMD_SENDER_ENROLLMENT_ENROLLED", str(tmp / "enrolled.json"))


#: Step 5c's knob (2026-11-24): what the turn's tool calls did, noted before the draft.
#: "filed" is one document filed; "listed" is a file list naming a document.
_ACT_CALLS = {
    "filed": (
        "mcp_smokeball_add_file",
        {"matter_id": "matter-1", "file_name": "Intake Memo.docx"},
        {
            "matterId": "matter-1",
            "fileName": "Intake Memo.docx",
            "fileId": "f-1",
            "readback": "confirmed",
        },
    ),
    "listed": (
        "mcp_smokeball_get_files_on_matter",
        {"matter_id": "matter-1"},
        {"value": [{"id": "f-1", "name": "Intake Memo", "fileExtension": ".docx"}]},
    ),
}


def _act_knob(mod: Any, act: Any) -> None:
    """Step 5c's record, fresh per case: no case but an act knob has a tool call
    noted. (A tree from before step 5c has no record: the knob does nothing there.)"""
    try:
        from shared import turn_facts
    except ImportError:
        return
    turn_facts.TURN_FACTS.clear()
    act_reply = getattr(mod.relay_checks, "act_reply", None)
    if act_reply is not None:
        act_reply.LOOK_INTOS.clear()
    if act in _ACT_CALLS:
        tool, args, result = _ACT_CALLS[act]
        kwargs = {"tool_name": tool, "args": args, "result": json.dumps(result)}
        turn_facts.note_call("s1", "msg-in-1", kwargs)


def run_relay(case: C.Case, raise_at: tuple[int, ...] = ()) -> recorder.Scope:
    from shared import inbound

    mod = plugin("hermes-smd-reply")
    knobs = dict(case.knobs)
    with tempfile.TemporaryDirectory() as raw, pytest.MonkeyPatch.context() as mp:
        tmp = Path(raw)
        _isolate(mp, tmp)
        (tmp / "customer.yaml").write_text(ROSTERED_YAML, encoding="utf-8")
        mp.setattr(mod, "_INFRA_READY", knobs.get("infra_ready", True))
        mp.setattr(mod, "_CUSTOMER_SLUG", "acme")
        mp.setattr(mod, "_D1_CLIENT", AuditRows())
        mp.setattr(mod, "_LIMITER", mod.relay.RateLimiter() if knobs.get("limiter", True) else None)
        mp.setattr(mod, "_YAML_PATH", tmp / "customer.yaml")
        mp.setattr(mod, "_HELD_STORE", mod.held_store.HeldReplyStore(str(tmp / "held.db")))
        mp.setattr(mod.relay, "send_reply", agentmail_transport)
        if knobs.get("champion"):
            enrolled = {"schema": 1, "enrolled": [{"address": SENDER, "label": "champion"}]}
            (tmp / "enrolled.json").write_text(json.dumps(enrolled), encoding="utf-8")
        # Step 5a's register, fresh per case: no case but a say knob has a
        # whole-reply sentence this turn ("match": the draft's own text).
        # (A tree from before step 5a has no register: the knob does nothing there.)
        turn_says = getattr(mod.relay_checks, "TurnSays", None)
        if turn_says is not None:
            mp.setattr(mod.relay_checks, "_TURN_SAYS", turn_says())
            mp.setattr(mod.relay_checks, "_SAY_HOLDS", mod.held_notice.HoldCounter())
            if knobs.get("say"):
                said = (
                    DRAFT["text"]
                    if knobs["say"] in ("match", "none_and_match")
                    else "Received. The memo is with us."
                )
                mod.relay_checks._TURN_SAYS.note("s1", "msg-in-1", said)
                if knobs["say"] == "none_and_match":
                    # A "none" beside a real say: the real say decides (review 2026-11-19).
                    mod.relay_checks._TURN_SAYS.note("s1", "msg-in-1", "none")
        _act_knob(mod, knobs.get("act"))
        recorder.reset_registers()
        session = knobs.get("session_id", "s1")
        if knobs.get("origin", "turn") == "turn":
            origin = inbound.InboundOrigin(
                SENDER, "msg-in-1", inbox_id=knobs.get("inbox_id", "inbox-1")
            )
            inbound.SESSION_INBOUND_ORIGIN.record("s1", origin)
            inbound.SESSION_INBOUND_ORIGIN.note_turn_prompt("s1", "msg-in-1")
        kw = {
            "tool_name": knobs.get("tool_name", "agentmail:create_draft"),
            "args": copy.deepcopy(knobs.get("args", DRAFT)),
            "result": json.dumps({"draft_id": "draft-1", "status": "ok"}),
            "tool_call_id": knobs.get("tool_call_id", "call-1"),
            "session_id": session,
        }
        ctl = control(case, vars(mod), {}, raise_at)
        with recorder.scope(ctl, coverage=True) as sc, recorder.deterministic(case.name):
            try:
                if knobs.get("direct"):
                    mod._relay_draft(**kw)
                mod.on_transform_tool_result(**kw)
                mod.on_post_tool_call(**kw)
            except Interrupt:
                pass
    return sc


def run_wall(case: C.Case, raise_at: tuple[int, ...] = ()) -> recorder.Scope:
    trust = plugin("hermes-smd-trust")
    enforce = trust.enforce
    knobs = dict(case.knobs)
    with tempfile.TemporaryDirectory() as raw, pytest.MonkeyPatch.context() as mp:
        _isolate(mp, Path(raw))
        mp.setenv("HERMES_ACTIVE_PROFILE", knobs.get("persona", "operator"))
        recorder.reset_registers()
        args = copy.deepcopy(knobs.get("args", SEND_ARGS))
        ctl = control(case, vars(enforce), WALL_BASE, raise_at)
        with recorder.scope(ctl, coverage=True) as sc, recorder.deterministic(case.name):
            try:
                enforce.evaluate_tool_call(
                    knobs.get("tool_name", "mcp_agentmail_send_message"),
                    args,
                    "acme",
                    session_id="s1",
                    tool_call_id="call-1",
                    session_match="keyed",
                    code_fixed_recipients=knobs.get("code_fixed_recipients", False),
                )
            except (Exception, Interrupt):  # noqa: BLE001 - the record holds the raise
                pass
    return sc


RUN = {"relay": run_relay, "wall": run_wall}


def compact(timeline: list[dict[str, Any]]) -> list[Any]:
    """A pair's or a raise's record: the order of what was called (direct calls
    by name, effects by name and arguments) and how each call ended."""
    out: list[Any] = []
    for entry in timeline:
        if "hook" in entry:
            out.append(entry)
            continue
        values = entry.get("values", [])
        events = [values[e["$ref"]] if "$ref" in e else e for e in entry["events"]]
        steps = []
        for e in events:
            e = values[e["$ref"]] if "$ref" in e else e
            if "call" in e:
                steps.append(e["call"] + ("!" if "raised" in e else ""))
            else:
                steps.append(
                    {
                        "edge": e["edge"],
                        "args": _deref(e.get("args"), values),
                        "kwargs": _deref(e.get("kwargs"), values),
                    }
                )
        end = {
            k: _deref(entry[k], values)
            for k in ("returned", "raised", "inputs_after")
            if k in entry
        }
        out.append({"decision": entry["decision"], "steps": steps, **end})
    return out


def from_raise(timeline: list[Any], k: int) -> list[Any]:
    """A raise case's record from its k-th direct call (the raised one) on: what
    came before it is the parent case's record, already pinned."""
    out = []
    first = True
    for entry in timeline:
        steps = entry.get("steps") if isinstance(entry, dict) else None
        if steps is None or not first:
            out.append(entry)
            continue
        first = False
        direct = [i for i, st in enumerate(steps) if isinstance(st, str)]
        at = direct[k] if k < len(direct) else len(steps)
        out.append({**entry, "steps": steps[at:], "from": k})
    return out


def _deref(v: Any, values: list[Any]) -> Any:
    if isinstance(v, dict):
        if set(v) == {"$ref"}:
            return _deref(values[v["$ref"]], values)
        return {k: _deref(x, values) for k, x in v.items()}
    if isinstance(v, list):
        return [_deref(x, values) for x in v]
    return v


def direct_calls(timeline: list[dict[str, Any]]) -> list[Any]:
    """The direct calls of the case's first decision: name, arguments, and how
    each ended (a call's path depends on what every earlier call returned)."""
    for entry in timeline:
        if "decision" in entry:
            values = entry.get("values", [])
            events = [_deref(e, values) for e in entry["events"]]
            return [
                [e["call"], e.get("args"), e.get("kwargs"), e.get("result"), e.get("raised")]
                for e in events
                if "call" in e
            ]
    return []


def _signature(calls: list[Any], k: int) -> str:
    """The calls before the k-th with their endings, and the k-th as called."""
    return json.dumps([*calls[:k], calls[k][:3]], sort_keys=True)


def corpus() -> tuple[dict[str, dict[str, Any]], recorder.Scope]:
    """Every synthetic golden, by file name, and the coverage they reached."""
    cov = recorder.Scope()
    docs: dict[str, dict[str, Any]] = {}

    def take(sc: recorder.Scope) -> None:
        cov.sites |= sc.sites
        cov.raised_sites |= sc.raised_sites

    for key, cases in C.CASES.items():
        run = RUN[key]
        bases = [c for c in cases if c.family in ("guard", "condition")]
        for family in ("guard", "condition"):
            docs[f"synthetic/{key}.{family}"] = {}
        prefixes: dict[str, list[Any]] = {}
        for c in bases:
            sc = run(c)
            take(sc)
            docs[f"synthetic/{key}.{c.family}"][c.name] = sc.timeline
            prefixes[c.name] = direct_calls(sc.timeline)
        docs[f"synthetic/{key}.pair"] = {}
        for c in C.pairs(cases):
            sc = run(c)
            take(sc)
            docs[f"synthetic/{key}.pair"][c.name] = compact(sc.timeline)
        docs[f"synthetic/{key}.raise"] = raises = {}
        seen: set[str] = set()
        second: list[tuple[C.Case, int, list[Any], list[tuple[str, int, int]]]] = []
        for c in bases:
            calls = prefixes[c.name]
            for k in range(len(calls)):
                sig = _signature(calls, k)
                if sig in seen:
                    continue
                seen.add(sig)
                sc = run(c, raise_at=(k,))
                take(sc)
                raises[f"{c.name}@{k}:{calls[k][0]}"] = from_raise(compact(sc.timeline), k)
                second.append((c, k, direct_calls(sc.timeline), sc.direct_sites))
        # A call only an error reaches (the catch-all's own calls) gets its own
        # raise: the first raise case that calls it, raised again there.
        for c, k, calls, sites in second:
            for j in range(k + 1, min(len(calls), len(sites))):
                if sites[j] in cov.raised_sites:
                    continue
                sc = run(c, raise_at=(k, j))
                take(sc)
                raises[f"{c.name}@{k}+{j}:{calls[j][0]}"] = from_raise(compact(sc.timeline), k)
    return docs, cov


__all__ = ["RUN", "WALL_BASE", "compact", "corpus", "plugin", "run_relay", "run_wall", "tables"]
