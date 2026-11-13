"""A real filing by ``jobs/filing.py`` on SMD's own staging tenant, before any
client seat runs the code (wave 3, the plan's gate 5).

    python -m jobs.filing_probe

Run ON the staging seat (``seat-probe.sh ap-staging``) with the seat's own
``ClientSeat`` and its own token: the laptop cannot write (``SshSeat`` is
read-only) and a laptop refresh could rotate the seat's token. It refuses
unless all three hold, the first two before it builds a client:

* the seat's slug (``seat_config.slug``) is on ``ALLOWED_SEATS``;
* ``PROBE_MATTER_ID`` is filled in: one fictional matter on the staging tenant,
  read off it (read-only) and committed here;
* the client the seat builds from its own environment is on Smokeball's
  ``staging`` environment (``SmokeballClient.environment``, which selects the
  host every write goes to).

Then, every file stamped ``[SMD-PROBE <ts>]``:

1. the dated-folder path (``file_into_new_folder``): two files into a new
   folder, proven by hash; then run again, which must send NOTHING;
2. the standing-folder path (``file_into_standing_folder``): one file into a
   new standing folder, proven; then run again, which must send nothing;
3. teardown: every stamped file deleted by id and the listing polled until each
   reads gone. The folders cannot be deleted through the seat; they are named in
   the output for a person to remove.

Exit 0 only when every step held; 1 when any did not, or while any stamped file
still reads present (the output names each). Nothing here touches any matter
but ``PROBE_MATTER_ID``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import filing, seat_config

#: The seats this probe may write on: SMD's own staging twin, nothing else.
ALLOWED_SEATS = frozenset({"ap-staging"})
#: The one fictional matter on the staging tenant the probe files into
#: (OPS-OPERATOR-LIBRARY, read off ap-staging read-only on 2026-11-13). The probe
#: refuses while it is empty.
PROBE_MATTER_ID = "84693f0c-ddc7-4d9d-8921-26dce238f96c"
#: Polls of the listing, and the pause between them, before a deleted file
#: that still reads present fails the probe.
GONE_TRIES = 20
GONE_PAUSE_SECONDS = 15.0


def refusal(slug: str | None, environment: str | None = "staging") -> str:
    """Why this process may not file, or "" when it may. ``environment`` is the
    built client's (None before it is built)."""
    if slug not in ALLOWED_SEATS:
        return f"the seat {slug!r} is not on the probe's allowlist {sorted(ALLOWED_SEATS)}"
    if not PROBE_MATTER_ID:
        return "PROBE_MATTER_ID is empty: read the fictional matter's id off the staging tenant and commit it"
    if environment != "staging":
        return f"the seat's Smokeball client is on {environment!r}, not 'staging'"
    return ""


class CountingSeat:
    """The seat, with every file write counted (the second runs must send none)."""

    def __init__(self, seat: Any) -> None:
        self._seat = seat
        self.sends: list[str] = []

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict[str, Any]:
        self.sends.append(name)
        return self._seat.add_file(matter_id, folder_id, name, data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._seat, name)


def _stamp() -> str:
    return f"[SMD-PROBE {time.strftime('%Y%m%dT%H%M%S', time.gmtime())}]"


