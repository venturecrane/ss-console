---
name: workspace
description: Reads and writes Google Workspace via broker tools. Every one of those broker tools is trust-classified.
version: 0.2.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Email, Calendar, Drive, Docs, Sheets, Google, SMD]
---

# Workspace

## When to Use

Use for principal-authorized Gmail, Calendar, Drive, Docs, and Sheets work.

This is the only Google Workspace path. Do not use the Hermes-native
`google-workspace` or `himalaya` skills. Do not use `execute_code`, `terminal`,
or connector CLIs for Workspace access. The gateway has no Google credential.
Every operation must use one of the classified `workspace_*` tools.

## Tools

- Gmail reads: `workspace_gmail_search`, `workspace_gmail_get`.
- Gmail internal writes: `workspace_gmail_create_draft`,
  `workspace_gmail_modify`, `workspace_gmail_archive`.
- Calendar reads: `workspace_calendar_list`, `workspace_calendar_get`.
- Calendar internal writes: `workspace_calendar_create_draft`,
  `workspace_calendar_update_draft`.
- Drive reads: `workspace_drive_list`, `workspace_drive_get`,
  `workspace_drive_export`.
- Docs: `workspace_docs_create`, `workspace_docs_get`,
  `workspace_docs_append`.
- Sheets: `workspace_sheets_create`, `workspace_sheets_get_values`,
  `workspace_sheets_update_values`.

Calendar draft tools always force tentative status, no attendees, and no
notifications.

## Trust Posture

Reads are autonomous. Internal writes pass the authored trust ceiling before a
short-lived broker grant is minted. There is no principal send, share, invite,
delete, or raw credential tool. The broker holds the credential under a
separate OS principal and records paired decision and execution evidence.

Anything that would notify or share with an external party is out of scope.
Report it as a recommended action not taken.

## Verification

- Reads return actual provider data.
- A calendar draft creates a tentative, attendee-free event.
- A Doc or Sheet write completes and returns the provider ID.
- No Workspace action succeeds through `execute_code`, `terminal`, a skill,
  subagent, cron shell, or direct connector CLI.
