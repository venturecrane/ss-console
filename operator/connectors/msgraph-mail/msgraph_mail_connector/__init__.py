"""mcp:msgraph-mail — author-built MCP connector for Microsoft Graph mail.

The provider-neutral operator email adapter #1 (email-channel-seam / ADR 0078):
the operator sends and receives through the client's own Microsoft 365 mailbox,
app-only and mailbox-scoped, behind the same seam AgentMail is being migrated onto.

App-only client-credentials against Graph; every write and send operates ONLY on
the single pinned mailbox (`/users/{MSGRAPH_MAILBOX}/...`) and takes no mailbox
parameter, and there is no delete tool. The two staff READS take a mailbox and
refuse any the firm has not authored (`staff_mailboxes`). Inbound reads normalize into the one
`InboundMessage` DTO the gate/router consumes (spec D2), so nothing downstream of
the seam branches on provider. See operator/connectors/README.md and
docs/specs/operator/email-channel-seam.md.
"""
