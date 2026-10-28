"""Boot harness: boot a CANDIDATE seat image whole, off every real seat, and judge it.

WHY THIS EXISTS. On 2026-10-27 a Hermes v0.22.5 image built, passed every repo
test, and then failed to come up on smd-staging: plugin enable made Hermes'
package manager build its own environment under /opt/data at BOOT, and that
environment lacked yaml and aiohttp ("Failed to load plugin 'hermes-smd-overlay':
No module named 'yaml'", "Webhook: aiohttp not installed"). The next build then
died in overlay activation ("No module named 'shared'"). Every one of those is a
BOOT fact: no unit test and no tests_e2e run reaches it, because tests_e2e
drives AIAgent directly with no gateway, no entrypoint, and no plugin enable.
The only instrument that saw them was a live seat. This package is the
instrument that sees them first.

THE SHAPE. One throwaway Fly Machine, in an app that is no seat's, runs the
candidate image with its REAL entrypoint and bootstrap on a staging-derived,
non-client customer.yaml (fixture.py) and per-run DUMMY secrets: the R2 endpoint
is a dead loopback port and the model key is junk, so the boot reaches no
external system at all. A stdlib-only collector (collect.py) runs on that
Machine and returns one JSON observation bundle; the judge (judge.py) turns the
bundle plus the Machine's own log into one PASS/FAIL line per act a seat must
perform. The judge is pure: tests/test_boot_harness.py feeds it the captured
signals of the failed staging boots and asserts FAIL (Law 12).

  boot-harness fixture [--out PATH]          render the harness customer.yaml
  boot-harness judge BUNDLE [--log FILE]     judge a captured bundle (offline)
  boot-harness plan --image REF              print the fly commands a run would issue
  boot-harness run --image REF               boot, collect, judge, destroy (app hermes-boot-harness)
  boot-harness run --image REF --postmortem  the same boot started under a held Machine, so a
                                             gateway that dies still leaves its state to collect
  boot-harness observe --machine ID --image REF   re-attach to a Machine a run left up

Expectations (Hermes pin, declared sub-plugins, brotlicffi override, whether the
launch path exports VIRTUAL_ENV) are read at the commit the image's rel-<sha>-
tag names, so an older known-good image is judged against its own pins.

CALIBRATION. A harness check is trusted only after it passes on an image every
seat runs green and fails on one that broke. Both are recorded in the PR:
rel-4cd01e8e-202610280041 (v2026.9.14) 18/18; rel-7e968373-202610280245 (the
staging crash loop) fails on the same lines staging showed.

WHAT IT DOES NOT PROVE (boot-path audit items with no probe here, by design
rather than by omission): a cron TOOL call audited from the gateway pid and an
attachment turn's cache/expose_cli writes (both need a model turn); an MCP stdio
child's interpreter (the fixture carries no MCP connector); a restart on the
same volume and a v2026.9.14 jobs.json surviving the first v0.22 boot (both need
a volume restored from a smd-staging snapshot); Telegram (staging has none); and
exec-to-disarm on a 1 GB Machine (the harness boots staging's 4 GB size).
"""
