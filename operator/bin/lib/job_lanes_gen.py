"""Project every job-lane output from operator/contracts/job-lanes.yaml.

The yaml is the one place a job lane is declared, with each way the old hand
lists legitimately differed as a named column. This module shape-checks it and
renders the committed outputs: one Python module per install tree that cannot
import another tree's code (the jobs bundle, the workspace broker, the
overlay's shared package, the laptop fleet tools), a TypeScript module for the
console, and a bash-3.2-safe shell file. Every sequence keeps the yaml's order.

operator/bin/gen-job-lanes.py writes the outputs; --check fails when one
differs, and operator/bin/tests/test_job_lanes_generation.py runs it in CI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

GENERATOR = "operator/bin/gen-job-lanes.py"
CONTRACT_REL = "operator/contracts/job-lanes.yaml"
TEST_REL = "operator/bin/tests/test_job_lanes_generation.py"

SHELL_REL = "operator/templates/_job-lanes.generated.sh"
TS_REL = "src/lib/operator/job-lanes.generated.ts"
#: The constants the console reads. The dead-export gate (knip --production)
#: refuses an export nothing in production imports, so the TypeScript output
#: carries only these; add a name here when a console module needs another.
TS_CONSTANTS = (
    "LANE_SKILL",
    "LANE_EMAIL_LABEL",
    "FIRM_INPUT_LANES",
    "FIRM_INPUT_FILE_LANES",
    "LANE_FIRM_MODULE",
    "FIRM_INPUT_EXTRAS",
)
PYTHON_RELS = (
    "operator/runners/jobs/jobs/job_lanes.py",
    "operator/workspace_broker/job_lanes.py",
    "operator/hermes-smd-overlay/shared/job_lanes.py",
    "operator/bin/lib/job_lanes.py",
)

_LANE = re.compile(r"\A[a-z][a-z0-9_]*\Z")
_SKILL = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")
_DIRNAME = re.compile(r"\A[a-z][a-z0-9._-]*\Z")
_WORD = re.compile(r"\A[a-z]+\Z")
_LABEL = re.compile(r"\A[a-z][a-z ]*[a-z]\Z")
_PREFIX = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")
_MODULE = re.compile(r"\Ajobs(\.[a-z_][a-z0-9_]*)+\Z")
_FLAGS = ("queued", "scheduled", "request_card", "broker_firm_copy")
_FIELDS = {
    "skill",
    "queued",
    "queue_dir",
    "jobs_dir",
    "child_pid",
    "wake_word",
    "scheduled",
    "request_card",
    "email_label",
    "audit_prefix",
    "firm_inputs",
    "firm_module",
    "broker_firm_copy",
}
_QUEUE_DIRS = ("queue_dir", "jobs_dir", "child_pid")
_TS_KEY = re.compile(r"\A[A-Za-z_$][A-Za-z0-9_$]*\Z")


@dataclass(frozen=True)
class Lane:
    name: str
    skill: str
    queued: bool = False
    queue_dir: str | None = None
    jobs_dir: str | None = None
    child_pid: str | None = None
    wake_word: str | None = None
    scheduled: bool = False
    request_card: bool = False
    email_label: str | None = None
    audit_prefix: str | None = None
    firm_inputs: str | None = None
    firm_module: str | None = None
    broker_firm_copy: bool = False


@dataclass(frozen=True)
class Declaration:
    lanes: tuple[Lane, ...]
    firm_input_extras: tuple[str, ...]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _str(name: str, spec: dict, key: str, pattern: re.Pattern[str]) -> str | None:
    value = spec.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.match(value):
        raise ValueError(f"job lanes: {name}.{key} = {value!r} is not a valid value")
    return value


def _lane(name: object, spec: object) -> Lane:
    if not isinstance(name, str) or not _LANE.match(name):
        raise ValueError(f"job lanes: lane name {name!r} is not lower_snake_case")
    if not isinstance(spec, dict):
        raise ValueError(f"job lanes: {name} must map its columns")
    if not set(spec) <= _FIELDS:
        raise ValueError(f"job lanes: {name} has unknown fields {sorted(set(spec) - _FIELDS)}")
    for flag in _FLAGS:
        if flag in spec and spec[flag] is not True:
            raise ValueError(f"job lanes: {name}.{flag} is a flag: write `true` or leave it out")
    skill = _str(name, spec, "skill", _SKILL)
    if skill is None:
        raise ValueError(f"job lanes: {name} needs a skill")
    queued = spec.get("queued") is True
    dirs = {k: _str(name, spec, k, _DIRNAME) for k in _QUEUE_DIRS}
    if queued != all(dirs.values()) or (not queued and any(dirs.values())):
        raise ValueError(
            f"job lanes: {name}: a queued lane names queue_dir, jobs_dir and child_pid; no other lane does"
        )
    firm_inputs = spec.get("firm_inputs")
    if firm_inputs not in (None, "file", "dir"):
        raise ValueError(f"job lanes: {name}.firm_inputs must be file or dir")
    firm_module = _str(name, spec, "firm_module", _MODULE)
    if (firm_inputs is None) != (firm_module is None):
        raise ValueError(f"job lanes: {name}: firm_inputs and firm_module come together")
    if spec.get("broker_firm_copy") and firm_inputs is None:
        raise ValueError(f"job lanes: {name}: broker_firm_copy needs firm_inputs")
    wake_word = _str(name, spec, "wake_word", _WORD)
    if spec.get("scheduled") and wake_word is None:
        raise ValueError(f"job lanes: {name}: a scheduled lane wakes a turn, so it needs a wake_word")
    return Lane(
        name=name,
        skill=skill,
        queued=queued,
        queue_dir=dirs["queue_dir"],
        jobs_dir=dirs["jobs_dir"],
        child_pid=dirs["child_pid"],
        wake_word=wake_word,
        scheduled=spec.get("scheduled") is True,
        request_card=spec.get("request_card") is True,
        email_label=_str(name, spec, "email_label", _LABEL),
        audit_prefix=_str(name, spec, "audit_prefix", _PREFIX),
        firm_inputs=firm_inputs,
        firm_module=firm_module,
        broker_firm_copy=spec.get("broker_firm_copy") is True,
    )


def load(path: Path) -> Declaration:
    """Parse and shape-check the yaml. Raises ValueError naming the first fault."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"lanes", "firm_input_extras"}:
        raise ValueError(f"{path}: expected exactly the keys lanes and firm_input_extras")
    if not isinstance(raw["lanes"], dict) or len(raw["lanes"]) < 2:
        raise ValueError(f"{path}: lanes must map at least two lanes")
    lanes = tuple(_lane(name, spec) for name, spec in raw["lanes"].items())
    extras = raw["firm_input_extras"]
    if not isinstance(extras, list) or not all(isinstance(e, str) and _LANE.match(e) for e in extras):
        raise ValueError(f"{path}: firm_input_extras must be a list of names")
    names = [lane.name for lane in lanes]
    for column in ("skill", "wake_word", "audit_prefix", "queue_dir", "jobs_dir", "child_pid"):
        values = [getattr(lane, column) for lane in lanes if getattr(lane, column) is not None]
        if len(values) != len(set(values)) and column != "skill":
            raise ValueError(f"{path}: two lanes share a {column}")
    if set(extras) & set(names):
        raise ValueError(f"{path}: a firm_input_extras name is a lane")
    return Declaration(lanes=lanes, firm_input_extras=tuple(extras))


