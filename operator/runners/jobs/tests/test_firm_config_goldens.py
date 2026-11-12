"""Every firm-settings loader's answers over its fixtures and a mutation set, against goldens.

Six lanes read their firm's settings file with six loaders that share one
skeleton (lanes review 2026-11-10, concept: firm-settings loaders): drafting,
demand, case file, litigation, records, and the chronology. Wave 2 folds them
into one ``firm_config`` kit. This is the instrument that proves the fold
changed no answer. For each lane, by calling the REAL loader:

* every existing fixture (each lane test kit's own inputs builder): the
  ``validate()`` problem list, the typed values ``load()`` returns (the loaded
  object's fields and every property), and the provisioning CLI's exit code and
  output (``python -m jobs.<lane>.firm <dir>``, the chronology's
  ``python -m jobs validate-config <path>``), plus the CLI's usage and refusal;
* a generated mutation set over the fixture: for every key the fixture
  declares (nested maps walked, list items not), the key missing, the key with
  a wrong type, the key blank, and, once per map, an unknown sibling key. Each
  mutation records ``validate()``'s problems, or the exception it raised.

``goldens/firm_config/<lane>/<fixture>.json`` holds it; ``allowed-changes.json``
names the paths that may differ (none today). ``JOBS_WRITE_KIT_GOLDENS=1``
rewrites the goldens.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

import goldens_kit as gk

FAMILY = "firm_config"
DIR = gk.GOLDENS / FAMILY
ALLOWED = gk.allowed(FAMILY)
UNKNOWN = "zz_unknown_key"
MAX_DEPTH = 4


# ---- the loaders and their fixtures -------------------------------------------------
def _medchron_fixture(root: Path) -> Path:
    from jobs.medchron.probes import SYNTHETIC_FIRM

    root.mkdir(parents=True, exist_ok=True)
    (root / "firm.yaml").write_text(yaml.safe_dump(SYNTHETIC_FIRM, sort_keys=False), encoding="utf-8")
    return root


def _records_fixture(mp: pytest.MonkeyPatch | None) -> Callable[[Path], Path]:
    def build(root: Path) -> Path:
        import records_testkit

        assert mp is not None, "the records fixture sets its environment through monkeypatch"
        root.mkdir(parents=True, exist_ok=True)
        env = records_testkit.setup_env(root, mp, records_testkit.FakeFinderModel())
        return env["inputs"]

    return build


class Lane:
    def __init__(
        self,
        module: Any,
        config_name: str,
        cli: Callable[[Path], list[str]],
        fixtures: dict[str, Callable[[Path], Path]],
        load_arg: Callable[[Path], str] = str,
    ) -> None:
        self.module, self.config_name, self.cli, self.fixtures, self.load_arg = (
            module,
            config_name,
            cli,
            fixtures,
            load_arg,
        )

    def mod(self) -> Any:
        return self.module


def lanes(mp: pytest.MonkeyPatch | None) -> dict[str, Lane]:
    import casefile_testkit
    import demand_crabtree_testkit
    import demand_numbered_testkit
    import demand_testkit
    import drafting_testkit
    import litigation_testkit
    from jobs.casefile import firm as casefile_firm
    from jobs.demand import firm as demand_firm
    from jobs.drafting import firm as drafting_firm
    from jobs.litigation import firm as litigation_firm
    from jobs.medchron import config as medchron_config
    from jobs.records import firm as records_firm

    def firm_cli(module: str) -> Callable[[Path], list[str]]:
        return lambda root: [sys.executable, "-m", module, str(root)]

    return {
        "casefile": Lane(
            casefile_firm, "casefile-firm.yaml", firm_cli("jobs.casefile.firm"), {"kit": casefile_testkit.make_inputs}
        ),
        "demand": Lane(
            demand_firm,
            "demand-firm.yaml",
            firm_cli("jobs.demand.firm"),
            {
                "kit": demand_testkit.make_inputs,
                "crabtree": demand_crabtree_testkit.make_crabtree_inputs,
                "crabtree_by_attorney": lambda r: demand_crabtree_testkit.make_crabtree_inputs(r, default=False),
                "numbered": demand_numbered_testkit.make_numbered_inputs,
            },
        ),
        "drafting": Lane(
            drafting_firm, "drafting-firm.yaml", firm_cli("jobs.drafting.firm"), {"kit": drafting_testkit.make_inputs}
        ),
        "litigation": Lane(
            litigation_firm,
            "litigation-firm.yaml",
            firm_cli("jobs.litigation.firm"),
            {"kit": litigation_testkit.make_inputs},
        ),
        "medchron": Lane(
            medchron_config,
            "firm.yaml",
            lambda root: [sys.executable, "-m", "jobs", "validate-config", str(root / "firm.yaml")],
            {"probes_synthetic_firm": _medchron_fixture},
            load_arg=lambda root: str(root / "firm.yaml"),
        ),
        "records": Lane(
            records_firm, "records-firm.yaml", firm_cli("jobs.records.firm"), {"kit": _records_fixture(mp)}
        ),
    }


# ---- what a loader answers --------------------------------------------------------
def _scrub(x: Any, tmp: Path, stamped: dict[str, str] | None = None) -> Any:
    """The temp dir out of every string, and each Word file's sha256 named
    instead of printed (a .docx the kit writes carries its creation time)."""
    if isinstance(x, str):
        x = x.replace(str(tmp), "<TMP>")
        for digest, name in (stamped or {}).items():
            x = x.replace(digest, name)
        return x
    if isinstance(x, dict):
        return {k: _scrub(v, tmp, stamped) for k, v in x.items()}
    if isinstance(x, list):
        return [_scrub(v, tmp, stamped) for v in x]
    return x


def _validate(validate: Callable[[Any], list[str]], data: Any) -> dict[str, Any]:
    try:
        return {"problems": list(validate(data))}
    except Exception as exc:  # noqa: BLE001 - a validator that raises on a bad file is a fact to pin
        return {"raises": f"{type(exc).__name__}: {exc}"}


def typed(obj: Any) -> dict[str, Any]:
    """The loaded object's fields and every public property, as plain JSON."""
    out: dict[str, Any] = {"__type": type(obj).__name__}
    for name in sorted(dir(type(obj))):
        if name.startswith("_"):
            continue
        attr = getattr(type(obj), name)
        if isinstance(attr, property):
            try:
                out[name] = gk.plain(getattr(obj, name))
            except Exception as exc:  # noqa: BLE001 - a property that raises is a fact
                out[name] = {"raises": f"{type(exc).__name__}: {exc}"}
    for name, value in sorted(vars(obj).items()) if hasattr(obj, "__dict__") else []:
        if not name.startswith("_"):
            out[name] = gk.plain(value)
    import dataclasses

    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            out[f.name] = gk.plain(getattr(obj, f.name))
    return out


