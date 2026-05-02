"""Command-line entrypoint for skill-guardian."""

from __future__ import annotations

from .runtime import entrypoint


def main(argv: list[str] | None = None) -> int:
    return entrypoint(argv)
