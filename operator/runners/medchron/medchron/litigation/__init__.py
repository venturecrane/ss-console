"""The litigation status job: every open litigation matter's complaint, service,
answer, next court date, case status and discovery, each value cited to the
document it came from, built into one workbook and filed to the firm's Operator
library matter. Product code: nothing here names a firm or a client; every
firm specific (court-paper names, form numbers, process servers, settlement
words, caps, models) is ``litigation-firm.yaml``.

Stages (``data/state.json``, resumable): inventory, diff, fetch, extract,
read1, read2, read3, gates, parity, book, file, report.
"""
