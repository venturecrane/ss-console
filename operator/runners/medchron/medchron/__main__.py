"""CLI: `medchron run <job_dir> [--from STAGE] [--dry-run] [--firm-config PATH]
[--pricing PATH] [--json]`, `medchron dag`, `medchron validate-config PATH`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod, dag, driver as driver_mod


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        # Progress goes to stderr: with --json, stdout is the machine-read
        # verdict and nothing else (the daemon parses it; live-caught
        # 2026-08-31 when interleaved [run] lines made the report unreadable
        # and a real refusal recorded as "exited 4 without a verdict").
        d = driver_mod.Driver(Path(args.job_dir), firm_config=args.firm_config, pricing=args.pricing,
                              dry_run=args.dry_run, start=args.start,
                              log=lambda m: print(m, file=sys.stderr))
        outcomes = d.run()
    except (driver_mod.DriverError, config_mod.ConfigError) as exc:
        print(f"medchron: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the envelope or budget refused; the CLI prints a sentence, never a trace
        print(f"medchron: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(driver_mod.to_json(outcomes) if args.json else driver_mod.report(outcomes))
    worst = {"delivered": 0, "dry_run": 0, "held": 3, "refused": 4, "failed": 1}
    return max(worst.get(o.outcome, 1) for o in outcomes) if outcomes else 1


def _cmd_dag(_args: argparse.Namespace) -> int:
    problems = dag.validate_dag()
    for s in dag.STAGES:
        kind = "decide" if s.decision else ("paid" if s.paid else "free")
        print(f"{s.name:24s} {kind:6s} {s.scope:5s} {s.script}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = config_mod.load(args.path)
    except config_mod.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{cfg.path}: OK ({cfg.slug})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="medchron")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a job envelope through the DAG")
    r.add_argument("job_dir")
    r.add_argument("--from", dest="start", default=None, help="resume from this stage")
    r.add_argument("--dry-run", action="store_true", help="author nothing, run nothing, report decisions and holds")
    r.add_argument("--firm-config", default=None)
    r.add_argument("--pricing", default=None)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_run)
    d = sub.add_parser("dag", help="print the stage order and validate it")
    d.set_defaults(fn=_cmd_dag)
    v = sub.add_parser("validate-config", help="validate a firm config file")
    v.add_argument("path")
    v.set_defaults(fn=_cmd_validate)
    pr = sub.add_parser("probe", help="run a registered gate's planted violation; exit 0 only when it is refused")
    pr.add_argument("gate", choices=["claim_audit", "extractive", "cross_client", "provenance"])
    pr.set_defaults(fn=lambda a: __import__("medchron.probes", fromlist=["run_probe"]).run_probe(a.gate))
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
