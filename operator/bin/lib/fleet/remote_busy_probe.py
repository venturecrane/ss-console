"""Seat-side busy probe. Runs ON THE MACHINE as root, under the image's python3.

Shipped by ``fleet`` over ``fly ssh console --pty=false -C 'python3 -I -'`` on
stdin, so it must stand alone: stdlib only, no package imports, and it must
work on images built before any fleet tooling existed (the first fleet release
is a bootstrap: old seats emit no jobs_running in their heartbeat). Everything
it reads has existed on every seat since the runner lanes shipped:

  /run/smd-medchron/*heartbeat.json   each lane daemon's liveness + counts
  /run/smd-medchron/*child.pid        a lane's running job child
  /opt/data/profiles/*/cron/jobs.json Hermes' per-profile cron store
  /opt/data/cron/jobs.json            the root cron store
  /run/smd-medchron/quiesce           the fleet's no-new-work marker

It DECIDES NOTHING: it prints one JSON line of facts and the operator side
(busy.decide) judges them, so the judgement is unit-tested on the laptop.
Reads only; never writes.
"""

import glob
import json
import os
import time
from datetime import datetime, timezone

RUN = "/run/smd-medchron"
CRON_STORES = ("/opt/data/profiles/*/cron/jobs.json", "/opt/data/cron/jobs.json")
MANAGED_PREFIX = "op-managed:"


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def started_at(pid):
    """When process ``pid`` started (epoch seconds), from /proc; None if unknown."""
    try:
        with open("/proc/%d/stat" % int(pid)) as fh:
            ticks = int(fh.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as fh:
            btime = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, TypeError, IndexError, StopIteration):
        return None


def epoch(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def lanes(now, errors):
    out = []
    for path in sorted(glob.glob(RUN + "/*heartbeat.json")):
        try:
            with open(path) as fh:
                hb = json.load(fh)
        except (OSError, ValueError) as exc:
            errors.append("%s unreadable: %s" % (path, exc))
            continue
        polled = epoch(hb.get("last_poll_at"))
        alive = pid_alive(hb.get("pid"))
        began = started_at(hb.get("pid")) if alive else None
        out.append(
            {
                "file": os.path.basename(path),
                "age_s": None if polled is None else round(now - polled, 1),
                "running": hb.get("running"),
                "queued": hb.get("queued"),
                "wakes_pending": hb.get("wakes_pending"),
                "daemon_alive": alive,
                # A beat last written before this daemon started was written by
                # its predecessor: a lane the running daemon does not run.
                "written_before_daemon": None if polled is None or began is None else polled < began - 5,
                "quiesced": hb.get("quiesced"),
            }
        )
    return out


def children(errors):
    out = []
    for path in sorted(glob.glob(RUN + "/*child.pid")):
        try:
            with open(path) as fh:
                raw = fh.read().strip()
        except OSError as exc:
            errors.append("%s unreadable: %s" % (path, exc))
            continue
        out.append({"file": os.path.basename(path), "pid": raw, "alive": pid_alive(raw)})
    return out


def crons(now, errors):
    out = []
    for pattern in CRON_STORES:
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path) as fh:
                    store = json.load(fh)
            except (OSError, ValueError) as exc:
                errors.append("%s unreadable: %s" % (path, exc))
                continue
            for job in store.get("jobs") or [] if isinstance(store, dict) else []:
                if not isinstance(job, dict) or job.get("enabled") is False:
                    continue
                name = str(job.get("name") or "")
                if not name.startswith(MANAGED_PREFIX):
                    continue
                nxt = epoch(job.get("next_run_at"))
                last = epoch(job.get("last_run_at"))
                out.append(
                    {
                        "store": path,
                        "name": name,
                        "next_in_s": None if nxt is None else round(nxt - now, 1),
                        "last_ago_s": None if last is None else round(now - last, 1),
                    }
                )
    return out


def quiesce():
    try:
        with open(RUN + "/quiesce") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main():
    now = time.time()
    errors = []
    facts = {
        "now": now,
        "run_dir_present": os.path.isdir(RUN),
        "lanes": lanes(now, errors),
        "children": children(errors),
        "crons": crons(now, errors),
        "quiesce": quiesce(),
        "errors": errors,
    }
    print("SMD_BUSY_PROBE " + json.dumps(facts, sort_keys=True))


main()
