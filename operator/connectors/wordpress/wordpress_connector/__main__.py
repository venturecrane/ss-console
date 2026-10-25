"""Entrypoint: serve the wordpress connector over stdio. Installed as the
``wordpress-mcp`` console-script; the overlay registry launches it by the
absolute venv path."""

from __future__ import annotations

from .server import server


def main() -> None:
    server.run_stdio()


if __name__ == "__main__":
    main()
