"""Revise mode: an attorney's requested changes to a brief the Operator already
filed, made to THAT brief and filed as new versions of the same files.

Why (2026-11-04, matter 200045): an attorney replied in the delivery thread
asking for nine edits to the filed mediation brief. The Operator queued a fresh
drafting job, which re-read all 3,631 pages, spent $56.64, and produced a draft
its own gate refused. The edits were then made by hand to the filed brief at
about nothing. This package is that hand route as code.

Doctrine Law 18: the model returns VALUES (a structured checklist, edited
section text, a per-item reading) and code acts on them: it validates the
checklist's shape and that every item quotes the attorney, keeps every section
no item names byte for byte, refuses any figure, date, quotation or citation an
edit brings in that the brief or the attorney's own words do not carry, checks
each item it can (citations of a kind gone, counts per section, spacing and
table styling in the Word XML), files new versions in place with a
compare-and-stop and a read-back by id, size and sha256.

Modules: ``sections`` (the brief as sections), ``checklist`` (the request as
items), ``edit`` (the section edits), ``checks`` (code checks and invariants),
``reading`` (the narrow model check of what code cannot), ``prior`` (the filed
brief as markdown, proven), ``versions`` (new versions in place), ``run`` (the
job), ``rehearse`` (the $0 rehearsal against an answer key).
"""
