"""CLI: `medchron run <job_dir> [--from STAGE] [--dry-run] [--firm-config PATH]
[--pricing PATH] [--json]`, `medchron rehearse <job_dir> [--redo STAGE,...]
[--firm-config PATH] [--pricing PATH] [--json]`, `medchron dag`,
`medchron validate-config PATH`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod, dag, driver as driver_mod, rehearsal, verdict as verdict_mod


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        # Progress goes to stderr: with --json, stdout is the machine-read
        # verdict and nothing else (the daemon parses it; live-caught
        # 2026-08-31 when interleaved [run] lines made the report unreadable
        # and a real refusal recorded as "exited 4 without a verdict").
        # Routing our own lines away from stdout fixed the lines we emit and
        # nothing else, so the verdict is also written to a file the daemon
        # prefers (ss#2906, verdict.py) -- a library's banner on stdout can no
        # longer cost a finished run its outcome.
        d = driver_mod.Driver(
            Path(args.job_dir),
            firm_config=args.firm_config,
            pricing=args.pricing,
            dry_run=args.dry_run,
            start=args.start,
            redo=tuple(x.strip() for x in (getattr(args, "redo", "") or "").split(",") if x.strip()),
            log=lambda m: print(m, file=sys.stderr),
        )
        outcomes = d.run()
    except (driver_mod.DriverError, config_mod.ConfigError) as exc:
        print(f"medchron: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the envelope or budget refused; the CLI prints a sentence, never a trace
        print(f"medchron: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    # Only a real run writes the file. A rehearsal is handed the SAME job dir
    # and must never leave a verdict there: the daemon would read it as the
    # next attempt's outcome.
    verdict_mod.write(Path(args.job_dir), driver_mod.to_json(outcomes))
    print(driver_mod.to_json(outcomes) if args.json else driver_mod.report(outcomes))
    return _exit_code(outcomes)


def _cmd_demand(args: argparse.Namespace) -> int:
    """One demand job (``demand/run.py``). Same verdict contract as ``run``:
    the JSON list goes to verdict.json and stdout, progress to stderr."""
    import json

    from .demand import firm as demand_firm, job as demand_job
    from .demand.run import DemandRun

    try:
        r = DemandRun(
            Path(args.job_dir), inputs_dir=args.inputs, pricing=args.pricing, log=lambda m: print(m, file=sys.stderr)
        )
    except (demand_firm.DemandConfigError, demand_job.DemandJobError) as exc:
        print(f"medchron: {exc}", file=sys.stderr)
        return 2
    v = r.run()
    payload = json.dumps(v.to_list())
    verdict_mod.write(Path(args.job_dir), payload)
    print(payload)
    return {"delivered": 0, "held": 3, "failed": 1}.get(v.outcome, 1)


def _cmd_draft(args: argparse.Namespace) -> int:
    """One drafting job (``drafting/run.py``). Same verdict contract as
    ``demand``: the JSON list goes to verdict.json and stdout, progress to
    stderr. ``--redo`` (a resume request's stages) reopens those stages."""
    import json

    from .drafting import firm as drafting_firm, job as drafting_job
    from .drafting.run import DraftingRun

    try:
        r = DraftingRun(
            Path(args.job_dir), inputs_dir=args.inputs, pricing=args.pricing, log=lambda m: print(m, file=sys.stderr)
        )
    except (drafting_firm.DraftingConfigError, drafting_job.DraftingJobError) as exc:
        print(f"medchron: {exc}", file=sys.stderr)
        return 2
    for stage in (x.strip() for x in (args.redo or "").split(",") if x.strip()):
        st = r._state()
        if st.pop(stage, None) is not None:
            r._put(stage, {"status": "reopened"})
    v = r.run()
    payload = json.dumps(v.to_list())
    verdict_mod.write(Path(args.job_dir), payload)
    print(payload)
    return {"delivered": 0, "held": 3, "failed": 1}.get(v.outcome, 1)


def _cmd_litigate(args: argparse.Namespace) -> int:
    """One litigation status job (``litigation/run.py``). The verdict is ONE
    JSON object: the only line on stdout, and the same text in verdict.json.
    Everything else the run (or a library it imports) writes to stdout is
    sent to stderr. Exit 0 delivered, 1 held, 2 failed."""
    import contextlib

    from .litigation import firm as lit_firm, job as lit_job
    from .litigation.outcome import Verdict
    from .litigation.run import LitigationRun

    with contextlib.redirect_stdout(sys.stderr):
        try:
            r = LitigationRun(
                Path(args.job_dir),
                inputs_dir=args.inputs,
                pricing=args.pricing,
                log=lambda m: print(m, file=sys.stderr),
            )
        except lit_job.LitigationJobError as exc:
            v = Verdict("failed", stage="envelope", reason=f"envelope_invalid: {exc}")
        except lit_firm.LitigationConfigError as exc:
            v = Verdict("failed", stage="config", reason=f"config_missing: {str(exc)[:300]}")
        except Exception as exc:  # noqa: BLE001 - a verdict, never a trace on stdout
            v = Verdict("failed", stage="setup", reason=f"unexpected: {type(exc).__name__}: {str(exc)[:300]}")
        else:
            r.reopen([x.strip() for x in (args.redo or "").split(",") if x.strip()])
            v = r.run()
    payload = v.to_json()
    verdict_mod.write(Path(args.job_dir), payload)
    print(payload)
    return v.exit_code


def _exit_code(outcomes) -> int:
    worst = {"delivered": 0, "dry_run": 0, "rehearsed": 0, "held": 3, "refused": 4, "failed": 1}
    return max(worst.get(o.outcome, 1) for o in outcomes) if outcomes else 1


def _cmd_rehearse(args: argparse.Namespace) -> int:
    """Every $0 gate against a COPY of the job's workdir; nothing spent, nothing
    real touched. The workdir path goes to stderr; the verdict is stdout."""
    redo = tuple(x.strip() for x in (args.redo or "").split(",") if x.strip())
    try:
        _copy, outcomes = rehearsal.run(
            Path(args.job_dir),
            firm_config=args.firm_config,
            pricing=args.pricing,
            log=lambda m: print(m, file=sys.stderr),
            redo=redo,
        )
    except (driver_mod.DriverError, config_mod.ConfigError, rehearsal.RehearsalError) as exc:
        print(f"medchron: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - same contract as run: a sentence, never a trace
        print(f"medchron: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(driver_mod.to_json(outcomes) if args.json else driver_mod.report(outcomes))
    return _exit_code(outcomes)


def _cmd_dag(_args: argparse.Namespace) -> int:
    problems = dag.validate_dag()
    for s in dag.STAGES:
        kind = "decide" if s.decision else ("paid" if s.paid else ("ext" if s.external else "free"))
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


def _cmd_explain_date(args: argparse.Namespace) -> int:
    """Record why a billed date of service is not in the chronology, so a held
    dos_check passes on resume. Internal only: nothing here reaches the firm."""
    import datetime
    import json
    from pathlib import Path

    from . import dos_check, job as job_mod

    try:
        datetime.date.fromisoformat(args.date)
    except ValueError:
        print(f"medchron: {args.date!r} is not a date in YYYY-MM-DD form")
        return 2
    if not args.reason.strip():
        print("a reason is required")
        return 2
    try:
        job = job_mod.load(Path(args.job_dir))
    except Exception as exc:  # noqa: BLE001 - the CLI prints a sentence, never a trace
        print(f"medchron: {type(exc).__name__}: {exc}")
        return 2
    run = job.data_root / job.slug / "runs" / args.unit
    if not run.is_dir():
        print(f"no run directory {run}")
        return 2
    path = run / dos_check.EXPLAINED_FILE
    rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    rows.append({"date": args.date, "reason": args.reason.strip()})
    path.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    print(f"recorded {args.date} in {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="medchron")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a job envelope through the DAG")
    r.add_argument("job_dir")
    r.add_argument("--from", dest="start", default=None, help="resume from this stage")
    r.add_argument(
        "--redo",
        default="",
        help="comma-separated stages to reopen before the walk; a resume names the stages the fix touched",
    )
    r.add_argument("--dry-run", action="store_true", help="author nothing, run nothing, report decisions and holds")
    r.add_argument("--firm-config", default=None)
    r.add_argument("--pricing", default=None)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_run)
    rh = sub.add_parser("rehearse", help="run every $0 gate against a copy of the workdir; spend nothing")
    rh.add_argument("job_dir")
    rh.add_argument("--redo", default="", help="comma-separated $0 stages to reopen in the copy before the walk")
    rh.add_argument("--firm-config", default=None)
    rh.add_argument("--pricing", default=None)
    rh.add_argument("--json", action="store_true")
    rh.set_defaults(fn=_cmd_rehearse)
    dm = sub.add_parser("demand", help="run one demand job (gap audit + draft demand) from pull to read-back")
    dm.add_argument("job_dir")
    dm.add_argument("--inputs", default=None, help="the demand firm-inputs dir (default: MEDCHRON_DEMAND_INPUTS)")
    dm.add_argument("--pricing", default=None)
    dm.set_defaults(fn=_cmd_demand)
    dr = sub.add_parser("draft", help="run one drafting job (one document of one class) from pull to read-back")
    dr.add_argument("job_dir")
    dr.add_argument("--inputs", default=None, help="the drafting firm-inputs dir (default: MEDCHRON_DRAFTING_INPUTS)")
    dr.add_argument("--pricing", default=None)
    dr.add_argument("--redo", default="", help="comma-separated stages to reopen (a resume request)")
    dr.set_defaults(fn=_cmd_draft)
    lt = sub.add_parser("litigate", help="run one litigation status job, inventory to read-back")
    lt.add_argument("job_dir")
    lt.add_argument("--inputs", default=None, help="the litigation inputs dir (default: MEDCHRON_LITIGATION_INPUTS)")
    lt.add_argument("--pricing", default=None)
    lt.add_argument("--redo", default="", help="comma-separated stages to reopen (a resume request)")
    lt.set_defaults(fn=_cmd_litigate)
    d = sub.add_parser("dag", help="print the stage order and validate it")
    d.set_defaults(fn=_cmd_dag)
    v = sub.add_parser("validate-config", help="validate a firm config file")
    v.add_argument("path")
    v.set_defaults(fn=_cmd_validate)
    pr = sub.add_parser("probe", help="run a registered gate's planted violation; exit 0 only when it is refused")
    pr.add_argument("gate", choices=["claim_audit", "extractive", "cross_client", "provenance"])
    pr.set_defaults(fn=lambda a: __import__("medchron.probes", fromlist=["run_probe"]).run_probe(a.gate))
    ex = sub.add_parser("explain-date", help="record why a billed date of service is not in the chronology")
    ex.add_argument("job_dir")
    ex.add_argument("unit")
    ex.add_argument("date", help="YYYY-MM-DD")
    ex.add_argument("reason")
    ex.set_defaults(fn=_cmd_explain_date)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