def _wrong(v: Any) -> Any:
    if isinstance(v, bool):
        return "yes"
    if isinstance(v, (int, float)):
        return "not-a-number"
    if isinstance(v, str):
        return 12345
    if isinstance(v, list):
        return "not-a-list"
    if isinstance(v, dict):
        return "not-a-map"
    return 12345


def _blank(v: Any) -> Any:
    if isinstance(v, str):
        return ""
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def _paths(data: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if not isinstance(data, dict) or len(prefix) >= MAX_DEPTH:
        return []
    out = []
    for k in data:
        out.append((*prefix, str(k)))
        out += _paths(data[k], (*prefix, str(k)))
    return out


def _parents(data: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if not isinstance(data, dict) or len(prefix) >= MAX_DEPTH:
        return []
    return [prefix] + [p for k in data for p in _parents(data[k], (*prefix, str(k)))]


def _at(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = data
    for k in path:
        node = node[k]
    return node


def mutations(validate: Callable[[Any], list[str]], data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in _paths(data):
        parent, key = path[:-1], path[-1]
        value = _at(data, path)
        for kind, change in (
            ("missing", None),
            ("wrong_type", _wrong(value)),
            ("blank", _blank(value)),
        ):
            d = copy.deepcopy(data)
            node = _at(d, parent)
            if kind == "missing":
                del node[key]
            else:
                node[key] = change
            out[f"{'.'.join(path)} :: {kind}"] = _validate(validate, d)
    for parent in _parents(data):
        d = copy.deepcopy(data)
        _at(d, parent)[UNKNOWN] = "x"
        out[f"{'.'.join(parent) or '<root>'} :: unknown_sibling"] = _validate(validate, d)
    return out


def _cli(argv: list[str], tmp: Path) -> dict[str, Any]:
    p = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603 - this interpreter, a jobs module, a temp dir
    return {"exit": p.returncode, "stdout": _scrub(p.stdout, tmp), "stderr": _scrub(p.stderr, tmp)}


def capture(lane: Lane, fixture: str, tmp: Path) -> dict[str, Any]:
    mod = lane.mod()
    root = lane.fixtures[fixture](tmp / "inputs")
    cfg = root / lane.config_name
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    try:
        loaded: Any = typed(mod.load(lane.load_arg(root)))
    except Exception as exc:  # noqa: BLE001 - a fixture the loader refuses is a fact
        loaded = {"raises": f"{type(exc).__name__}: {exc}"}
    cli = {"fixture": _cli(lane.cli(root), tmp)}
    cli["no_argument"] = _cli(lane.cli(root)[:-1], tmp)
    first = next(iter(data))
    broken = copy.deepcopy(data)
    del broken[first]
    cfg.write_text(yaml.safe_dump(broken, sort_keys=False), encoding="utf-8")
    cli[f"without_{first}"] = _cli(lane.cli(root), tmp)
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    stamped = {gk.sha_bytes(p.read_bytes()): f"<sha256 of {p.name}>" for p in sorted(root.rglob("*.docx"))}
    return _scrub(
        {
            "validate": _validate(mod.validate, data),
            "load": loaded,
            "cli": cli,
            "mutations": mutations(mod.validate, data),
        },
        tmp,
        stamped,
    )


def _cases() -> list[tuple[str, str]]:
    ls = lanes(None)
    return sorted((name, fx) for name, lane in ls.items() for fx in lane.fixtures)


CASES = _cases()


def test_every_golden_has_a_case_and_an_allowed_list() -> None:
    files = {(p.parent.name, p.stem) for p in DIR.glob("*/*.json")}
    assert files == set(CASES)
    assert {f"{a}/{b}" for a, b in CASES} <= set(ALLOWED) - {"_why"}


@pytest.mark.parametrize("lane,fixture", CASES)
def test_a_firm_loader_answers_what_the_golden_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane: str, fixture: str
) -> None:
    """MUTATION: change a key's type rule, a required key, a problem sentence,
    a default ``load()`` fills, or the CLI's exit code or words in any of the
    six loaders, and its case fails, naming the path."""
    got = capture(lanes(monkeypatch)[lane], fixture, tmp_path)
    bad = gk.write_or_compare(DIR / lane / f"{fixture}.json", got, ALLOWED[f"{lane}/{fixture}"])
    assert bad == [], f"{lane}/{fixture}: {bad[:20]}"


def test_a_planted_key_rule_fails_the_records_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: one declared key dropped from the records loader's closed key
    set, and its mutation verdicts no longer match the golden."""
    from jobs.records import firm as records_firm

    monkeypatch.setattr(records_firm, "KEYS", tuple(k for k in records_firm.KEYS if k != "usd_per_page"))
    got = capture(lanes(monkeypatch)["records"], "kit", tmp_path)
    assert any(p.startswith("mutations.") for p in gk.compare(DIR / "records" / "kit.json", got))
