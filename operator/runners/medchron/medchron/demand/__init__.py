"""The demand job (docs/specs/operator/demand-drafting-routine.md): a firm
administrator's request for a gap audit and a draft time-limited demand on one
matter, run on the seat as a queued job beside the chronology package.

The stages, in order, and where nothing is paid before the estimate holds:

    pull        list the matter, fetch every document (email bodies too), and
                put client and firm-internal correspondence behind the
                privilege wall by sender and recipient            $0
    preflight   extract, route blank/junk pages to transcription, scan names
                and subjects for premise facts, reconcile the Medicals tab
                with the bills, and estimate the cost             $0
    estimate    hold the job, before anything is paid, when the estimate is
                over the firm's per-job cap or the month's budget $0
    premise     the skeleton's Section 0 gate, decided from the record's own
                documents; a failure files a coverage report and ends $0
    transcribe  scanned and junk-layer files, page by page, live, in parallel
    summarize   120K-character chunks, parallel, streamed live, never Batch
    gap_audit   Deliverable 1, against the requester's brief
    compose     Deliverable 2, the requester's brief is the instruction
    audit -> repair -> reaudit, batch ids carrying a content hash
    gate        the drafting gate check, with the held-out names
    render      the firm's own house demand file, format-checked
    file        both files to a dated folder, read back by name and size

Nothing in this package names a firm, a client, or a matter. The firm's voice,
skeleton, prompts, house reference document and limits are firm inputs
(``firm.py``), delivered from the firm's vault; the request is the envelope.
"""
