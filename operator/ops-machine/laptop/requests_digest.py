"""requests_digest.py [YYYY-MM-DD]: what a client asked its Operator on one day.

Runs ON THE SEAT (piped to `python3 -` by laptop/ops-requests through the
client's ops workspace), as the hermes user, read-only. Prints a digest a
person reads in a terminal: each request received that day in Phoenix time
(who, subject, what was asked, what the Operator replied), every job created
or updated that day with its state, and the instrument lines that tell an
empty day from a read that failed (total rows, newest row of any date, the
day before's count, when the index was last written).

Why it exists (2026-11-10): the daily "what did the firm ask today" check was a
free-text task handed to a workspace Claude session, which wrote this query
from scratch each time and took minutes, or 20 when the session never started.
The question is the same every day, so the answer is a script (Law 18).

Exit codes: 0 the read worked (zero requests is a real zero); 3 the index is
missing or unreadable, said in words, never reported as "none".
Standard library only; the seat image's python3 runs it.
"""

import datetime as dt
import json
import os
import sqlite3
import sys

PHOENIX = dt.timezone(dt.timedelta(hours=-7))  # Arizona keeps no daylight time
REQUESTS_DB = os.environ.get("SMD_REQUESTS_DB", "/opt/data/requests.db")
AUDIT_DB = os.environ.get("SMD_AUDIT_DB", "/opt/data/audit/audit.db")
TEXT_CHARS = 500
JOB_FIELDS = ("job_id", "id", "kind", "state", "stage", "matter_id", "matter",
              "attempt", "reason", "hold_reason", "cents", "spent_cents")
JOB_TIMES = ("created_at", "submitted_at", "updated_at")
#: One literal query per lane job table: a table name cannot be a bound
#: parameter, and a formatted one is a query built from data. A lane table the
#: seat has that is missing here is REPORTED as not read, never skipped, and
#: tests/ops-requests.test.ts fails when the Operator code creates one this
#: map lacks.
JOB_QUERIES = {
    "casefile_jobs": "select * from casefile_jobs",
    "demand_jobs": "select * from demand_jobs",
    "drafting_jobs": "select * from drafting_jobs",
    "library_setup_jobs": "select * from library_setup_jobs",
    "litigation_jobs": "select * from litigation_jobs",
    "medchron_jobs": "select * from medchron_jobs",
    "negotiation_jobs": "select * from negotiation_jobs",
    "note_jobs": "select * from note_jobs",
    "records_jobs": "select * from records_jobs",
}


def fail(why):
    print("READ FAILED: " + why)
    print("This is NOT zero requests. Nothing about today is known from this run.")
    sys.exit(3)


def stamp(epoch):
    if epoch is None:
        return "never"
    return dt.datetime.fromtimestamp(epoch, PHOENIX).strftime("%Y-%m-%d %I:%M %p MST")


def clock(epoch):
    return dt.datetime.fromtimestamp(epoch, PHOENIX).strftime("%I:%M %p").lstrip("0")


