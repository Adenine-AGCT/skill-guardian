#!/usr/bin/env python3
"""Sync the packaged runtime into the publishable skill bundle."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Only verify whether the files are already in sync.")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "src" / "skill_guardian" / "runtime.py"
    destination = repo_root / "skills" / "skill-guardian" / "assets" / "python" / "skill_guardian_runtime.py"

    if not source.exists():
        print(f"Missing source runtime: {source}", file=sys.stderr)
        return 1
    if not destination.exists() and args.check:
        print(f"Bundled runtime is missing: {destination}", file=sys.stderr)
        return 1

    source_hash = sha256(source)
    destination_hash = sha256(destination) if destination.exists() else None

    if args.check:
        if source_hash != destination_hash:
            print("Bundled runtime is out of sync.", file=sys.stderr)
            return 1
        print("Bundled runtime is in sync.")
        return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    print(f"Synced runtime to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