# ---- the constants, computed once and rendered three ways ----------------------


def constants(decl: Declaration) -> list[tuple[str, str, object]]:
    """(name, comment, value) for every generated constant; a value is a tuple or a dict."""
    return [*_run_constants(decl.lanes), *_input_constants(decl)]


def _run_constants(lanes: tuple[Lane, ...]) -> list[tuple[str, str, object]]:
    """The lanes, their skills and kinds, their run-dir names and their wakes."""
    return [
        ("JOB_LANES", "Every job lane, in declared order.", tuple(lane.name for lane in lanes)),
        (
            "LANE_SKILL",
            "Job lane -> the skill whose being enabled authors it.",
            {lane.name: lane.skill for lane in lanes},
        ),
        (
            "LANE_JOB_KIND",
            "Job lane -> its job kind (the broker's reply kind).",
            {lane.name: f"{lane.name}_job" for lane in lanes},
        ),
        (
            "QUEUED_LANES",
            "Lanes whose jobs the runner takes from a queue under the run dir.",
            tuple(lane.name for lane in lanes if lane.queued),
        ),
        (
            "LANE_QUEUE_DIR",
            "Queued lane -> its queue under the run dir.",
            {lane.name: lane.queue_dir for lane in lanes if lane.queued},
        ),
        (
            "LANE_JOBS_DIR",
            "Queued lane -> its job dirs under the run dir.",
            {lane.name: lane.jobs_dir for lane in lanes if lane.queued},
        ),
        (
            "LANE_CHILD_PID",
            "Queued lane -> its child pid file under the run dir.",
            {lane.name: lane.child_pid for lane in lanes if lane.queued},
        ),
        (
            "WAKE_WORD_JOB_KIND",
            "The word a completion wake uses -> the job kind it names.",
            {lane.wake_word: f"{lane.name}_job" for lane in lanes if lane.wake_word},
        ),
        (
            "SCHEDULED_JOB_KINDS",
            "Job kinds whose wake may be a scheduled run's (no request email).",
            tuple(f"{lane.name}_job" for lane in lanes if lane.scheduled),
        ),
    ]


