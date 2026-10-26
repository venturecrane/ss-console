"""The Case File Manager: one caseload's matters read, each file's current File
Status filed in it, the caseload ranked, and numbered changes proposed for a
person to approve (``run.py``); and the apply of approved lines (``apply.py``).

Generic stage code only. Every firm rule (the reader contract and prompts, the
money and court words, the statute reminders, the pack budgets, the ranking
thresholds, the caseload statuses) is a firm INPUT, staged on the seat and read
by ``firm.py``; nothing firm-specific is in this package (a test refuses known
firm tokens by hash).
"""
