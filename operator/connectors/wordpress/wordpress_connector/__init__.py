"""mcp:wordpress - author-built MCP connector for a firm's WordPress site.

Reads (posts, categories, tags, media), a media upload, and posts created or
updated ONLY in ``pending`` status for a person at the firm to review and
publish. There is no publish tool, the status is never caller-controlled, and
every post write passes the advertising gate first. See manifest.toml.
"""