def _input_constants(decl: Declaration) -> list[tuple[str, str, object]]:
    """Request cards, email labels, audit prefixes and firm inputs."""
    lanes = decl.lanes
    firm = [lane for lane in lanes if lane.firm_inputs]
    return [
        (
            "REQUEST_CARD_LANES",
            "Lanes that write a request card row.",
            tuple(lane.name for lane in lanes if lane.request_card),
        ),
        (
            "LANE_EMAIL_LABEL",
            "Lane -> what a person calls its work in a card email.",
            {lane.name: lane.email_label for lane in lanes if lane.email_label},
        ),
        (
            "AUDIT_PREFIX_LANE",
            "Audit action_type prefix the broker writes -> its lane.",
            {lane.audit_prefix: lane.name for lane in lanes if lane.audit_prefix},
        ),
        ("FIRM_INPUT_LANES", "Lanes with firm inputs in the vault.", tuple(lane.name for lane in firm)),
        (
            "FIRM_INPUT_FILE_LANES",
            "Firm-input lanes whose inputs are one file, <lane>-firm.yaml.",
            tuple(lane.name for lane in firm if lane.firm_inputs == "file"),
        ),
        (
            "FIRM_INPUT_DIR_LANES",
            "Firm-input lanes whose inputs are a directory, <lane>/.",
            tuple(lane.name for lane in firm if lane.firm_inputs == "dir"),
        ),
        (
            "LANE_FIRM_MODULE",
            "Firm-input lane -> the runner module whose validate() judges its inputs.",
            {lane.name: lane.firm_module for lane in firm},
        ),
        (
            "BROKER_FIRM_COPY_LANES",
            "Lanes whose firm inputs the applier also copies for the broker.",
            tuple(lane.name for lane in lanes if lane.broker_firm_copy),
        ),
        ("FIRM_INPUT_EXTRAS", "Firm inputs that are not job lanes.", decl.firm_input_extras),
    ]


def _header(comment: str) -> str:
    return (
        f"{comment} GENERATED by {GENERATOR} from {CONTRACT_REL}.\n"
        f"{comment} DO NOT EDIT BY HAND. Regenerate: python3 {GENERATOR}\n"
        f"{comment} CI checks it is in sync ({TEST_REL}).\n"
    )


def _py_value(value: object) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        body = "".join(f'    "{k}": "{v}",\n' for k, v in value.items())
        return "{\n" + body + "}"
    items = list(value)  # type: ignore[arg-type]
    if not items:
        return "()"
    if len(items) == 1:
        return f'("{items[0]}",)'  # ruff format keeps a one-item tuple on one line
    return "(\n" + "".join(f'    "{v}",\n' for v in items) + ")"


def render_python(decl: Declaration) -> str:
    lines = [
        _header("#"),
        f'"""The job lanes, generated from {CONTRACT_REL}. Stdlib only, no imports."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    for name, comment, value in constants(decl):
        kind = "dict[str, str]" if isinstance(value, dict) else "tuple[str, ...]"
        lines += [f"#: {comment}", f"{name}: {kind} = {_py_value(value)}", ""]
    return "\n".join(lines)


def _ts_value(value: object) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        body = "".join(f"  {k if _TS_KEY.match(k) else repr(k)}: '{v}',\n" for k, v in value.items())
        return "{\n" + body + "}"
    items = list(value)  # type: ignore[arg-type]
    if not items:
        return "[]"
    return "[\n" + "".join(f"  '{v}',\n" for v in items) + "]"


def render_ts(decl: Declaration) -> str:
    """The console's copy (src/), prettier-stable."""
    parts = [_header("//"), f"/** The job lanes, generated from {CONTRACT_REL}. */\n"]
    for name, comment, value in constants(decl):
        if name not in TS_CONSTANTS:
            continue
        if isinstance(value, dict):
            # `as const` keeps the literal keys, so `keyof typeof` names the lanes.
            parts.append(f"\n/** {comment} */\nexport const {name} = {_ts_value(value)} as const\n")
            continue
        decl_line = f"export const {name}: readonly string[] = "
        inline = "[" + ", ".join(f"'{v}'" for v in value) + "]"  # type: ignore[union-attr]
        # Prettier collapses an array that fits the print width (100).
        rendered = inline if len(decl_line) + len(inline) <= 100 else _ts_value(value)
        parts.append(f"\n/** {comment} */\n{decl_line}{rendered}\n")
    return "".join(parts)


def render_shell(decl: Declaration) -> str:
    """Space-separated lists and lookup functions: no arrays, so bash 3.2 (macOS) reads it."""
    head = _header("#") + "# Assignments and functions only: sourcing this file exports nothing.\n"
    lines = [head + "# shellcheck shell=bash disable=SC2034 # read by the scripts that source this file"]
    for name, comment, value in constants(decl):
        lines.append(f"# {comment}")
        if isinstance(value, dict):
            fn = f"smd_{name.lower()}"
            arms = " ".join(f"{k}) echo '{v}' ;;" for k, v in value.items())
            lines.append(f'{fn}() {{ case "$1" in {arms} *) return 1 ;; esac; }}')
            lines.append(f"SMD_{name}_KEYS='{' '.join(value)}'")
        else:
            lines.append(f"SMD_{name}='{' '.join(value)}'")  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


def outputs(root: Path) -> dict[Path, str]:
    """Every generated file and the text it must hold."""
    decl = load(root / CONTRACT_REL)
    py = render_python(decl)
    out = {root / SHELL_REL: render_shell(decl), root / TS_REL: render_ts(decl)}
    out.update({root / rel: py for rel in PYTHON_RELS})
    return out
