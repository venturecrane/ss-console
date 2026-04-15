# Global Guardrails Upload

Upload the global guardrails doc to the crane context worker.

## When to run

After editing the guardrails source to add new protected action categories, heuristics, or SOD summary lines.

## Invocation

```bash
CRANE_ADMIN_KEY=<key> ./scripts/upload-doc-to-context-worker.sh /path/to/guardrails.md global
```

The script uses `basename` of the path as the doc name, so the file must be named `guardrails.md`.

Get `CRANE_ADMIN_KEY` from Infisical: `infisical secrets get CRANE_ADMIN_KEY --env=dev --plain`.

## Verify

Confirm the upload via crane MCP: `crane_doc('global', 'guardrails.md')` and check the version number incremented.

## Notes

- Script is at `scripts/upload-doc-to-context-worker.sh` (copied from sc-console, #377).
- The context worker endpoint is `https://crane-context.automation-ab6.workers.dev/admin/docs`.
- If the admin key rejects, confirm the correct key with Captain. The key in Infisical `ss` dev env may not match the key registered in the worker.
