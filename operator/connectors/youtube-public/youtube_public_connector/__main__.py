"""Entrypoint: serve the youtube-public connector over stdio. Installed as the
``youtube-public-mcp`` console-script."""

from __future__ import annotations

from .server import server


def main() -> None:
    server.run_stdio()


if __name__ == "__main__":
    main()
