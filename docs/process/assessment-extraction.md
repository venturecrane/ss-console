# Assessment Extraction — Process Guide

**Deliverable #34 | Decision #17 — Assessment Call Capture**

This document describes how to use the Claude extraction prompt to turn a MacWhisper speaker-separated transcript into structured assessment data.

---

## Overview

After every assessment call, we extract structured data from the transcript to feed the quote builder and assessment record. The extraction identifies which of the 6 universal SMB operations problems are present, captures owner quotes for proposals, flags disqualification criteria, estimates complexity, and identifies the internal champion candidate.

## Prerequisites

- MacWhisper Pro running during the assessment call (start before the call begins)
- Speaker-separated transcript exported from MacWhisper after the call

## Phase 1 Workflow (Manual)

1. **End the call.** MacWhisper has the recording with speaker separation.
2. **Export the transcript.** In MacWhisper, export the speaker-separated text transcript.
3. **Open Claude.** Use the standard Claude chat interface.
4. **Paste the full prompt.** Copy the extraction prompt from `src/portal/assessments/extraction-prompt.ts` — use the `buildManualExtractionPrompt()` output, which combines the system context and user prompt with the transcript.
5. **Send and wait.** Claude produces a JSON object matching the `AssessmentExtraction` schema.
6. **Review the output.** Verify that:
   - The identified problems match your assessment from the call
   - Owner quotes are accurate (not fabricated or paraphrased beyond recognition)
   - Disqualification flags are correct
   - Champion candidate identification matches your observation
   - Complexity signals are reasonable
7. **Copy into the assessment record.** Paste the full JSON into the assessment's extraction field in the portal.

### Denormalized Fields

When saving to the portal, also populate these from the extraction:

| Portal Field    | Source in Extraction                              |
| --------------- | ------------------------------------------------- |
| `problems`      | `identified_problems[].problem_id` (array of IDs) |
| `disqualifiers` | `disqualification_flags` (the full object)        |
| `champion_name` | `champion_candidate.name`                         |
| `champion_role` | `champion_candidate.role`                         |

## Phase 5 Workflow (Automated)

When the portal Claude API integration ships:

1. Admin uploads transcript to the portal (stored in R2)
2. Admin clicks "Extract" in the assessment record
3. Portal sends the transcript to Claude API using `EXTRACTION_SYSTEM_PROMPT` as the system message and `buildExtractionUserPrompt(transcript)` as the user message
4. Portal parses the JSON response using `validateExtraction()` to check structure
5. Admin reviews the pre-populated extraction before committing

## Output Schema

The full TypeScript schema is defined in `src/portal/assessments/extraction-schema.ts`. Key sections:

- **Business profile** — name, vertical, employee count, tools in use
- **Identified problems** — 2-3 from the 6 universal problems, with severity, quotes, and root cause
- **Complexity signals** — employee count, locations, tool migrations, data volume, integrations
- **Champion candidate** — name, role, evidence, confidence level
- **Disqualification flags** — hard (automatic no) and soft (yellow flags) from Decision #4
- **Budget signals** — proxies from Decision #4 (payroll employees, years in business, crisis)
- **Quote drivers** — recommended problems, complexity estimate, upward/downward pressures, ROI anchors

## Tips

- **Review owner quotes carefully.** These may end up in proposals and case studies. Make sure they are real quotes from the transcript, not AI-generated paraphrases.
- **Adjust severity if needed.** The extraction may weight severity differently than your in-person read of the conversation. Trust your judgment.
- **Champion confidence is a signal, not a decision.** Even if the extraction says "strong," verify during the Day 1 orientation.
- **ROI anchors are gold.** When the owner computes their own loss numbers during the call (Decision #15), these become the close. Make sure the extraction captured them accurately.

## Files

| File                                          | Purpose                                           |
| --------------------------------------------- | ------------------------------------------------- |
| `src/portal/assessments/extraction-prompt.ts` | Prompt template and validation function           |
| `src/portal/assessments/extraction-schema.ts` | TypeScript types for the extraction JSON          |
| `tests/fixtures/sample-transcript.ts`         | Sample transcript and expected output for testing |
| `tests/extraction-prompt.test.ts`             | Schema and prompt validation tests                |

---

_SMD Services | Deliverable #34_