def _dated(seat: CountingSeat, work: Path, stamp: str, say: Any) -> tuple[bool, str]:
    out, folder = work / "dated" / "out" / "probe", f"{stamp} dated"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i in (1, 2):
        p = out / f"{stamp} dated {i}.txt"
        p.write_bytes(f"{stamp} dated-folder probe file {i}\n".encode())
        data = p.read_bytes()
        manifest.append(
            {
                "name": p.name,
                "folder": folder,
                "local_path": str(p),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    (out / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for attempt in ("first", "second"):
        before = len(seat.sends)
        rc = filing.file_into_new_folder(lambda: seat, PROBE_MATTER_ID, work / "dated", "probe", say)
        if rc != 0:
            return False, f"dated folder, {attempt} run: exit {rc}"
        if attempt == "second" and len(seat.sends) != before:
            return False, f"dated folder: the second run sent {seat.sends[before:]}"
    rec = json.loads((work / "dated" / "runs" / "probe" / "delivery.json").read_text(encoding="utf-8"))
    return True, str(rec.get("folder"))


def _standing(seat: CountingSeat, work: Path, stamp: str, say: Any) -> tuple[bool, str]:
    data, folder = work / "standing", f"{stamp} standing"
    data.mkdir(parents=True, exist_ok=True)
    local = data / f"{stamp} standing.txt"
    local.write_bytes(f"{stamp} standing-folder probe file\n".encode())
    for attempt in ("first", "second"):
        before = len(seat.sends)
        try:
            filing.file_into_standing_folder(seat, PROBE_MATTER_ID, folder, local, data, say)
        except (filing.FilingHeld, filing.FilingFailed) as exc:
            return False, f"standing folder, {attempt} run: {exc}"
        if attempt == "second" and len(seat.sends) != before:
            return False, f"standing folder: the second run sent {seat.sends[before:]}"
    return True, folder


def _stamped(seat: Any, stamp: str) -> list[dict[str, Any]]:
    return [f for f in seat.list_files(PROBE_MATTER_ID) if not f.get("deleted") and stamp in filing.full_name(f)]


def teardown(
    seat: Any, stamp: str, say: Any, *, tries: int = GONE_TRIES, pause: float = GONE_PAUSE_SECONDS
) -> list[str]:
    """Delete every stamped file by id, then poll until each reads gone;
    returns the full names still present (empty is a clean teardown). A
    listing that fails proves nothing gone, so it is reported as left."""
    try:
        for f in _stamped(seat, stamp):
            say(f"deleting {filing.full_name(f)} (id {f.get('id')})")
            seat.delete_file(PROBE_MATTER_ID, str(f["id"]))
    except Exception as exc:  # noqa: BLE001 - the deletes stopped; the poll below reports what is left
        say(f"teardown: the deletes stopped ({type(exc).__name__}: {str(exc)[:120]})")
    left: list[str] = []
    for attempt in range(tries):
        try:
            left = [filing.full_name(f) for f in _stamped(seat, stamp)]
        except Exception as exc:  # noqa: BLE001 - a teardown that cannot read the matter has not proven anything gone
            left = [f"(the matter could not be read: {type(exc).__name__}: {str(exc)[:120]})"]
        if not left:
            say("teardown: every stamped file reads deleted")
            return []
        if attempt + 1 < tries:
            time.sleep(pause)
    return left


def run(seat: Any, say: Any = print, *, gone_tries: int = GONE_TRIES, gone_pause: float = GONE_PAUSE_SECONDS) -> int:
    """The probe over ``seat`` (the caller has checked ``refusal``)."""
    stamp, counted, failed = _stamp(), CountingSeat(seat), []
    say(f"filing probe {stamp} on matter {PROBE_MATTER_ID}")
    try:
        with tempfile.TemporaryDirectory(prefix="filing-probe-") as tmp:
            for name, step in (("dated folder", _dated), ("standing folder", _standing)):
                try:
                    ok, detail = step(counted, Path(tmp), stamp, say)
                except Exception as exc:  # noqa: BLE001 - a step that raises fails the probe and still reaches the teardown
                    ok, detail = False, f"{type(exc).__name__}: {str(exc)[:200]}"
                say(f"{name}: {'PROVEN, second run sent nothing' if ok else 'FAILED'} ({detail})")
                if not ok:
                    failed.append(name)
    finally:
        left = teardown(seat, stamp, say, tries=gone_tries, pause=gone_pause)
    say(f"folders left for a person to remove: '{stamp} dated', '{stamp} standing'")
    if left:
        say(f"FAILED: {len(left)} stamped file(s) still read present: {', '.join(left)}")
    return 1 if failed or left else 0


def main(argv: list[str] | None = None) -> int:
    slug = seat_config.slug(on_missing="none")
    why = refusal(slug)
    if not why:
        from .seat import ClientSeat

        seat = ClientSeat()
        why = refusal(slug, str(getattr(seat.client, "environment", "") or ""))
    if why:
        print(f"filing probe REFUSED: {why}", file=sys.stderr)
        return 2
    print(f"filing probe: seat {slug}, Smokeball host {getattr(seat.client, 'api_host', '?')}")
    return run(seat)


if __name__ == "__main__":
    sys.exit(main())
