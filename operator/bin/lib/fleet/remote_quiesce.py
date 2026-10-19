"""Seat-side quiesce writer. Runs ON THE MACHINE as root, under the image's python3.

``fleet`` prepends one line, ``ARGS = {...}``, and ships the result on stdin
(see busy.quiesce_source). ARGS["action"] is "set" (write the marker with an
expiry) or "clear" (remove it, only if this fleet run wrote it, or, with
ARGS["prefix"], if any fleet run wrote it; a marker the seat's own supervisor
wrote is never removed from here).

The marker is the contract the runner lanes honor (WS3): a root-owned
/run/smd-medchron/quiesce {"until": epoch, "by", "reason"}; while it exists and
``until`` is in the future, no lane claims new work. An expired marker is
ignored by the lanes, so a laptop that dies mid-rollout never starves a queue
for longer than the expiry. Written atomically (temp + rename), 0644 root:root.
"""

import json
import os
import time

RUN = "/run/smd-medchron"
MARK = RUN + "/quiesce"

ARGS = globals().get("ARGS") or {}


def set_marker():
    if not os.path.isdir(RUN):
        print("SMD_QUIESCE no-run-dir")
        return
    body = {"until": float(ARGS["until"]), "by": str(ARGS["by"]), "reason": str(ARGS["reason"]), "set_at": time.time()}
    tmp = RUN + "/.quiesce.tmp"
    with open(tmp, "w") as fh:
        json.dump(body, fh)
    os.chmod(tmp, 0o644)
    os.chown(tmp, 0, 0)
    os.replace(tmp, MARK)
    print("SMD_QUIESCE set " + json.dumps(body))


def clear_marker():
    try:
        with open(MARK) as fh:
            current = json.load(fh)
    except (OSError, ValueError):
        print("SMD_QUIESCE absent")
        return
    by = str(current.get("by") or "")
    prefix = ARGS.get("prefix")
    if by != ARGS.get("by") and not (prefix and by.startswith(prefix)):
        print("SMD_QUIESCE kept (set by %s)" % by)
        return
    os.unlink(MARK)
    print("SMD_QUIESCE cleared")


if ARGS.get("action") == "set":
    set_marker()
elif ARGS.get("action") == "clear":
    clear_marker()
else:
    print("SMD_QUIESCE bad-args")
