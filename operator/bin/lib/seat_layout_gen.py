"""Project every seat-layout output from operator/contracts/seat-layout.yaml.

The yaml is the one place a seat path is declared. This module resolves it
(``{parent}/child`` derivations, cycle and shape checks) and renders each
committed output: a shell file, a JSON file for the seat probe, a TypeScript
module for the console (src/), and one Python
module per install tree that cannot import another tree's code (the jobs
bundle, the workspace broker, the overlay, the laptop fleet tools). The trees
are separate on a seat and the jobs bundle is built at a different commit from
the image (ADR 0103 decision 3), which is why each carries its own generated
copy instead of reading a shared file at runtime.

operator/bin/gen-seat-layout.py writes the outputs; --check fails when one
differs, and operator/bin/tests/test_seat_layout_generation.py runs it in CI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GENERATOR = "operator/bin/gen-seat-layout.py"
CONTRACT_REL = "operator/contracts/seat-layout.yaml"

SHELL_REL = "operator/templates/_seat-layout.generated.sh"
JSON_REL = "operator/templates/seat-layout.generated.json"
TS_REL = "src/lib/operator/seat-layout.generated.ts"
PYTHON_RELS = (
    "operator/runners/jobs/jobs/seat_layout.py",
    "operator/workspace_broker/seat_layout.py",
    "operator/hermes-smd-overlay/shared/seat_layout.py",
    "operator/bin/lib/seat_layout.py",
)

_ID = re.compile(r"\A[a-z][a-z0-9_]*\Z")
_ENV = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")
_REF = re.compile(r"\{([a-z][a-z0-9_]*)\}")
# Every output quotes a path verbatim (shell single quotes, Python and TS string
# literals), so a path is limited to characters none of them needs to escape.
_PATH_CHARS = re.compile(r"\A[A-Za-z0-9/._-]+\Z")
_FIELDS = {"path", "env", "expect", "kind", "when", "volume", "bundle_mount"}
_EXPECT = {"present", "absent", "lazy"}
_KIND = {"dir", "file"}
_WHEN = {"always", "lanes"}


@dataclass(frozen=True)
class Entry:
    id: str
    path: str
    env: tuple[str, ...] = ()
    expect: str | None = None
    kind: str | None = None
    when: str = "always"
    volume: bool = False
    bundle_mount: bool = False


@dataclass(frozen=True)
class Layout:
    entries: dict[str, Entry]
    restated: list[dict] = field(default_factory=list)
    left_out: list[dict] = field(default_factory=list)


def repo_root() -> Path:
    """ss-console repo root: operator/bin/lib/seat_layout_gen.py -> parents[3]."""
    return Path(__file__).resolve().parents[3]


def _resolve(raw: dict[str, dict], key: str, seen: tuple[str, ...]) -> str:
    if key in seen:
        raise ValueError(f"seat layout: derivation cycle {' -> '.join((*seen, key))}")
    spec = raw.get(key)
    if spec is None:
        raise ValueError(f"seat layout: {seen[-1] if seen else key} refers to unknown id {key!r}")
    path = spec.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"seat layout: {key} has no path")
    resolved = _REF.sub(lambda m: _resolve(raw, m.group(1), (*seen, key)), path)
    if not _PATH_CHARS.match(resolved):
        raise ValueError(f"seat layout: {key} resolves to {resolved!r}, which holds a character no output can quote")
    if not resolved.startswith("/") or "//" in resolved or resolved.endswith("/"):
        raise ValueError(f"seat layout: {key} resolves to {resolved!r}, not a clean absolute path")
    return resolved


def _entry(key: str, spec: dict, path: str) -> Entry:
    unknown = set(spec) - _FIELDS
    if unknown:
        raise ValueError(f"seat layout: {key} has unknown fields {sorted(unknown)}")
    env = tuple(spec.get("env") or ())
    if any(not isinstance(v, str) or not _ENV.match(v) for v in env):
        raise ValueError(f"seat layout: {key} env must be UPPER_SNAKE names")
    expect = spec.get("expect")
    kind = spec.get("kind")
    when = spec.get("when", "always")
    if expect is not None and expect not in _EXPECT:
        raise ValueError(f"seat layout: {key} expect {expect!r} not in {sorted(_EXPECT)}")
    if kind is not None and kind not in _KIND:
        raise ValueError(f"seat layout: {key} kind {kind!r} not in {sorted(_KIND)}")
    if when not in _WHEN:
        raise ValueError(f"seat layout: {key} when {when!r} not in {sorted(_WHEN)}")
    return Entry(
        id=key,
        path=path,
        env=env,
        expect=expect,
        kind=kind,
        when=when,
        volume=bool(spec.get("volume", False)),
        bundle_mount=bool(spec.get("bundle_mount", False)),
    )


def load(path: Path) -> Layout:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("paths")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{path}: no `paths` map")
    entries: dict[str, Entry] = {}
    for key in sorted(raw):
        if not _ID.match(key) or not isinstance(raw[key], dict):
            raise ValueError(f"seat layout: bad id or entry {key!r}")
        entries[key] = _entry(key, raw[key], _resolve(raw, key, ()))
    envs = [e for entry in entries.values() for e in entry.env]
    dupes = sorted({e for e in envs if envs.count(e) > 1})
    if dupes:
        raise ValueError(f"seat layout: env names on more than one id: {dupes}")
    restated = data.get("restated") or []
    for row in restated:
        missing = [i for i in row.get("ids", []) if i not in entries]
        if missing or not row.get("file") or not row.get("why"):
            raise ValueError(f"seat layout: restated row {row!r} is incomplete or names unknown ids")
    left_out = data.get("left_out") or []
    declared = {e.path for e in entries.values()}
    for row in left_out:
        if not row.get("path") or not row.get("why") or row["path"] in declared:
            raise ValueError(f"seat layout: left_out row {row!r} is incomplete or names a declared path")
    return Layout(entries=entries, restated=restated, left_out=left_out)


def _header(comment: str) -> str:
    return (
        f"{comment} GENERATED by {GENERATOR} from {CONTRACT_REL}.\n"
        f"{comment} DO NOT EDIT BY HAND. Regenerate: python3 {GENERATOR}\n"
        f"{comment} CI checks it is in sync (operator/bin/tests/test_seat_layout_generation.py).\n"
    )


def render_shell(layout: Layout) -> str:
    body = "".join(f"SMD_LAYOUT_{e.id.upper()}='{e.path}'\n" for e in layout.entries.values())
    head = _header("#") + "# Assignments only: sourcing this file exports nothing.\n"
    return f"{head}# shellcheck shell=bash disable=SC2034 # read by the scripts that source this file\n{body}"


def render_json(layout: Layout) -> str:
    doc = {
        "$generated": f"GENERATED by {GENERATOR} from {CONTRACT_REL}. DO NOT EDIT BY HAND.",
        "paths": {
            e.id: {
                k: v
                for k, v in {
                    "path": e.path,
                    "env": list(e.env),
                    "expect": e.expect,
                    "kind": e.kind,
                    "when": e.when,
                }.items()
                if v not in (None, [])
            }
            for e in layout.entries.values()
        },
    }
    return json.dumps(doc, indent=2, sort_keys=False) + "\n"


def render_python(layout: Layout) -> str:
    lines = [
        _header("#"),
        '"""Seat paths, generated from operator/contracts/seat-layout.yaml.',
        "",
        "Read a path as ``os.environ.get(VAR) or seat_layout.NAME`` where the yaml",
        "declares an env override, else as ``seat_layout.NAME``. Stdlib only.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    lines += [f'{e.id.upper()} = "{e.path}"' for e in layout.entries.values()]
    lines += ["", "PATHS: dict[str, str] = {"]
    lines += [f'    "{e.id}": {e.id.upper()},' for e in layout.entries.values()]
    lines += ["}", ""]
    return "\n".join(lines)


