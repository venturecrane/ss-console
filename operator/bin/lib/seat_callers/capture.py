"""Run every site in its own child and assemble the caller table."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import inputs as inp
from .discover import CONCEPTS, SiteSpec, discover
from .loader import tree_of
from .modes import fails_open, modes

CHILD = Path(__file__).resolve().parent / "child.py"
SITE_DEADLINE = 600
CENSUS_IDS = {
    "customer-yaml": "customer-yaml-hand-parsers",
    "broker-socket": "broker-socket-clients",
    "slug": "customer-slug-reads",
}


def _clean_env(tmp: Path) -> dict[str, str]:
    home = tmp / "home"
    (tmp / "t").mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp / "t"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def run_site(spec: SiteSpec, repo: Path, python: str) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="seatcall-")).resolve()
    sockdir = Path(tempfile.mkdtemp(prefix="sc", dir="/tmp")).resolve()
    try:
        payload = {**asdict(spec), "repo": str(repo), "tmp": str(tmp), "sockdir": str(sockdir)}
        spec_file = tmp / "spec.json"
        spec_file.write_text(json.dumps(payload), encoding="utf-8")
        proc = subprocess.run(  # noqa: S603 - argv is this interpreter plus the tracked child script and a spec file this run wrote
            [python, str(CHILD), str(spec_file)],
            env=_clean_env(tmp),
            cwd=str(tmp),
            capture_output=True,
            timeout=SITE_DEADLINE,
        )
        if os.environ.get("SEAT_CALLERS_DEBUG"):
            sys.stderr.write(f"== {spec.file}::{spec.qualname}\n{proc.stderr.decode('utf-8', 'replace')[-4000:]}\n")
        out = tmp / "result.json"
        if out.exists():
            return json.loads(out.read_text(encoding="utf-8"))
        return {"child_error": f"child exited {proc.returncode} with no result"}
    except subprocess.TimeoutExpired:
        return {"child_error": "site deadline exceeded"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)


def _brief(run: dict[str, Any], keep_digest: bool) -> str:
    o = run.get("outcome")
    if o == "return":
        text = f"return {run.get('shape')}" + (f" {run.get('digest')}" if keep_digest else "")
    elif o == "raise":
        text = f"raise {run.get('type')}"
    elif o == "exit":
        text = f"exit {run.get('code')}"
    elif o == "blocked":
        text = f"blocked {run.get('kind')}"
    elif o in ("not-executable", "not-applicable"):
        text = f"{o}: {run.get('reason')}"
    else:
        text = str(o)
    extras = []
    if run.get("reads"):
        extras.append(f"reads={run['reads']}")
    if run.get("unix_connects"):
        extras.append(f"connects={run['unix_connects']}")
    if run.get("blocked_attempts"):
        extras.append(f"attempted={','.join(run['blocked_attempts'])}")
    if run.get("stdout") and keep_digest:
        extras.append(f"stdout={run['stdout']}")
    return " ".join([text, *extras])


def _status(spec: SiteSpec, result: dict[str, Any]) -> tuple[str, str | None]:
    if "child_error" in result:
        return "not-executable", result["child_error"]
    if "import_error" in result:
        return "not-executable", result["import_error"]
    runs = [r for r in result.get("runs", []) if not str(r["input"]).startswith("$")]
    reasons = sorted({r.get("reason", "") for r in runs if r.get("outcome") == "not-executable"})
    if runs and all(r.get("outcome") in ("not-executable", "not-applicable") for r in runs):
        return "not-executable", "; ".join(reasons)
    kinds = sorted({r.get("kind", "") for r in runs if r.get("outcome") == "blocked"})
    if runs and all(r.get("outcome") in ("blocked", "not-applicable") for r in runs):
        return "not-executable", f"attempts a side effect on every input: {', '.join(kinds)}"
    return ("inline" if spec.kind in ("module", "script") else "executable"), None


def _row(spec: SiteSpec, result: dict[str, Any], real_digests: bool) -> dict[str, Any]:
    status, reason = _status(spec, result)
    runs = result.get("runs", [])
    row: dict[str, Any] = {
        "file": spec.file,
        "symbol": spec.qualname or f"<{spec.kind}>:{spec.line}",
        "tree": tree_of(spec.file),
        "kind": spec.kind,
        "status": status,
        "calls": [f"{spec.file}:{c['line']} {c['expr']}" for c in spec.calls],
    }
    if spec.assign:
        row["assign"] = spec.assign
    if spec.bind:
        row["bind"] = spec.bind
    if reason:
        row["reason"] = reason
    if status == "not-executable":
        return row
    row["behaviour"] = {
        r["input"]: _brief(r, real_digests or not r.get("real")) for r in runs if not r["input"].startswith("$")
    }
    for extra in (r for r in runs if r["input"] == "$observed-keys"):
        row["observed_keys"] = extra["keys"]
    if spec.concept == "broker-socket":
        row["wire"] = {r["input"]: r.get("wire", []) for r in runs if r.get("wire")}
    if spec.concept == "slug":
        row["slug_values"] = {r["input"]: r.get("value") for r in runs if "value" in r}
    row["modes"] = modes(spec.concept, runs)
    return row


def capture(
    repo: Path, python: str = sys.executable, jobs: int = 0, only: str = "", real_digests: bool = False
) -> dict[str, Any]:
    files, sites = discover(repo)
    if only:
        sites = [s for s in sites if only in s.file]
    workers = jobs or max(2, (os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda s: run_site(s, repo, python), sites))
    table: dict[str, Any] = {}
    for concept in CONCEPTS:
        rows = [_row(s, r, real_digests) for s, r in zip(sites, results, strict=True) if s.concept == concept]
        rows.sort(key=lambda r: (r["file"], r["symbol"]))
        with_sites = {r["file"] for r in rows}
        table[concept] = {
            "census": CENSUS_IDS[concept],
            "files": files[concept],
            "files_without_sites": [f for f in files[concept] if f not in with_sites],
            "rows": rows,
            "fail_open": [
                {"file": r["file"], "symbol": r["symbol"], "labels": fails_open(r.get("modes", []))}
                for r in rows
                if fails_open(r.get("modes", []))
            ],
        }
    return table


def summary(table: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for concept, body in table.items():
        by_tree: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for r in body["rows"]:
            by_tree[r["tree"]] = by_tree.get(r["tree"], 0) + 1
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        out[concept] = {
            "files": len(body["files"]),
            "sites": len(body["rows"]),
            "by_tree": dict(sorted(by_tree.items())),
            "by_status": dict(sorted(by_status.items())),
            "fail_open": len(body["fail_open"]),
        }
    return out


def document(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "$comment": (
            "GENERATED by operator/bin/capture-seat-config-callers.py; do not hand-edit. Every customer.yaml hand "
            "parser, broker socket client and CUSTOMER_SLUG read, EXECUTED over fixed inputs; behaviour is what it "
            "returned (shape and a content-free digest) or raised. Modes are derived from behaviour by "
            "operator/bin/lib/seat_callers/modes.py. Real customer.yaml inputs are named by a hash of their folder "
            "and recorded by outcome class only, so a seat's config edit does not move this file; pass "
            "--with-real-digests for the digests. Regenerate: python3 operator/bin/capture-seat-config-callers.py "
            "--write. Gate: operator/bin/tests/test_seat_config_callers.py."
        ),
        "inputs": {
            "customer-yaml": [
                "good",
                *[i for i, _, _ in inp.FAILURE_INPUTS],
                *inp.BLOCK_INPUTS,
                "real-<sha256(folder)[:10]>",
            ],
            "broker-socket": [
                f"{s}/{p}" for s in inp.SOCKET_SCENARIOS for p, _ in (inp.PAYLOADS if s == "ok" else inp.PAYLOADS[:1])
            ],
            "slug": [s for s, _ in inp.SLUG_STATES],
            "payloads": {pid: payload for pid, payload in inp.PAYLOADS},
        },
        "summary": summary(table),
        "concepts": table,
    }
