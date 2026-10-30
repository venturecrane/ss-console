# Post list: the email

Rendered by `pre_run.py` (`render`, `subject`, `skeleton`). This page is the
shape, not a template the turn fills.

Subject: `Post list <reference>: <n> need a word from you, <m> to calendar`
(either part omitted when it is zero). The reference is the seat's date,
`2026-10-29`, or `2026-10-29-2` for a second list the same day; a reply's
subject carries it back.

```
Hello,

Post since the last list: 3 scans, 14 letters, 12 filed on their matters.

Needs a word from you:
1. Scan of 10:14 AM ("Post 10-29.pdf"), pages 7-8, letter from Radiology Associates: the only matter I found is <matter-number>. Is it that one?
2. Scan of 10:14 AM ("Post 10-29.pdf"), pages 9-9, medical bill from Valley Clinic: filed on matter <matter-number>, but it is not on the Medicals tab (the bill prints no total).
3. Scan of 2:02 PM ("Scan 2.pdf"), the whole scan (31 pages) could not be read (the scan could not be read all the way through), so nothing in it was filed.

Filed, needs calendaring:
- Scan of 10:14 AM ("Post 10-29.pdf"), pages 12-13, court paper from Superior Court, filed on matter <matter-number>.

Reply to this email with each item's number and its matter number, and I will file them. One reply can answer every item.
```

The counts line counts every letter in the window; "filed" is every letter
that was placed on a matter, listed or not. The reply line appears only when
there is a numbered item. No em dash anywhere.

Skeleton (sent only if the full body is refused): the count of items and that
the list could not be included, with no sender, page or matter number.
