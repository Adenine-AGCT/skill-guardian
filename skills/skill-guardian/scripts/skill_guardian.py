#!/usr/bin/env python3
"""Wrapper entrypoint for the publishable skill-guardian skill."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _run_repo_runtime(skill_root: Path) -> int | None:
    repo_src = skill_root.parents[1] / "src"
    if not repo_src.exists():
        return None
    os.environ.setdefault("SKILL_GUARDIAN_STATE_DIR", str(skill_root.parents[1] / ".skill-guardian-state"))
    sys.path.insert(0, str(repo_src))
    from skill_guardian.cli import main

    return main(
        [
            "audit",
            "--format",
            "markdown",
            "--write-lock",
            "--mode",
            "quick",
            "--max-detailed-skills",
            "5",
            "--max-remote-checks",
            "0",
            "--bundled-upstreams",
            str(skill_root / "references" / "default-upstreams.json"),
            *sys.argv[1:],
        ]
    )


def _run_bundled_runtime(skill_root: Path) -> int:
    vendor_dir = skill_root / "assets" / "python"
    sys.path.insert(0, str(vendor_dir))
    import skill_guardian_runtime

    return skill_guardian_runtime.entrypoint(
        [
            "audit",
            "--format",
            "markdown",
            "--write-lock",
            "--mode",
            "quick",
            "--max-detailed-skills",
            "5",
            "--max-remote-checks",
            "0",
            "--bundled-upstreams",
            str(skill_root / "references" / "default-upstreams.json"),
            *sys.argv[1:],
        ]
    )


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent
    repo_result = _run_repo_runtime(skill_root)
    if repo_result is not None:
        return repo_result
    return _run_bundled_runtime(skill_root)


if __name__ == "__main__":
    raise SystemExit(main())