def parse_time(value):
    """A job timestamp as epoch seconds: the lanes store epoch or ISO text."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()


def clip(text):
    flat = " ".join((text or "").split())
    return flat if len(flat) <= TEXT_CHARS else flat[:TEXT_CHARS] + " [...]"


def open_ro(path):
    if not os.path.exists(path):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def columns(con, table):
    return [r[0] for r in con.execute("select name from pragma_table_info(?)", (table,))]


def day_window(arg):
    if arg:
        try:
            day = dt.datetime.strptime(arg, "%Y-%m-%d").date()
        except ValueError:
            fail("the date %r is not YYYY-MM-DD" % arg)
    else:
        day = dt.datetime.now(PHOENIX).date()
    start = dt.datetime(day.year, day.month, day.day, tzinfo=PHOENIX).timestamp()
    return day, start, start + 86400


def replies_for(con, row, cols):
    ids = [row[c] for c in ("vendor_message_id", "internet_message_id", "thread_ref")
           if c in cols and row[c]]
    found = {}
    for one in ids:
        for s in con.execute("select * from replies where in_reply_to=?", (one,)):
            found[s["sent_message_id"]] = s
    return sorted(found.values(), key=lambda s: s["sent_at"])


def print_requests(con, start, end):
    cols = columns(con, "requests")
    rows = con.execute(
        "select * from requests where received_at>=? and received_at<? order by received_at",
        (start, end),
    ).fetchall()
    print("REQUESTS RECEIVED: %d" % len(rows))
    for n, r in enumerate(rows, 1):
        print()
        print("%d. %s  from %s" % (n, clock(r["received_at"]), r["sender"] or "(no sender)"))
        print("   Subject: %s" % (r["subject"] or "(none)"))
        asked = (r["request_text"] if "request_text" in cols else None) or (
            r["request_opening"] if "request_opening" in cols else None)
        print("   Asked:   %s" % (clip(asked) or "(text not kept)"))
        sent = replies_for(con, r, cols)
        first = (r["reply_text"] if "reply_text" in cols else None) or r["reply_opening"]
        if not sent and not first:
            print("   Reply:   NONE YET")
            continue
        if sent:
            for s in sent:
                print("   Reply %s: %s" % (clock(s["sent_at"]), clip(s["text"] or s["opening"])))
        else:
            print("   Reply:   %s" % clip(first))
    return len(rows)


def job_line(table, row, cols):
    parts = [table.replace("_jobs", "")]
    for k in JOB_FIELDS:
        if k in cols and row[k] not in (None, ""):
            parts.append("%s=%s" % (k, row[k]))
    for k in JOB_TIMES:
        t = parse_time(row[k]) if k in cols else None
        if t:
            parts.append("%s=%s" % (k.replace("_at", ""), clock(t)))
    return "  ".join(parts)


def print_jobs(audit, start, end):
    print()
    if audit is None:
        print("JOBS: could not look (no %s). This is not 'no jobs'." % AUDIT_DB)
        return
    tables = [r[0] for r in audit.execute(
        "select name from sqlite_master where type='table' and name like '%_jobs' order by 1")]
    lines, attention = [], 0
    unread = [t for t in tables if t not in JOB_QUERIES]
    for t in tables:
        if t not in JOB_QUERIES:
            continue
        cols = columns(audit, t)
        for row in audit.execute(JOB_QUERIES[t]):
            times = [parse_time(row[k]) for k in JOB_TIMES if k in cols]
            if not any(x is not None and start <= x < end for x in times):
                continue
            state = str(row["state"]).lower() if "state" in cols and row["state"] else ""
            flag = "  <-- needs a look" if state in ("held", "failed") else ""
            attention += 1 if flag else 0
            lines.append("  " + job_line(t, row, cols) + flag)
    print("JOBS CREATED OR UPDATED THAT DAY: %d (%d held or failed), across %d lane table(s)"
          % (len(lines), attention, len(tables)))
    for line in lines:
        print(line)
    if unread:
        print("NOT READ: lane table(s) this script does not know: %s. Their jobs are "
              "missing above; that is not 'no jobs'." % ", ".join(unread))
    held = audit.execute(
        "select count(*) from audit_log where action_type='REPLY_HELD' and ts>=? and ts<?",
        (iso(start), iso(end)),
    ).fetchone()[0] if "audit_log" in [r[0] for r in audit.execute(
        "select name from sqlite_master where type='table'")] else None
    if held is not None:
        print("REPLIES HELD THAT DAY: %d" % held)


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def main(argv):
    day, start, end = day_window(argv[1] if len(argv) > 1 else "")
    try:
        con = open_ro(REQUESTS_DB)
        if con is None:
            fail("no request index at %s" % REQUESTS_DB)
        total = con.execute("select count(*) from requests").fetchone()[0]
        newest = con.execute("select max(received_at) from requests").fetchone()[0]
        before = con.execute(
            "select count(*) from requests where received_at>=? and received_at<?",
            (start - 86400, start),
        ).fetchone()[0]
        print("Operator requests for %s (Phoenix time), read %s"
              % (day.isoformat(), stamp(dt.datetime.now(PHOENIX).timestamp())))
        print()
        print_requests(con, start, end)
        print_jobs(open_ro(AUDIT_DB), start, end)
        print()
        print("READ CHECK: %d requests in the index in all; newest of any date %s; "
              "the day before had %d; index last written %s."
              % (total, stamp(newest), before, stamp(os.stat(REQUESTS_DB).st_mtime)))
    except sqlite3.Error as exc:
        fail("sqlite: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
