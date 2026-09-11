"""First-class Google Workspace operations exposed by the broker."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from .google_auth import _customer_google_auth, authored_identities, service

Operation = Callable[[dict[str, Any]], Any]


class WorkspaceOperations:
    """Dispatch the reviewed Workspace capability surface."""

    def __init__(self, credential_path: Path, customer_path: Path) -> None:
        self._credential_path = credential_path
        self._customer_path = customer_path

    def dispatch(self, operation: str, payload: dict[str, Any]) -> Any:
        handler = self._handlers().get(operation)
        if handler is None:
            raise ValueError(f"unsupported Workspace operation: {operation}")
        return handler(payload)

    def supports(self, operation: str) -> bool:
        """Return whether an operation is in the reviewed surface."""
        return operation in self._handlers()

    def supported_operations(self) -> list[str]:
        """Sorted operation names, for the broker capability handshake."""
        return sorted(self._handlers())

    def _validate_from(self, mailbox: str, from_addr: str) -> None:
        """Fail-closed unless `from_addr` is an authored send-as for the mailbox.

        Mirrors the subject check in `google_auth.credentials`: the broker holds
        the credential, so it independently validates the requested `From`
        against its own read of authored config — never trusting the gateway.
        """
        if not from_addr:
            return
        default, _, send_as = authored_identities(_customer_google_auth(self._customer_path))
        effective = (mailbox or "").strip() or default
        if from_addr not in send_as.get(effective, set()):
            raise RuntimeError(f"from {from_addr!r} is not an authored send-as for {effective!r}")

    def _handlers(self) -> dict[str, Operation]:
        return {
            "workspace_gmail_search": self.gmail_search,
            "workspace_gmail_get": self.gmail_get,
            "workspace_gmail_create_draft": self.gmail_create_draft,
            "workspace_gmail_modify": self.gmail_modify,
            "workspace_gmail_archive": self.gmail_archive,
            "workspace_calendar_list": self.calendar_list,
            "workspace_calendar_get": self.calendar_get,
            "workspace_calendar_create_draft": self.calendar_create_draft,
            "workspace_calendar_update_draft": self.calendar_update_draft,
            "workspace_drive_list": self.drive_list,
            "workspace_drive_get": self.drive_get,
            "workspace_drive_export": self.drive_export,
            "workspace_docs_create": self.docs_create,
            "workspace_docs_get": self.docs_get,
            "workspace_docs_append": self.docs_append,
            "workspace_sheets_create": self.sheets_create,
            "workspace_sheets_get_values": self.sheets_get_values,
            "workspace_sheets_update_values": self.sheets_update_values,
        }

    def _service(self, api: str, version: str, mailbox: str = ""):
        return service(api, version, self._credential_path, self._customer_path, mailbox)

    def gmail_search(self, payload: dict[str, Any]) -> Any:
        # `query` is optional: a bare "list/read the mailbox" request carries no
        # search term. Gmail's messages.list accepts an empty `q` (returns the
        # most recent messages across the mailbox), so default to "" rather than
        # KeyError on a missing key — listing is a first-class use of this tool,
        # not only term searches.
        return (
            self._service("gmail", "v1", str(payload.get("mailbox") or ""))
            .users()
            .messages()
            .list(
                userId="me",
                q=str(payload.get("query") or ""),
                maxResults=int(payload.get("max_results", 25)),
            )
            .execute()
            .get("messages", [])
        )

    def gmail_get(self, payload: dict[str, Any]) -> Any:
        return (
            self._service("gmail", "v1", str(payload.get("mailbox") or ""))
            .users()
            .messages()
            .get(userId="me", id=str(payload["message_id"]), format="full")
            .execute()
        )

    def gmail_create_draft(self, payload: dict[str, Any]) -> Any:
        mailbox = str(payload.get("mailbox") or "")
        from_addr = str(payload.get("from") or "")
        self._validate_from(mailbox, from_addr)
        message = EmailMessage()
        message["To"] = str(payload["to"])
        if from_addr:
            message["From"] = from_addr
        message["Subject"] = str(payload["subject"])
        message.set_content(str(payload["body"]))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        body: dict[str, Any] = {"message": {"raw": raw}}
        if payload.get("thread_id"):
            body["message"]["threadId"] = str(payload["thread_id"])
        return self._service("gmail", "v1", mailbox).users().drafts().create(userId="me", body=body).execute()

    def gmail_modify(self, payload: dict[str, Any]) -> Any:
        body = {
            "addLabelIds": payload.get("add_label_ids", []),
            "removeLabelIds": payload.get("remove_label_ids", []),
        }
        return (
            self._service("gmail", "v1", str(payload.get("mailbox") or ""))
            .users()
            .messages()
            .modify(userId="me", id=str(payload["message_id"]), body=body)
            .execute()
        )

    def gmail_archive(self, payload: dict[str, Any]) -> Any:
        return self.gmail_modify(
            {
                "message_id": payload["message_id"],
                "remove_label_ids": ["INBOX"],
                "mailbox": payload.get("mailbox") or "",
            }
        )

    def calendar_list(self, payload: dict[str, Any]) -> Any:
        params: dict[str, Any] = {
            "calendarId": payload.get("calendar_id", "primary"),
            "maxResults": int(payload.get("max_results", 25)),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        for source, target in (
            ("time_min", "timeMin"),
            ("time_max", "timeMax"),
            ("query", "q"),
        ):
            if payload.get(source):
                params[target] = payload[source]
        return self._service("calendar", "v3").events().list(**params).execute().get("items", [])

    def calendar_get(self, payload: dict[str, Any]) -> Any:
        return (
            self._service("calendar", "v3")
            .events()
            .get(
                calendarId=payload.get("calendar_id", "primary"),
                eventId=str(payload["event_id"]),
            )
            .execute()
        )

    def calendar_create_draft(self, payload: dict[str, Any]) -> Any:
        body = {
            "summary": str(payload["title"]),
            "status": "tentative",
            "start": {"dateTime": str(payload["start"])},
            "end": {"dateTime": str(payload["end"])},
        }
        for field in ("description", "location"):
            if payload.get(field):
                body[field] = str(payload[field])
        return (
            self._service("calendar", "v3")
            .events()
            .insert(
                calendarId=payload.get("calendar_id", "primary"),
                body=body,
                sendUpdates="none",
            )
            .execute()
        )

    def calendar_update_draft(self, payload: dict[str, Any]) -> Any:
        patch: dict[str, Any] = {}
        for field, target in (
            ("title", "summary"),
            ("description", "description"),
            ("location", "location"),
        ):
            if field in payload:
                patch[target] = payload[field]
        for field in ("start", "end"):
            if field in payload:
                patch[field] = {"dateTime": payload[field]}
        if not patch:
            raise ValueError("calendar update requires at least one changed field")
        return (
            self._service("calendar", "v3")
            .events()
            .patch(
                calendarId=payload.get("calendar_id", "primary"),
                eventId=str(payload["event_id"]),
                body=patch,
                sendUpdates="none",
            )
            .execute()
        )

    def drive_list(self, payload: dict[str, Any]) -> Any:
        query = []
        if payload.get("folder_id"):
            query.append(f"'{payload['folder_id']}' in parents")
        if payload.get("query"):
            query.append(str(payload["query"]))
        params: dict[str, Any] = {
            "pageSize": int(payload.get("max_results", 25)),
            "fields": "files(id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink)",
            "orderBy": "modifiedTime desc",
        }
        if query:
            params["q"] = " and ".join(query)
        return self._service("drive", "v3").files().list(**params).execute().get("files", [])

    def drive_get(self, payload: dict[str, Any]) -> Any:
        return (
            self._service("drive", "v3")
            .files()
            .get(
                fileId=str(payload["file_id"]),
                fields="id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink",
            )
            .execute()
        )

    def drive_export(self, payload: dict[str, Any]) -> Any:
        data = (
            self._service("drive", "v3")
            .files()
            .export(
                fileId=str(payload["file_id"]),
                mimeType=payload.get("mime_type", "text/plain"),
            )
            .execute()
        )
        return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data

    def docs_create(self, payload: dict[str, Any]) -> Any:
        docs = self._service("docs", "v1")
        doc = docs.documents().create(body={"title": str(payload["title"])}).execute()
        if payload.get("content"):
            self._append_doc_text(docs, doc["documentId"], str(payload["content"]))
        return doc

    def docs_get(self, payload: dict[str, Any]) -> Any:
        return self._service("docs", "v1").documents().get(documentId=str(payload["document_id"])).execute()

    def docs_append(self, payload: dict[str, Any]) -> Any:
        docs = self._service("docs", "v1")
        return self._append_doc_text(docs, str(payload["document_id"]), str(payload["text"]))

    @staticmethod
    def _append_doc_text(docs: Any, document_id: str, text: str) -> Any:
        doc = docs.documents().get(documentId=document_id).execute()
        content = doc.get("body", {}).get("content", [])
        end_index = max((item.get("endIndex", 1) for item in content), default=1)
        request = {
            "requests": [
                {
                    "insertText": {
                        "location": {"index": max(end_index - 1, 1)},
                        "text": text,
                    }
                }
            ]
        }
        return docs.documents().batchUpdate(documentId=document_id, body=request).execute()

    def sheets_create(self, payload: dict[str, Any]) -> Any:
        return (
            self._service("sheets", "v4")
            .spreadsheets()
            .create(
                body={"properties": {"title": str(payload["title"])}},
                fields="spreadsheetId,spreadsheetUrl",
            )
            .execute()
        )

    def sheets_get_values(self, payload: dict[str, Any]) -> Any:
        return (
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .get(
                spreadsheetId=str(payload["spreadsheet_id"]),
                range=str(payload["range"]),
            )
            .execute()
        )

    def sheets_update_values(self, payload: dict[str, Any]) -> Any:
        values = payload.get("values")
        if not isinstance(values, list):
            raise ValueError("values must be an array of rows")
        return (
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=str(payload["spreadsheet_id"]),
                range=str(payload["range"]),
                valueInputOption=payload.get("value_input_option", "USER_ENTERED"),
                body={"values": values},
            )
            .execute()
        )
