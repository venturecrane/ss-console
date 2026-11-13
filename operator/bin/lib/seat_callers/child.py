"""Run ONE site over every input and write what it did.

Invoked by the capture as `python child.py <spec.json>` in a clean environment.
The site's module is imported once (under the sandbox); each input then runs in
a fork of that process, so module state left by one input (a cache, a global)
never reaches the next, and a site that hangs is killed at the deadline.
Inline sites (module-level code) are imported or run inside the fork instead.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import re
import runpy
import select
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != HERE]
sys.path.insert(0, str(HERE.parent.parent.parent))  # operator/, so `bin.lib.seat_callers` imports
sys.dont_write_bytecode = True

from bin.lib.seat_callers import inputs as inp  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers import loader, sandbox, sockserver, yamltrack  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.args import Ctx, NoValue, kwargs_for  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.canon import Normaliser, canon, digest, shape  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.sites import OVERRIDES  # noqa: E402 - imported only once sys.path holds the operator tree

DEADLINE = 20.0
# A seat directory or file variable (..._HOME, ..._RUN_DIR, ..._DB_PATH): pointed into the input's scratch.
SEAT_DIR = re.compile(r"^[A-Z0-9]+(_[A-Z0-9]+)*_(DIR|HOME|ROOT|PATH)$")


class Site:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.repo = Path(spec["repo"])
        self.tmp = Path(spec["tmp"])
        self.sockdir = Path(spec["sockdir"])
        self.file = self.repo / spec["file"]
        self.key = f"{spec['file']}::{spec['qualname'] or spec['line']}"
        self.override = OVERRIDES.get(self.key, {})
        self.module: Any = None
        self.env_names: set[str] = set()
        # Read by main() BEFORE the sandbox is installed: under it every path named customer.yaml is
        # redirected to the current input, so Path.glob's lstat of each candidate found none (0 real rows).
        self.reals: list[tuple[str, str]] = []
        self.norm = Normaliser(
            [
                (str(self.tmp), "<TMP>"),
                (str(self.sockdir), "<SOCK>"),
                (str(self.repo), "<REPO>"),
                (os.path.expanduser("~"), "<HOME>"),
            ]
        )


# ---------------------------------------------------------------- one input


def _materialise(site: Site, input_id: str, kind: str, text: str | None) -> Ctx:
    base = site.tmp / "in" / input_id
    scratch = base / "scratch"
    seat_dir = base / "customers" / inp.GOOD_SLUG
    for d in (scratch, seat_dir):
        d.mkdir(parents=True, exist_ok=True)
    for target in (base / "customer.yaml", seat_dir / "customer.yaml"):
        if kind == "dir":
            target.mkdir(exist_ok=True)
        elif kind == "file" and text is not None:
            # The harness's own write: past the customer.yaml redirect, which is for the site.
            with sandbox.original("open")(target, "w", encoding="utf-8") as fh:
                fh.write(text)
    return Ctx(
        path=base / "customer.yaml",
        text=text if kind == "file" else None,
        tmp=scratch,
        slug=inp.GOOD_SLUG,
        customers_dir=base / "customers",
        sock=str(site.sockdir / "b.sock"),
        repo=site.repo,
    )


def _slug_names(site: Site) -> list[str]:
    """The slug variable and every other slug-named variable the site was seen to read."""
    return sorted({inp.SLUG_VAR} | {n for n in site.env_names if "SLUG" in n})


def _set_env(site: Site, state: dict[str, str]) -> None:
    for name in _slug_names(site):
        os.environ.pop(name, None)
    resolved = {k: v for k, v in state.items() if k != inp.ALTERNATES}
    if inp.ALTERNATES in state:
        resolved |= {n: state[inp.ALTERNATES] for n in _slug_names(site) if n != inp.SLUG_VAR}
    os.environ.update(resolved)


def _callable(site: Site, ctx: Ctx) -> Any:
    qualname = site.spec["qualname"]
    target = loader.resolve(site.module, qualname)
    if "." not in qualname:
        return target
    owner_name, method = qualname.rsplit(".", 1)
    owner = loader.resolve(site.module, owner_name)
    if not isinstance(owner, type) or isinstance(inspect.getattr_static(owner, method), staticmethod | classmethod):
        return target
    if "instance" in site.override:
        return getattr(site.override["instance"](ctx, site.module), method)
    init = site.override.get("init", lambda _c, _m: {})(ctx, site.module)
    instance = owner(**kwargs_for(owner, ctx, init))
    return getattr(instance, method)


def _call(site: Site, ctx: Ctx) -> Any:
    kind = site.spec["kind"]
    if kind == "function":
        fn = _callable(site, ctx)
        given = {**site.spec.get("bind", {}), **site.override.get("kwargs", lambda _c, _m: {})(ctx, site.module)}
        return fn(**kwargs_for(fn, ctx, given))
    if kind == "script":
        argv = site.override.get("argv", lambda _c, _m: [])(ctx, site.module)
        sys.argv = [str(site.file), *argv]
        return runpy.run_path(str(site.file), run_name="__main__").get(site.spec.get("assign") or "", None)
    module = loader.load(site.file, site.repo)
    return getattr(module, site.spec["assign"]) if site.spec.get("assign") else None


def _outcome(site: Site, ctx: Ctx) -> dict[str, Any]:
    if "skip" in site.override:
        return {"outcome": "not-executable", "reason": site.override["skip"]}
    out: dict[str, Any] = {}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                value = _call(site, ctx)
            finally:
                sandbox.STATE.track = False  # what the harness reads to digest the value is not the site's read
    except sandbox.Blocked as exc:
        out = {"outcome": "blocked", "kind": exc.kind}
    except NoValue as exc:
        if exc.inapplicable:
            out = {"outcome": "not-applicable", "reason": f"the site takes {exc.name}"}
        else:
            out = {"outcome": "not-executable", "reason": f"no synthetic value for parameter {exc.name}"}
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int | None) else "message"
        out = {"outcome": "exit", "code": code}
    except BaseException as exc:  # noqa: BLE001 - every raise is the behaviour being recorded
        out = {"outcome": "raise", "type": type(exc).__name__}
    else:
        out = {"outcome": "return", "shape": shape(value), "digest": digest(value, site.norm)}
        if site.spec["concept"] == "slug":
            out.update(_slug_view(value, site.norm))
    printed = buf.getvalue()
    if printed:
        out["stdout"] = digest(printed, site.norm)
    return out


def _slug_view(value: Any, norm: Normaliser) -> dict[str, Any]:
    text = json.dumps(canon(value, norm), sort_keys=True)
    view: dict[str, Any] = {"mentions": [s for s in (inp.GOOD_SLUG, inp.ALT_SLUG) if s in text]}
    if value is None or isinstance(value, bool | int) or (isinstance(value, str) and len(value) <= 64):
        view["value"] = value
    return view


def _seat_paths(site: Site, ctx: Ctx) -> dict[str, str]:
    """The seat's own path variables, pointed at this input: every name the site was seen to read
    that names a socket gets the socket target, every one naming the customer.yaml gets its path."""
    found = {}
    for name in site.env_names:
        if "SOCK" in name:
            found[name] = ctx.sock
        elif "CUSTOMER_YAML" in name or "CUSTOMER_CONFIG" in name:
            found[name] = str(ctx.path)
        elif SEAT_DIR.search(name):
            found[name] = str(ctx.tmp / name.lower())
    return found


def _in_fork(site: Site, ctx: Ctx, apply: dict[str, Any]) -> dict[str, Any]:
    sandbox.block_fork()
    _set_env(site, {**_seat_paths(site, ctx), **apply.get("env", {})})
    sandbox.STATE.yaml_target = str(ctx.path)
    sandbox.STATE.yaml_text = ctx.text
    sandbox.STATE.track = bool(apply.get("track"))
    scenario = apply.get("scenario")
    sandbox.STATE.sock_target = ctx.sock if scenario else str(site.sockdir / "absent.sock")
    recorder = sockserver.Recorder(ctx.sock, scenario) if scenario else None
    if recorder is not None:
        recorder.start()
        ctx.payload = apply["payload"]
    try:
        out = _outcome(site, ctx)
    finally:
        if recorder is not None:
            recorder.stop()
    out["reads"] = sandbox.STATE.reads
    out["unix_connects"] = len(sandbox.STATE.unix_connects)
    if recorder is not None:
        out["wire"] = recorder.wire()
    if sandbox.STATE.blocked and out["outcome"] != "blocked":
        out["blocked_attempts"] = sorted(set(sandbox.STATE.blocked))
    if apply.get("track"):
        out["accessed"] = [[list(p), t] for p, t in sandbox.STATE.accessed.items()]
    out["env_reads"] = sorted(sandbox.STATE.env_reads)
    return out


def _read_child(fd: int, pid: int) -> dict[str, Any]:
    data, end = b"", time.monotonic() + DEADLINE
    while True:
        left = end - time.monotonic()
        ready, _, _ = select.select([fd], [], [], max(left, 0))
        if not ready:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            return {"outcome": "timeout"}
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        data += chunk
    os.waitpid(pid, 0)
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        return {"outcome": "crashed"}


def run_input(site: Site, ctx: Ctx, apply: dict[str, Any]) -> dict[str, Any]:
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            out = _in_fork(site, ctx, apply)
        except BaseException as exc:  # noqa: BLE001 - the harness itself failed; recorded, never hidden
            out = {"outcome": "harness-error", "type": type(exc).__name__}
        payload = json.dumps(out, sort_keys=True).encode("utf-8")
        while payload:
            payload = payload[os.write(write_fd, payload) :]
        os._exit(0)
    os.close(write_fd)
    try:
        return _read_child(read_fd, pid)
    finally:
        os.close(read_fd)


# ---------------------------------------------------------------- the input sets


def _learn_paths(site: Site, ctx: Ctx, apply: dict[str, Any]) -> None:
    """Unrecorded runs that learn which seat path variables the site reads (names only)."""
    for _ in range(2):  # a second pass sees names read only once the first ones are set
        site.env_names |= set(run_input(site, ctx, apply).get("env_reads", []))


def _yaml_runs(site: Site) -> list[dict[str, Any]]:
    good = inp.good_text()
    _learn_paths(site, _materialise(site, "learn", "file", good), {})
    # The block a site reads is learned from the synthetic document only: a key the
    # site asks for is seen even when absent, and nothing a real seat authors (a
    # person's address used as a key, a block one seat added) reaches the table or
    # moves the mutated inputs.
    probe = run_input(site, _materialise(site, "probe", "file", good), {"track": True})
    accessed: dict[tuple, str] = {tuple(p): t for p, t in probe.get("accessed", [])}
    rows = [{"input": "good", **run_input(site, _materialise(site, "good", "file", good), {})}]
    for input_id, kind, text in inp.FAILURE_INPUTS:
        rows.append({"input": input_id, **run_input(site, _materialise(site, input_id, kind, text), {})})
    for input_id, text in inp.block_inputs(accessed).items():
        if text is None:
            rows.append(
                {"input": input_id, "outcome": "not-applicable", "reason": "no customer.yaml key observed read"}
            )
        else:
            rows.append({"input": input_id, **run_input(site, _materialise(site, input_id, "file", text), {})})
    for rid, text in site.reals:
        rows.append({"input": rid, "real": True, **run_input(site, _materialise(site, rid, "file", text), {})})
    observed = sorted(".".join(map(str, p)) for p in accessed if p and p[-1] != "*")
    rows.append({"input": "$observed-keys", "keys": observed})
    return rows


def _socket_runs(site: Site) -> list[dict[str, Any]]:
    good = _materialise(site, "good", "file", inp.good_text())
    _learn_paths(site, good, {"scenario": "ok", "payload": inp.PAYLOADS[0][1]})
    rows = []
    for scenario in inp.SOCKET_SCENARIOS:
        payloads = inp.PAYLOADS if scenario == "ok" else inp.PAYLOADS[:1]
        for pid, payload in payloads:
            res = run_input(site, good, {"scenario": scenario, "payload": payload})
            rows.append({"input": f"{scenario}/{pid}", **res})
    return rows


def _slug_runs(site: Site) -> list[dict[str, Any]]:
    good = _materialise(site, "good", "file", inp.good_text())
    _learn_paths(site, good, {"env": dict(inp.SLUG_STATES[2][1])})
    rows = [{"input": sid, **run_input(site, good, {"env": state})} for sid, state in inp.SLUG_STATES]
    rows.append({"input": "$slug-names", "names": _slug_names(site)})
    return rows


def main(spec_path: str) -> None:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    site = Site(spec)
    loader.setup_path(site.repo)
    if spec["concept"] == "customer-yaml":
        site.reals = [(rid, path.read_text(encoding="utf-8")) for rid, path in inp.real_inputs(site.repo)]
    sandbox.install(str(site.tmp))
    yamltrack.install()
    _set_env(site, {})
    sandbox.STATE.yaml_target = str(site.tmp / "absent" / "customer.yaml")
    result: dict[str, Any] = {"key": site.key}
    if spec["kind"] == "function" and "skip" not in site.override:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                site.module = loader.load(site.file, site.repo)
        except sandbox.Blocked as exc:
            result["import_error"] = f"import attempts a side effect: {exc.kind}"
        except BaseException as exc:  # noqa: BLE001 - an import failure is recorded as the reason the site cannot run here
            name = getattr(exc, "name", None) or ""
            result["import_error"] = f"import raises {type(exc).__name__}{': ' + name if name else ''}"
            if isinstance(exc, ModuleNotFoundError):
                result["import_error"] += " (absent from the bare CI environment the table is captured in)"
    if "import_error" not in result:
        runner = {"customer-yaml": _yaml_runs, "broker-socket": _socket_runs, "slug": _slug_runs}[spec["concept"]]
        result["runs"] = runner(site)
    out = site.tmp / "result.json"
    with sandbox.original("open")(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, sort_keys=True)
    shutil.rmtree(site.tmp / "in", ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1])
