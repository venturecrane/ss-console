# The month's plan record

Store: `marketing-plan`. Record: `plan-<YYYY-MM>.md`. Written by the PICKS
mode of the blog skill on the reviewer's reply to the topic slate, updated by
each drafting run, read by the social, Business Profile and analytics skills.

```
---
month: 2026-11
picked_by: reviewer@example.com
picked_on: 2026-11-03
slate_subject: Topic slate for November 2026
---

1. Title: What California's e-bike rules mean for riders
   Why: new statute in effect January 1
   Channels: blog, social
   Sources: https://leginfo.legislature.ca.gov/example, https://www.dmv.ca.gov/example
   Blog: queued
   Social: none yet

4. Title: Walking safely after dark as the days get shorter
   Why: seasonal, pedestrian crashes rise in late autumn
   Channels: blog, social, gbp
   Sources: https://www.ots.ca.gov/example
   Blog: pending 4812
   Social: drafted 2026-11-06 (Facebook, Instagram)
```

Field values:

- `Blog`: `queued`, `drafting <YYYY-MM-DD>`, `pending <WordPress post id>`,
  `held <rule>`, `dropped`, or `not for blog`.
- `Social`: `none yet`, or `drafted <YYYY-MM-DD> (<networks>)`, one per run,
  newest last.
- `Gbp`: present only once a Business Profile post used the topic:
  `drafted <YYYY-MM-DD>`.
- An entry the reviewer added in their own words carries `Added: <date>` and
  is numbered after the slate's highest number.

Rules: the numbers are the slate's numbers, never renumbered. An entry is never
deleted; a dropped topic says `dropped`. Every write reads the record first and
writes it back whole with `overwrite: true`, and is read back before anything
is reported from it. On a turn that reads the web, every write happens before
the first web read.
