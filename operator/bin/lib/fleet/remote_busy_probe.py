"""Seat-side busy probe. Runs ON THE MACHINE as root, under the image's python3.

Shipped by ``fleet`` over ``fly ssh console --pty=false -C 'python3 -I -'`` on
stdin, so it must stand alone: stdlib only, no package imports, and it must
work on images built before any fleet tooling existed (the first fleet release
is a bootstrap: old seats emit no jobs_running in their heartbeat). Everything
it reads has existed on every seat since the runner lanes shipped:

  /run/smd-jobs/*heartbeat.json   each lane daemon's liveness + counts
  /run/smd-jobs/*child.pid        a lane's running job child
  /run/smd-jobs/*jobs/*/daemon.json  a finished job's DELIVER wake + reply watch
  /opt/data/profiles/*/gateway_state.json  the gateway's own in-flight turn count
  /opt/data/profiles/*/cron/jobs.json Hermes' per-profile cron store
  /opt/data/cron/jobs.json            the root cron store
  /run/smd-jobs/quiesce           the fleet's no-new-work marker

On a seat whose image predates the ADR 0103 volume move the run dir is
/run/smd-medchron, and that is the one read when /run/smd-jobs is absent.

It DECIDES NOTHING: it prints one JSON line of facts and the operator side
(busy.decide) judges them, so the judgement is unit-tested on the laptop.
Reads only; never writes.
"""

import glob
import json
import os
import time
from datetime import datetime, timezone

RUN = "/run/smd-jobs"
# ADR 0103 R2: this probe ships from the newest tooling to EVERY seat, the one a
# rollout is about to move included. A seat still on the image before the move
# binds the same tree at the old run dir only; reading the new name alone there
# finds no lane, no child and no turn, and calls a seat with a job running idle.
RUN_BEFORE_R2 = "/run/smd-medchron"
if not os.path.isdir(RUN) and os.path.isdir(RUN_BEFORE_R2):
    RUN = RUN_BEFORE_R2
CRON_STORES = ("/opt/data/profiles/*/cron/jobs.json", "/opt/data/cron/jobs.json")
MANAGED_PREFIX = "op-managed:"
TURN_SCAN_S = 1800
GATEWAY_STATES = ("/opt/data/gateway_state.json", "/opt/data/profiles/*/gateway_state.json")


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


def turns(now, errors):
    """DELIVER turns the daemons started recently whose reply is not yet seen.

    A consumed wake is a gateway turn, not a lane job: the child is gone and
    the heartbeat says idle while the turn is still sending (2026-11-02 A&P:
    seven offer emails dispatched at 09:48:38, the release restarted the
    Machine at 09:48:48, and four never went). Only job dirs touched in the
    last TURN_SCAN_S are read, so the scan stays small on a seat holding
    72 h of finished jobs."""
    out = []
    for path in sorted(glob.glob(RUN + "/*jobs/*/daemon.json")):
        try:
            if now - os.path.getmtime(path) > TURN_SCAN_S:
                continue
            with open(path) as fh:
                st = json.load(fh)
        except (OSError, ValueError) as exc:
            errors.append("%s unreadable: %s" % (path, exc))
            continue
        wake = st.get("wake") if isinstance(st, dict) else None
        if not isinstance(wake, dict) or wake.get("pending"):
            continue  # a pending wake is already counted by its lane's heartbeat
        sent = epoch(wake.get("sent_at"))
        if sent is None:
            continue
        watch = st.get("reply_watch") if isinstance(st.get("reply_watch"), dict) else {}
        out.append(
            {
                "file": "/".join(path.split("/")[-3:-1]),
                "sent_ago_s": round(now - sent, 1),
                "reply_open": bool(watch) and not watch.get("done") and not watch.get("proven_at"),
            }
        )
    return out


def gateways(errors):
    """Each gateway's in-flight turn count, as the gateway itself publishes it.

    Hermes rewrites ``active_agents`` in its launch home's gateway_state.json at
    every turn boundary. A turn answering an inbound email is no lane job and no
    cron: before this read, a restart killed it with "drain timed out after 0.0s
    with N active agent(s)" and nothing in the gate could have seen it."""
    out = []
    for pattern in GATEWAY_STATES:
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path) as fh:
                    st = json.load(fh)
            except (OSError, ValueError) as exc:
                errors.append("%s unreadable: %s" % (path, exc))
                continue
            if not isinstance(st, dict):
                continue
            out.append(
                {
                    "file": path,
                    "alive": pid_alive(st.get("pid")),
                    "state": st.get("gateway_state"),
                    "active_agents": st.get("active_agents"),
                    "quiet_s": conversation_quiet_s(os.path.dirname(path), time.time()),
                }
            )
    return out


def conversation_quiet_s(home, now):
    """Seconds since this gateway last wrote a conversation message; None if unknown.

    A turn writes its user message when it starts and every tool call and reply
    as it goes, so a turn count with no message written in a long time is a
    count the gateway failed to release, not work (captain-mark 2026-11-02: an
    interrupted reply left ``active_agents`` at 2 with the seat idle, and the
    gate held every release). This runs as root, so the database is opened
    read-only (``mode=ro``): SQLite then creates no journal or WAL file, and no
    root-owned file is left for the hermes user to trip on (the 2026-07-16 cron
    lesson). A database it cannot open that way reads as unknown.
    """
    db = os.path.join(home, "state.db")
    if not os.path.exists(db):
        return None
    try:
        import sqlite3

        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2)
        try:
            row = con.execute("SELECT MAX(timestamp) FROM messages").fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - unknown is reported as None, which the gate reads as busy
        return None
    last = row[0] if row else None
    if not isinstance(last, (int, float)):
        return None
    return round(now - float(last), 1)


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
        "turns": turns(now, errors),
        "gateways": gateways(errors),
        "quiesce": quiesce(),
        "errors": errors,
    }
    print("SMD_BUSY_PROBE " + json.dumps(facts, sort_keys=True))


main()
