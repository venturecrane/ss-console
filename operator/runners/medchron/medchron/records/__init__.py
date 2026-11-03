"""The matter records lane (ADR 0102 amendment 4): a staff member emails the
Operator for named records on a matter; this queued job finds the right files
and pages, cuts them, and writes the reply's facts in code, under a dollar cap
no chat turn could hold. ``run.py`` is the job; ``records_lane.py`` (one level
up) is the daemon's slot, which writes the SMD review row the gateway sends.
"""
