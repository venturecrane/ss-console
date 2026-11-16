"""The store of paid page text in SMD's work digest.

Every job lane reads a firm's pages through one store (wave 5,
``runners/jobs/jobs/matter/store.py``): a page a model already read the same
way is served from it instead of paid for again. A store that is off, or never
hit, looks exactly like a store working on new files, and the cost shows up
only as a bill. So each job that read a page reports ``page_store`` on its
final audit row (``workspace_broker/page_store_meta.py``: ``{store, hits,
misses}``, counts only), and this module turns those rows into two digest
lines per lane:

* ``page_store_off``: the lane's newest such job ran with the store OFF (the
  seat's page-store env var did not reach the child), so every page is paid
  for again.
* ``page_store_cold``: the lane's last ``page_store_cold_jobs`` (work-health.yaml,
  3) jobs that each paid for pages hit the store not once.

Pure: rows, the contract and a clock in, items out.
"""

from __future__ import annotations

from datetime import datetime

from bin.lib.work_health import LANE_PREFIXES, Config, Item, _JOB_TYPE, hhmm, item, meta, stamped

ENDED = ("DELIVERED", "HELD", "FAILED")


def _runs(rows: list[dict]) -> dict[str, list[tuple[datetime, str, dict]]]:
    """{lane: [(when, job id, page_store)]}, oldest first, one per job (its last row)."""
    by_job: dict[tuple[str, str], tuple[datetime, str, dict]] = {}
    for ts, row in stamped(rows):
        match = _JOB_TYPE.match(str(row.get("action_type") or ""))
        if not match or match.group(2) not in ENDED:
            continue
        md = meta(row)
        store, job_id = md.get("page_store"), md.get("job_id")
        if not isinstance(store, dict) or store.get("store") not in ("on", "off") or not isinstance(job_id, str):
            continue
        lane = LANE_PREFIXES[match.group(1)]
        by_job[(lane, job_id)] = (ts, job_id, store)
    out: dict[str, list[tuple[datetime, str, dict]]] = {}
    for (lane, _job), run in sorted(by_job.items(), key=lambda kv: kv[1][0]):
        out.setdefault(lane, []).append(run)
    return out


def _count(store: dict, key: str) -> int:
    v = store.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def page_store_items(seat: str, rows: list[dict], cfg: Config, now: datetime) -> list[Item]:
    out: list[Item] = []
    for lane, runs in _runs(rows).items():
        label = cfg.lanes[lane].label if lane in cfg.lanes else lane
        ts, job_id, store = runs[-1]
        if store["store"] == "off":
            out.append(
                item(
                    seat,
                    "page_store_off",
                    f"{lane}:{job_id}:page_store_off",
                    ts,
                    f"The {label} job that ended {hhmm(ts)} read pages with the page store off, so every page "
                    "it read was paid for again.",
                    "Check that the seat's boot exported the page store's dir (seat-layout jobs_textcache) and that the lane's child got it "
                    "(seat-checks <lane>-page-store-dir-child-only; python -m jobs.textcache stats on the seat).",
                )
            )
            continue
        paid = [r for r in runs if _count(r[2], "misses")]
        last = paid[-cfg.page_store_cold_jobs :] if cfg.page_store_cold_jobs > 0 else []
        if len(last) >= cfg.page_store_cold_jobs > 0 and not any(_count(r[2], "hits") for r in last):
            pages = sum(_count(r[2], "misses") for r in last)
            out.append(
                item(
                    seat,
                    "page_store_cold",
                    f"{lane}:{last[-1][1]}:page_store_cold",
                    last[-1][0],
                    f"The last {len(last)} {label} jobs paid for {pages} page(s) and the page store served none.",
                    "Run python -m jobs.textcache stats on the seat: no hits across jobs means the store is not "
                    "keeping pages (quiet writes, a full disk, or a method key that changes every job).",
                )
            )
    return out


__all__ = ["page_store_items"]
