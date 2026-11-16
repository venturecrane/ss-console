"""``jobs.litigation.textcache``: the text store's old address, kept for one release.

The store moved to ``jobs/textcache.py`` (wave 5), where every lane reads it.
This name stays so a command written against it (``python -m
jobs.litigation.textcache purge --matter <id>``, the runbooks' and the handbook's)
still runs on a seat that took the move; remove it the release after.
"""

from __future__ import annotations

from ..textcache import (
    DATA_ENV as DATA_ENV,
    DEFAULT_DATA_DIR as DEFAULT_DATA_DIR,
    ENV as ENV,
    REHEARSAL_MARK as REHEARSAL_MARK,
    STORE_ENV as STORE_ENV,
    TextCache as TextCache,
    cache_dir as cache_dir,
    main as main,
    method_version as method_version,
    probe_matter as probe_matter,
    purge_matter as purge_matter,
    purge_sha as purge_sha,
    seed_job as seed_job,
    stats as stats,
    storage_error_text as storage_error_text,
)

if __name__ == "__main__":
    raise SystemExit(main())
