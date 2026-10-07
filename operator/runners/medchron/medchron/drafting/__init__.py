"""The drafting job: a firm administrator's request for one document of one
class (a mediation brief, a propounded discovery set, discovery responses, a
memo, a deposition outline) on one matter, drafted from the matter's record in
the firm attorney's house style, rendered as a Word file, and filed into its
own Operator folder on the matter. Run on the seat as a queued job in the root
daemon's third lane (``drafting_lane.py``), beside the chronology and demand.

Nothing in this package names a firm, a client, or a matter. The house style,
the per-class skeletons, prompts and exemplars, the served attachments, the
format rules and the limits are firm inputs (``firm.py``), delivered from the
firm's vault; the request is the envelope (``job.py``).
"""