def render_ts(layout: Layout) -> str:
    """The console's copy (src/), prettier-stable: one exported const."""
    body = "".join(f"  {e.id}: '{e.path}',\n" for e in layout.entries.values())
    return (
        f"{_header('//')}\n"
        "/** Seat paths, generated from operator/contracts/seat-layout.yaml. */\n"
        f"export const SEAT_LAYOUT = {{\n{body}}} as const\n"
    )


_SHELL_REF = re.compile(r"\$\{SMD_LAYOUT_([A-Z0-9_]+)\}")


def expand_shell(text: str, root: Path | None = None) -> str:
    """A shell script's text as the seat runs it: each ``${SMD_LAYOUT_<ID>}``
    replaced by the value the generated shell file assigns. For tests that
    assert on (or extract and run) a boot script's text. An unassigned id
    raises: under ``set -u`` it would kill the boot."""
    shell = ((root or repo_root()) / SHELL_REL).read_text(encoding="utf-8")
    values = dict(re.findall(r"^SMD_LAYOUT_([A-Z0-9_]+)='([^']*)'$", shell, re.M))

    def value(m: re.Match[str]) -> str:
        if m.group(1) not in values:
            raise KeyError(f"SMD_LAYOUT_{m.group(1)} is not assigned in {SHELL_REL}")
        return values[m.group(1)]

    return _SHELL_REF.sub(value, text)


def outputs(root: Path) -> dict[Path, str]:
    """Every generated file and the text it must hold."""
    layout = load(root / CONTRACT_REL)
    py = render_python(layout)
    out = {
        root / SHELL_REL: render_shell(layout),
        root / JSON_REL: render_json(layout),
        root / TS_REL: render_ts(layout),
    }
    out.update({root / rel: py for rel in PYTHON_RELS})
    return out
