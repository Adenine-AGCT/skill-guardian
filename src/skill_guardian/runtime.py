#!/usr/bin/env python3
"""Shared runtime for the public skill-guardian CLI and bundled skill wrapper."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from importlib import resources
from pathlib import Path
from typing import Any, TextIO

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency at runtime
    yaml = None


TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".ps1",
    ".psm1",
    ".psd1",
    ".sh",
    ".bash",
    ".bat",
    ".cmd",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
}

SCRIPT_EXTENSIONS = {
    ".py",
    ".ps1",
    ".psm1",
    ".sh",
    ".bash",
    ".bat",
    ".cmd",
    ".js",
    ".ts",
}

EXECUTABLE_EXTENSIONS = {
    ".ps1",
    ".psm1",
    ".sh",
    ".bash",
    ".bat",
    ".cmd",
    ".exe",
    ".dll",
    ".msi",
    ".jar",
    ".com",
    ".scr",
}

DOC_EXTENSIONS = {".md", ".txt"}
MAX_TEXT_SCAN_BYTES = 256_000
LOCK_VERSION = 3
STATE_ENV_VAR = "SKILL_GUARDIAN_STATE_DIR"
CONFIG_ENV_VAR = "SKILL_GUARDIAN_CONFIG_FILE"
SKILL_METADATA_CANDIDATES = (
    ".github-skill.json",
    ".skill-metadata.json",
    "skill-metadata.json",
    ".openai/skill.json",
    ".codex/skill.json",
)

PATTERN_RULES = [
    (
        "network",
        "medium",
        12,
        re.compile(
            r"urllib|requests\.|httpx\.|Invoke-WebRequest|curl\s|wget\s|git clone",
            re.IGNORECASE,
        ),
        "Contains network or download behavior",
    ),
    (
        "subprocess",
        "high",
        14,
        re.compile(
            r"subprocess|os\.system|Popen\(|Start-Process|powershell|cmd /c|bash\s",
            re.IGNORECASE,
        ),
        "Contains subprocess or shell execution behavior",
    ),
    (
        "destructive",
        "critical",
        22,
        re.compile(
            r"Remove-Item|shutil\.rmtree|rm -rf|git reset --hard|del /f|rmdir /s",
            re.IGNORECASE,
        ),
        "Contains destructive filesystem or reset behavior",
    ),
    (
        "prompt_override",
        "medium",
        8,
        re.compile(
            r"ignore (all )?(previous|prior) instructions|override (the )?system",
            re.IGNORECASE,
        ),
        "Contains instruction-override language",
    ),
]

POLICY_PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {
        "weights": {
            "source_confidence": 0.25,
            "integrity_confidence": 0.25,
            "behavior_confidence": 0.30,
            "update_confidence": 0.20,
        },
        "min_update_confidence_for_update": 88,
        "max_script_change_action": "block",
        "metadata_change_action": "review",
        "block_on_unknown_upstream_similarity": True,
    },
    "balanced": {
        "weights": {
            "source_confidence": 0.25,
            "integrity_confidence": 0.25,
            "behavior_confidence": 0.30,
            "update_confidence": 0.20,
        },
        "min_update_confidence_for_update": 80,
        "max_script_change_action": "review",
        "metadata_change_action": "review",
        "block_on_unknown_upstream_similarity": False,
    },
    "research": {
        "weights": {
            "source_confidence": 0.20,
            "integrity_confidence": 0.20,
            "behavior_confidence": 0.35,
            "update_confidence": 0.25,
        },
        "min_update_confidence_for_update": 72,
        "max_script_change_action": "review",
        "metadata_change_action": "review",
        "block_on_unknown_upstream_similarity": False,
    },
}


@dataclass
class Issue:
    category: str
    severity: str
    points: int
    file: str
    detail: str


@dataclass
class PathAudit:
    fingerprint: str
    frontmatter_name: str | None
    frontmatter_description: str | None
    frontmatter_valid: bool
    has_openai_yaml: bool
    file_hashes: dict[str, str]
    total_files: int
    script_files: int
    executable_files: int
    binary_files: int
    issues: list[Issue]


@dataclass
class RootSpec:
    path: Path
    host_adapter: str
    discovery_source: str


@dataclass
class UpstreamSpec:
    repo: str
    path: str
    ref: str = "main"
    commit: str | None = None
    tag: str | None = None


@dataclass
class RemoteState:
    latest_commit: str | None = None
    latest_commit_short: str | None = None
    latest_commit_date: str | None = None
    latest_tag: str | None = None
    path_audit: PathAudit | None = None
    error: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clamp(value: int) -> int:
    return max(0, min(100, value))


def short_sha(value: str | None) -> str | None:
    if not value:
        return None
    return value[:8]


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def parse_semver_tag(value: str | None) -> tuple[int, int, int] | None:
    if not value or not isinstance(value, str):
        return None
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def severity_rank(severity: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(severity, 0)


def risk_from_score(score: int) -> str:
    if score >= 85:
        return "low"
    if score >= 65:
        return "medium"
    if score >= 40:
        return "high"
    return "critical"


def build_baseline_snapshot(local_audit: PathAudit, upstream: UpstreamSpec | None) -> dict[str, Any]:
    tracked_hashes = {
        relative: digest
        for relative, digest in local_audit.file_hashes.items()
        if relative == "SKILL.md"
        or relative == "agents/openai.yaml"
        or relative.startswith("scripts/")
    }
    return {
        "fingerprint": local_audit.fingerprint,
        "frontmatter_name": local_audit.frontmatter_name,
        "frontmatter_description": local_audit.frontmatter_description,
        "tracked_hashes": tracked_hashes,
        "script_files": local_audit.script_files,
        "executable_files": local_audit.executable_files,
        "generated_at": now_iso(),
        "upstream": asdict(upstream) if upstream else None,
    }


def evaluate_baseline(local_audit: PathAudit, lock_entry: dict[str, Any]) -> tuple[str, str | None, str]:
    baseline = lock_entry.get("baseline")
    if not isinstance(baseline, dict):
        return "new", None, "low"

    tracked_hashes = baseline.get("tracked_hashes", {})
    if not isinstance(tracked_hashes, dict):
        tracked_hashes = {}

    if baseline.get("fingerprint") == local_audit.fingerprint:
        return "unchanged", None, "low"

    changed_tracked = []
    for relative, old_digest in tracked_hashes.items():
        if local_audit.file_hashes.get(relative) != old_digest:
            changed_tracked.append(relative)

    script_drift = any(relative.startswith("scripts/") for relative in changed_tracked)
    metadata_drift = any(relative in {"SKILL.md", "agents/openai.yaml"} for relative in changed_tracked)
    executable_delta = int(baseline.get("executable_files", 0) or 0) != local_audit.executable_files

    if script_drift or executable_delta:
        risk = "high"
    elif metadata_drift:
        risk = "medium"
    else:
        risk = "low"

    if changed_tracked:
        summary = ", ".join(changed_tracked[:3])
        if len(changed_tracked) > 3:
            summary += ", ..."
        return "drifted", f"Tracked local files changed: {summary}", risk
    return "drifted", "Local fingerprint changed since the last trusted baseline.", risk


def compute_reputation(lock_entry: dict[str, Any], source_type: str, baseline_status: str) -> tuple[int, str, str]:
    review_count = int(lock_entry.get("review_count", 0) or 0)
    block_count = int(lock_entry.get("block_count", 0) or 0)
    unknown_count = int(lock_entry.get("unknown_source_count", 0) or 0)
    drift_count = int(lock_entry.get("drift_count", 0) or 0)

    score = 100 - min(18, review_count * 3) - min(28, block_count * 7) - min(18, unknown_count * 3) - min(20, drift_count * 5)
    if source_type == "unknown":
        score -= 8
    if baseline_status == "drifted":
        score -= 12
    score = clamp(score)

    negative_signals = review_count + block_count + drift_count
    if block_count >= 2 or drift_count >= 2:
        trend = "rising"
    elif negative_signals == 0:
        trend = "stable"
    else:
        trend = "mixed"

    if block_count >= 2 or review_count >= 3:
        stability = "unstable"
    elif source_type == "unknown":
        stability = "unproven"
    else:
        stability = "stable"

    return score, trend, stability


def compute_version_gap(
    *,
    current_tag: str | None,
    current_commit: str | None,
    current_commit_date: str | None,
    latest_tag: str | None,
    latest_commit: str | None,
    latest_commit_date: str | None,
) -> tuple[str, str | None]:
    local_semver = parse_semver_tag(current_tag)
    remote_semver = parse_semver_tag(latest_tag)
    if local_semver and remote_semver:
        local_major, local_minor, local_patch = local_semver
        remote_major, remote_minor, remote_patch = remote_semver
        if local_semver == remote_semver:
            return "none", "Local and remote semantic versions match."
        if remote_major > local_major or remote_minor > local_minor:
            return "large", f"Remote semantic version {latest_tag} is at least one major/minor line ahead of local {current_tag}."
        patch_gap = remote_patch - local_patch
        if patch_gap >= 3:
            return "medium", f"Remote semantic version {latest_tag} is several patch releases ahead of local {current_tag}."
        if patch_gap >= 1:
            return "small", f"Remote semantic version {latest_tag} is slightly ahead of local {current_tag}."
        return "unknown", "Semantic versions do not progress in an obvious forward order."

    if current_commit and latest_commit and current_commit == latest_commit:
        return "none", "Local and remote commits match."

    local_date = parse_iso_datetime(current_commit_date)
    remote_date = parse_iso_datetime(latest_commit_date)
    if local_date and remote_date:
        delta_days = max(0, (remote_date - local_date).days)
        if delta_days < 30:
            return "small", f"Remote commit is {delta_days} day(s) newer than the last known local version."
        if delta_days <= 90:
            return "medium", f"Remote commit is {delta_days} day(s) newer than the last known local version."
        return "large", f"Remote commit is {delta_days} day(s) newer than the last known local version."

    return "unknown", "Version gap could not be estimated from tags or commit timestamps."


def classify_change_lane(
    diff_summary: dict[str, Any],
    baseline_status: str,
) -> tuple[str, str]:
    flags = [
        diff_summary.get("doc_only"),
        diff_summary.get("skill_md_changed"),
        diff_summary.get("metadata_changed"),
        diff_summary.get("scripts_changed"),
        diff_summary.get("executable_added"),
    ]
    active_count = sum(1 for flag in flags if flag)

    if baseline_status == "drifted" and diff_summary.get("changed_files", 0) == 0:
        return "local_drift_only", "medium"
    if diff_summary.get("executable_added"):
        return ("mixed_change" if active_count > 1 else "binary_or_executable_change"), "high"
    if diff_summary.get("scripts_changed"):
        return ("mixed_change" if active_count > 1 else "script_change"), "high"
    if diff_summary.get("skill_md_changed"):
        return ("mixed_change" if active_count > 1 else "prompt_change"), "medium"
    if diff_summary.get("metadata_changed"):
        return ("mixed_change" if active_count > 1 else "metadata_change"), "medium"
    if diff_summary.get("doc_only"):
        return "docs_change", "low"
    return "none", "low"


def default_max_remote_checks(mode: str) -> int:
    return {"quick": 0, "standard": 3, "deep": 999}[mode]


def is_truthy_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    return Path(path_text).expanduser()


def state_dir_candidates() -> list[Path]:
    env_override = os.environ.get(STATE_ENV_VAR)
    if env_override:
        return [Path(env_override).expanduser()]
    candidates: list[Path] = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "skill-guardian")
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "skill-guardian")
        candidates.append(Path.home() / ".skill-guardian")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "skill-guardian")
        candidates.append(Path.home() / ".skill-guardian")
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            candidates.append(Path(xdg_config).expanduser() / "skill-guardian")
        candidates.append(Path.home() / ".config" / "skill-guardian")
        candidates.append(Path.home() / ".skill-guardian")
    return candidates


def app_state_dir(*, for_write: bool) -> Path:
    candidates = state_dir_candidates()
    if not candidates:
        raise RuntimeError("No state directory candidates are available.")

    if not for_write:
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate
        return candidates[0]

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if candidate.is_dir() and os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    candidate_list = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        "No writable state directory is available. "
        f"Tried: {candidate_list}"
    )


def default_config_path(*, for_write: bool) -> Path:
    env_override = os.environ.get(CONFIG_ENV_VAR)
    if env_override:
        return Path(env_override).expanduser()
    return app_state_dir(for_write=for_write) / "config.json"


def default_lock_path(*, for_write: bool) -> Path:
    return app_state_dir(for_write=for_write) / "skills.lock.json"


def default_history_path(*, for_write: bool) -> Path:
    return app_state_dir(for_write=for_write) / "history.jsonl"


def default_config() -> dict[str, Any]:
    return {
        "roots": [],
        "upstreams": {},
        "policy": "balanced",
        "source_allowlist": [],
        "source_blocklist": [],
    }


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_history(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_config(path: Path | None) -> dict[str, Any]:
    config = default_config()
    if path is None:
        path = default_config_path(for_write=False)
    loaded = read_json(path, {})
    if not isinstance(loaded, dict):
        loaded = {}
    for key, value in loaded.items():
        config[key] = value
    if config.get("policy") not in POLICY_PRESETS:
        config["policy"] = "balanced"
    if not isinstance(config.get("roots"), list):
        config["roots"] = []
    if not isinstance(config.get("upstreams"), dict):
        config["upstreams"] = {}
    for key in ("source_allowlist", "source_blocklist"):
        if not isinstance(config.get(key), list):
            config[key] = []
    return config


def load_lock(path: Path) -> dict[str, Any]:
    data = read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", LOCK_VERSION)
    data.setdefault("skills", {})
    return data


def host_adapter_for_path(path: Path, discovery_source: str) -> str:
    normalized = path.as_posix().lower()
    if normalized.endswith("/.codex/skills") or "/.codex/skills" in normalized:
        return "codex"
    if normalized.endswith("/.agents/skills") or "/.agents/skills" in normalized:
        return "generic-agent"
    return "custom" if discovery_source == "config" else "generic-agent"


def discover_roots(config: dict[str, Any], explicit_roots: list[str] | None = None) -> list[RootSpec]:
    candidates: list[tuple[Path, str]] = []
    if explicit_roots:
        for raw in explicit_roots:
            candidates.append((Path(raw).expanduser(), "explicit"))
    else:
        candidates.extend(
            [
                (Path.home() / ".codex" / "skills", "default"),
                (Path.home() / ".agents" / "skills", "default"),
            ]
        )
        for raw in config.get("roots", []):
            if isinstance(raw, str) and raw.strip():
                candidates.append((Path(raw).expanduser(), "config"))

    seen: set[str] = set()
    roots: list[RootSpec] = []
    for path, source in candidates:
        resolved_key = str(path.resolve()) if path.exists() else str(path.expanduser())
        if resolved_key in seen:
            continue
        seen.add(resolved_key)
        if not path.exists():
            continue
        if not path.is_dir():
            continue
        roots.append(
            RootSpec(
                path=path,
                host_adapter=host_adapter_for_path(path, source),
                discovery_source=source,
            )
        )
    return roots


def load_default_upstreams(path: Path | None) -> dict[str, Any]:
    if path is None:
        data = json.loads(
            resources.files("skill_guardian")
            .joinpath("resources/default-upstreams.json")
            .read_text(encoding="utf-8")
        )
    else:
        data = read_json(path, {})
    if not isinstance(data, dict):
        return {}
    skills = data.get("skills", {})
    return skills if isinstance(skills, dict) else {}


def github_request(url: str) -> bytes:
    headers = {"User-Agent": "skill-guardian"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def github_api_url(repo: str, suffix: str) -> str:
    return f"https://api.github.com/repos/{repo}/{suffix}"


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:2048]
    except OSError:
        return False
    return b"\x00" not in sample


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dir_fingerprint(file_hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(file_hashes):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hashes[relative_path].encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def should_scan_patterns(relative: str) -> bool:
    if relative in {"LICENSE", "LICENSE.txt", "NOTICE", "NOTICE.txt"}:
        return False
    if relative.startswith("assets/"):
        return False
    return (
        relative == "SKILL.md"
        or relative == "agents/openai.yaml"
        or relative.startswith("scripts/")
    )


def parse_frontmatter(text: str) -> dict[str, str] | None:
    text = text.lstrip("\ufeff")
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        return None
    payload = match.group(1)
    if yaml is not None:
        try:
            parsed_yaml = yaml.safe_load(payload)
        except Exception:
            parsed_yaml = None
        if isinstance(parsed_yaml, dict):
            normalized: dict[str, str] = {}
            for key, value in parsed_yaml.items():
                if isinstance(key, str) and value is not None:
                    normalized[key] = value if isinstance(value, str) else str(value)
            return normalized or None

    parsed: dict[str, str] = {}
    lines = payload.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            index += 1
            continue
        match_key = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", raw_line)
        if not match_key:
            return None
        key = match_key.group(1)
        value = match_key.group(2).strip()
        index += 1

        if value in {"|", ">"}:
            block_lines: list[str] = []
            while index < len(lines):
                continuation = lines[index]
                if continuation.startswith(" ") or continuation.startswith("\t"):
                    block_lines.append(continuation.lstrip())
                    index += 1
                    continue
                if not continuation.strip():
                    block_lines.append("")
                    index += 1
                    continue
                break
            parsed[key] = "\n".join(block_lines).strip()
            continue

        continuation_lines: list[str] = []
        while index < len(lines):
            continuation = lines[index]
            if continuation.startswith(" ") or continuation.startswith("\t"):
                continuation_lines.append(continuation.strip())
                index += 1
                continue
            break
        if continuation_lines:
            value = " ".join([value, *continuation_lines]).strip()
        parsed[key] = value.strip('"').strip("'")

    return parsed or None


def audit_skill_path(path: Path) -> PathAudit:
    file_hashes: dict[str, str] = {}
    issues: list[Issue] = []
    frontmatter_name = None
    frontmatter_description = None
    frontmatter_valid = False
    has_openai_yaml = (path / "agents" / "openai.yaml").exists()
    total_files = 0
    script_files = 0
    executable_files = 0
    binary_files = 0

    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = file_path.relative_to(path).as_posix()
        if any(part in {"__pycache__", ".git"} for part in file_path.parts):
            continue
        total_files += 1
        file_hashes[relative] = file_sha256(file_path)
        suffix = file_path.suffix.lower()
        if suffix in SCRIPT_EXTENSIONS:
            script_files += 1
        if suffix in EXECUTABLE_EXTENSIONS:
            executable_files += 1
            issues.append(
                Issue(
                    category="executable_file",
                    severity="medium",
                    points=8,
                    file=relative,
                    detail="Contains an executable or shell-oriented file extension",
                )
            )
        text_like = is_probably_text(file_path)
        if not text_like:
            binary_files += 1
            if suffix in EXECUTABLE_EXTENSIONS:
                issues.append(
                    Issue(
                        category="binary_executable",
                        severity="high",
                        points=16,
                        file=relative,
                        detail="Contains a binary executable asset",
                    )
                )
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="utf-8", errors="ignore")

        if relative == "SKILL.md":
            frontmatter = parse_frontmatter(text)
            if frontmatter and frontmatter.get("name") and frontmatter.get("description"):
                frontmatter_valid = True
                frontmatter_name = frontmatter.get("name")
                frontmatter_description = frontmatter.get("description")
            else:
                issues.append(
                    Issue(
                        category="invalid_frontmatter",
                        severity="high",
                        points=20,
                        file=relative,
                        detail="Missing or invalid YAML frontmatter",
                    )
                )

        if len(text.encode("utf-8")) > MAX_TEXT_SCAN_BYTES or not should_scan_patterns(relative):
            continue
        for category, severity, points, pattern, detail in PATTERN_RULES:
            if pattern.search(text):
                issues.append(
                    Issue(
                        category=category,
                        severity=severity,
                        points=points,
                        file=relative,
                        detail=detail,
                    )
                )

    if not (path / "SKILL.md").exists():
        issues.append(
            Issue(
                category="missing_skill_md",
                severity="critical",
                points=30,
                file="SKILL.md",
                detail="SKILL.md is missing",
            )
        )
    if not has_openai_yaml:
        issues.append(
            Issue(
                category="missing_openai_yaml",
                severity="low",
                points=4,
                file="agents/openai.yaml",
                detail="agents/openai.yaml is missing",
            )
        )

    return PathAudit(
        fingerprint=dir_fingerprint(file_hashes),
        frontmatter_name=frontmatter_name,
        frontmatter_description=frontmatter_description,
        frontmatter_valid=frontmatter_valid,
        has_openai_yaml=has_openai_yaml,
        file_hashes=file_hashes,
        total_files=total_files,
        script_files=script_files,
        executable_files=executable_files,
        binary_files=binary_files,
        issues=issues,
    )


def enumerate_local_skills(root: RootSpec) -> tuple[list[dict[str, Any]], list[str]]:
    reports: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entry in sorted(p for p in root.path.iterdir() if p.is_dir()):
        if entry.name.startswith("."):
            skipped.append(entry.name)
            continue
        if not (entry / "SKILL.md").exists():
            skipped.append(entry.name)
            continue
        reports.append(
            {
                "skill_id": make_skill_id(root.path, entry.name),
                "name": entry.name,
                "path": entry,
                "root": str(root.path),
                "host_adapter": root.host_adapter,
                "local_audit": audit_skill_path(entry),
            }
        )
    return reports, skipped


def make_skill_id(root_path: Path, skill_name: str) -> str:
    return f"{str(root_path.resolve()).lower()}::{skill_name.lower()}"


def load_skill_install_metadata(skill_path: Path) -> dict[str, Any] | None:
    for relative in SKILL_METADATA_CANDIDATES:
        candidate = skill_path / relative
        if not candidate.exists() or not candidate.is_file():
            continue
        data = read_json(candidate, {})
        if isinstance(data, dict):
            return data
    return None


def normalize_upstream_mapping(raw: dict[str, Any] | None) -> UpstreamSpec | None:
    if not isinstance(raw, dict):
        return None
    repo = raw.get("repo")
    path = raw.get("path")
    if not isinstance(repo, str) or not isinstance(path, str):
        return None
    return UpstreamSpec(
        repo=repo,
        path=path,
        ref=str(raw.get("ref", "main")),
        commit=str(raw["commit"]) if raw.get("commit") else None,
        tag=str(raw["tag"]) if raw.get("tag") else None,
    )


def resolve_upstream(
    skill_name: str,
    skill_path: Path,
    config: dict[str, Any],
    lock_data: dict[str, Any],
    default_upstreams: dict[str, Any],
) -> tuple[str, UpstreamSpec | None, dict[str, Any] | None]:
    metadata = load_skill_install_metadata(skill_path)
    if metadata:
        upstream = normalize_upstream_mapping(metadata.get("upstream") if isinstance(metadata.get("upstream"), dict) else metadata)
        if upstream:
            return "installed-metadata", upstream, metadata

    configured = config.get("upstreams", {}).get(skill_name)
    upstream = normalize_upstream_mapping(configured if isinstance(configured, dict) else None)
    if upstream:
        return "configured-upstream", upstream, None

    default_mapping = default_upstreams.get(skill_name)
    upstream = normalize_upstream_mapping(default_mapping if isinstance(default_mapping, dict) else None)
    if upstream:
        return "bundled-default", upstream, None

    lock_entry = lock_data.get("skills", {}).get(make_skill_id(skill_path.parent, skill_name), {})
    upstream = normalize_upstream_mapping(lock_entry.get("upstream") if isinstance(lock_entry, dict) else None)
    if upstream:
        return "configured-upstream", upstream, None
    return "unknown", None, None


def remote_latest_commit(upstream: UpstreamSpec) -> tuple[str | None, str | None, str | None]:
    suffix = "commits?" + urllib.parse.urlencode(
        {"sha": upstream.ref, "path": upstream.path, "per_page": 1}
    )
    payload = json.loads(github_request(github_api_url(upstream.repo, suffix)).decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        return None, None, None
    item = payload[0]
    sha = item.get("sha")
    date = item.get("commit", {}).get("author", {}).get("date")
    return sha, short_sha(sha), date


def remote_latest_tag(upstream: UpstreamSpec) -> str | None:
    try:
        payload = json.loads(
            github_request(github_api_url(upstream.repo, "tags?per_page=100")).decode("utf-8")
        )
    except urllib.error.HTTPError:
        return None
    if not isinstance(payload, list):
        return None
    for item in payload:
        commit = item.get("commit", {})
        if commit.get("sha"):
            return item.get("name")
    return None


def inspect_remote_metadata(upstream: UpstreamSpec) -> RemoteState:
    try:
        latest_commit, latest_commit_short, latest_commit_date = remote_latest_commit(upstream)
        latest_tag = remote_latest_tag(upstream)
        if not latest_commit:
            return RemoteState(error="No upstream commit found for mapped skill path")
        return RemoteState(
            latest_commit=latest_commit,
            latest_commit_short=latest_commit_short,
            latest_commit_date=latest_commit_date,
            latest_tag=latest_tag,
        )
    except Exception as exc:  # pragma: no cover - network failures vary
        return RemoteState(error=str(exc))


def safe_extract_zip(zip_file: zipfile.ZipFile, dest_dir: Path) -> None:
    dest_root = dest_dir.resolve()
    for info in zip_file.infolist():
        extracted_path = (dest_dir / info.filename).resolve()
        if extracted_path == dest_root or str(extracted_path).startswith(str(dest_root) + os.sep):
            continue
        raise ValueError("Archive contains a path outside the extraction directory")
    zip_file.extractall(dest_dir)


def fetch_remote_skill_snapshot(upstream: UpstreamSpec, commit: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="skill-guardian-") as tmp_dir:
        temp_root = Path(tmp_dir)
        zip_url = f"https://codeload.github.com/{upstream.repo}/zip/{commit}"
        archive = temp_root / "repo.zip"
        archive.write_bytes(github_request(zip_url))
        with zipfile.ZipFile(archive, "r") as zip_file:
            safe_extract_zip(zip_file, temp_root)
        extracted_roots = [p for p in temp_root.iterdir() if p.is_dir()]
        if len(extracted_roots) != 1:
            raise ValueError("Unexpected archive layout")
        remote_skill_path = extracted_roots[0] / Path(upstream.path)
        if not remote_skill_path.exists():
            raise ValueError(f"Remote skill path not found in archive: {upstream.path}")
        durable_root = Path(tempfile.mkdtemp(prefix="skill-guardian-snapshot-"))
        destination = durable_root / remote_skill_path.name
        for source in remote_skill_path.rglob("*"):
            target = destination / source.relative_to(remote_skill_path)
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        return destination


def inspect_remote(upstream: UpstreamSpec) -> RemoteState:
    try:
        latest_commit, latest_commit_short, latest_commit_date = remote_latest_commit(upstream)
        if not latest_commit:
            return RemoteState(error="No upstream commit found for mapped skill path")
        snapshot_path = fetch_remote_skill_snapshot(upstream, latest_commit)
        audit = audit_skill_path(snapshot_path)
        latest_tag = remote_latest_tag(upstream)
        return RemoteState(
            latest_commit=latest_commit,
            latest_commit_short=latest_commit_short,
            latest_commit_date=latest_commit_date,
            latest_tag=latest_tag,
            path_audit=audit,
        )
    except Exception as exc:  # pragma: no cover - network failures vary
        return RemoteState(error=str(exc))


def compare_hashes(local_hashes: dict[str, str], remote_hashes: dict[str, str]) -> dict[str, list[str]]:
    local_keys = set(local_hashes)
    remote_keys = set(remote_hashes)
    changed = sorted(path for path in local_keys & remote_keys if local_hashes[path] != remote_hashes[path])
    added = sorted(remote_keys - local_keys)
    removed = sorted(local_keys - remote_keys)
    return {"changed": changed, "added": added, "removed": removed}


def classify_diff(diff: dict[str, list[str]]) -> dict[str, Any]:
    files = diff["changed"] + diff["added"] + diff["removed"]
    doc_only = bool(files)
    skill_md_changed = False
    metadata_changed = False
    scripts_changed = False
    executable_added = False

    for relative in files:
        suffix = Path(relative).suffix.lower()
        if relative == "SKILL.md":
            skill_md_changed = True
            doc_only = False
        elif relative == "agents/openai.yaml":
            metadata_changed = True
            doc_only = False
        elif relative.startswith("scripts/") or suffix in SCRIPT_EXTENSIONS:
            scripts_changed = True
            doc_only = False
        elif suffix in EXECUTABLE_EXTENSIONS:
            executable_added = True
            doc_only = False
        elif suffix not in DOC_EXTENSIONS and not relative.startswith("references/"):
            doc_only = False

    return {
        "changed_files": len(files),
        "doc_only": doc_only,
        "skill_md_changed": skill_md_changed,
        "metadata_changed": metadata_changed,
        "scripts_changed": scripts_changed,
        "executable_added": executable_added,
        "changed": diff["changed"],
        "added": diff["added"],
        "removed": diff["removed"],
    }


def explain_issues(issues: list[Issue], prefix: str) -> list[str]:
    lines: list[str] = []
    for issue in sorted(issues, key=lambda item: (-severity_rank(item.severity), -item.points, item.file))[:5]:
        lines.append(f"{prefix}: {issue.detail} in {issue.file} ({issue.severity})")
    return lines


def add_similarity_warning(
    report_issues: list[Issue],
    skill_name: str,
    source_type: str,
    known_names: set[str],
    policy: dict[str, Any],
) -> None:
    if source_type != "unknown":
        return
    best_name = None
    best_ratio = 0.0
    for candidate in known_names:
        ratio = SequenceMatcher(None, skill_name.lower(), candidate.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = candidate
    if best_name and best_ratio >= 0.84:
        severity = "high" if policy["block_on_unknown_upstream_similarity"] else "medium"
        points = 18 if severity == "high" else 10
        report_issues.append(
            Issue(
                category="similarity_warning",
                severity=severity,
                points=points,
                file="SKILL.md",
                detail=f"Unknown-source skill name is very similar to known public skill '{best_name}'",
            )
        )


def repo_allowed(upstream: UpstreamSpec | None, config: dict[str, Any]) -> tuple[bool, str | None]:
    if upstream is None:
        return True, None
    repo = upstream.repo
    blocklist = {item for item in config.get("source_blocklist", []) if isinstance(item, str)}
    if repo in blocklist:
        return False, f"Repo {repo} is explicitly blocklisted."
    allowlist = {item for item in config.get("source_allowlist", []) if isinstance(item, str)}
    if allowlist and repo not in allowlist:
        return False, f"Repo {repo} is not present in the allowlist."
    return True, None


def compute_report(
    entry: dict[str, Any],
    config: dict[str, Any],
    lock_data: dict[str, Any],
    default_upstreams: dict[str, Any],
    policy_name: str,
    offline: bool,
    remote_state: RemoteState | None = None,
    allow_remote_check: bool = True,
    requested_mode: str = "standard",
    remote_check_reason: str | None = None,
) -> dict[str, Any]:
    policy = POLICY_PRESETS[policy_name]
    skill_name = entry["name"]
    skill_path: Path = entry["path"]
    local_audit: PathAudit = entry["local_audit"]
    root = entry["root"]
    host_adapter = entry["host_adapter"]
    skill_id = entry["skill_id"]
    lock_entry = lock_data.get("skills", {}).get(skill_id, {})
    if not isinstance(lock_entry, dict):
        lock_entry = {}

    source_type, upstream, metadata = resolve_upstream(
        skill_name=skill_name,
        skill_path=skill_path,
        config=config,
        lock_data=lock_data,
        default_upstreams=default_upstreams,
    )
    source_allowed, source_message = repo_allowed(upstream, config)

    issues = list(local_audit.issues)
    add_similarity_warning(issues, skill_name, source_type, set(default_upstreams), policy)
    if source_message:
        issues.append(
            Issue(
                category="source_policy",
                severity="high",
                points=16,
                file="SKILL.md",
                detail=source_message,
            )
        )

    baseline_status, drift_summary, local_modification_risk = evaluate_baseline(local_audit, lock_entry)
    reputation_score, historical_risk_trend, upstream_stability = compute_reputation(
        lock_entry,
        source_type,
        baseline_status,
    )

    source_confidence = 55
    integrity_confidence = 88
    behavior_confidence = 92
    update_confidence: int | None = None
    explanation: list[str] = []

    if source_type == "installed-metadata":
        source_confidence += 25
        explanation.append("Version provenance comes from installed skill metadata.")
    elif source_type == "configured-upstream":
        source_confidence += 18
        explanation.append("Version provenance comes from user-configured upstream mapping.")
    elif source_type == "bundled-default":
        source_confidence += 12
        explanation.append("Version provenance comes from bundled default upstream mapping.")
    else:
        source_confidence -= 30
        explanation.append("No upstream mapping was found, so version provenance is unknown.")

    current_commit = None
    current_tag = None
    if isinstance(metadata, dict):
        current_commit = metadata.get("commit")
        current_tag = metadata.get("tag")
    if not current_commit:
        current_commit = lock_entry.get("current_commit")
    if not current_tag:
        current_tag = lock_entry.get("current_tag")
    current_commit_date = None
    if isinstance(metadata, dict):
        current_commit_date = metadata.get("commit_date")
    if not current_commit_date:
        current_commit_date = lock_entry.get("current_commit_date") or lock_entry.get("last_known_local_version_date")

    if current_commit or current_tag:
        source_confidence += 10
        explanation.append(
            f"Local version metadata is recorded as {short_sha(str(current_commit)) or current_tag}."
        )
    else:
        source_confidence -= 8
        explanation.append("No recorded local upstream commit or tag is stored yet.")

    if not local_audit.frontmatter_valid:
        integrity_confidence -= 25
        explanation.append("Local SKILL.md frontmatter is missing or invalid.")
    if not local_audit.has_openai_yaml:
        integrity_confidence -= 8
        explanation.append("Local agents/openai.yaml is missing.")
    if baseline_status == "drifted":
        integrity_confidence -= 12
        explanation.append(drift_summary or "Local files drifted from the last stored baseline.")
        if local_modification_risk == "high":
            behavior_confidence -= 10
        elif local_modification_risk == "medium":
            behavior_confidence -= 5
    elif baseline_status == "new":
        explanation.append("No prior local baseline exists yet for this skill.")

    for issue in issues:
        behavior_confidence -= issue.points
    if local_audit.script_files == 0:
        behavior_confidence = min(100, behavior_confidence + 4)
        explanation.append("No local scripts were found, which lowers execution risk.")

    history_review_count = int(lock_entry.get("review_count", 0) or 0)
    history_block_count = int(lock_entry.get("block_count", 0) or 0)
    history_unknown_count = int(lock_entry.get("unknown_source_count", 0) or 0)
    history_drift_count = int(lock_entry.get("drift_count", 0) or 0)
    if history_review_count:
        explanation.append(f"This skill has triggered review {history_review_count} time(s) before.")
    if history_block_count:
        explanation.append(f"This skill has triggered block {history_block_count} time(s) before.")
        behavior_confidence -= min(10, history_block_count * 2)
    if source_type == "unknown" and history_unknown_count:
        explanation.append(f"This skill has remained unknown-source across {history_unknown_count} prior run(s).")
    if history_drift_count:
        explanation.append(f"This skill has drifted from baseline {history_drift_count} time(s) before.")

    explanation.extend(explain_issues(issues, "Local audit"))

    if upstream and not offline and source_allowed and allow_remote_check and (
        remote_state is None or remote_state.path_audit is None
    ):
        remote_state = inspect_remote(upstream)

    update_recommendation = "skip"
    diff_summary = {
        "changed_files": 0,
        "doc_only": False,
        "skill_md_changed": False,
        "metadata_changed": False,
        "scripts_changed": False,
        "executable_added": False,
        "changed": [],
        "added": [],
        "removed": [],
    }
    latest_commit = remote_state.latest_commit if remote_state else None
    latest_tag = remote_state.latest_tag if remote_state else None
    local_version = short_sha(str(current_commit)) if current_commit else (str(current_tag) if current_tag else None)
    latest_version = short_sha(latest_commit) if latest_commit else (latest_tag if latest_tag else None)
    update_available: bool | str = False

    if remote_state and latest_commit:
        if current_commit and latest_commit != current_commit:
            update_available = True
        elif current_tag and latest_tag and current_tag != latest_tag:
            update_available = True
        elif current_commit and latest_commit == current_commit:
            update_available = False
        elif current_tag and latest_tag and current_tag == latest_tag:
            update_available = False
        else:
            update_available = "unknown"

    version_gap_level, version_gap_reason = compute_version_gap(
        current_tag=current_tag,
        current_commit=current_commit,
        current_commit_date=current_commit_date,
        latest_tag=latest_tag,
        latest_commit=latest_commit,
        latest_commit_date=remote_state.latest_commit_date if remote_state else None,
    )
    if version_gap_reason and latest_commit:
        explanation.append(version_gap_reason)

    if remote_state and remote_state.error:
        explanation.append(f"Remote version check could not be completed: {remote_state.error}")
    elif upstream and remote_state and remote_state.path_audit and latest_commit:
        diff = compare_hashes(local_audit.file_hashes, remote_state.path_audit.file_hashes)
        diff_summary = classify_diff(diff)
        same_snapshot = local_audit.fingerprint == remote_state.path_audit.fingerprint
        if same_snapshot:
            update_available = False
            update_confidence = 94
            if not current_commit:
                local_version = short_sha(latest_commit)
                explanation.append("Local snapshot matches the latest upstream snapshot.")
        else:
            update_available = True
            remote_issue_points = sum(issue.points for issue in remote_state.path_audit.issues)
            if diff_summary["doc_only"]:
                update_confidence = 90
                update_recommendation = "update"
                explanation.append("Remote diff is docs-only, so update risk is low.")
            elif diff_summary["scripts_changed"] or diff_summary["executable_added"]:
                update_confidence = clamp(50 - remote_issue_points)
                update_recommendation = policy["max_script_change_action"]
                explanation.append("Remote diff changes scripts or executable behavior.")
            elif diff_summary["skill_md_changed"] or diff_summary["metadata_changed"]:
                update_confidence = 62
                update_recommendation = policy["metadata_change_action"]
                explanation.append("Remote diff changes skill instructions or metadata.")
            else:
                update_confidence = 72
                update_recommendation = "review"
                explanation.append("Remote diff is not docs-only, so review is recommended.")

            if update_confidence >= policy["min_update_confidence_for_update"] and diff_summary["doc_only"]:
                update_recommendation = "update"
            for remote_issue in remote_state.path_audit.issues:
                if severity_rank(remote_issue.severity) >= severity_rank("high"):
                    update_recommendation = "block"
            explanation.extend(explain_issues(remote_state.path_audit.issues, "Remote audit"))
    else:
        if upstream and not source_allowed:
            explanation.append("Remote inspection was skipped because the upstream violates source policy.")
        elif upstream:
            explanation.append("Remote update confidence is unavailable because no remote snapshot was checked.")
        update_available = update_available if upstream else False

    if baseline_status == "drifted" and update_recommendation == "skip":
        update_recommendation = "review"

    if not source_allowed:
        update_recommendation = "block"
        update_confidence = 0 if update_confidence is None else min(update_confidence, 10)

    source_confidence = clamp(source_confidence)
    integrity_confidence = clamp(integrity_confidence)
    behavior_confidence = clamp(behavior_confidence)
    version_confidence = {
        "installed-metadata": 95,
        "configured-upstream": 85,
        "bundled-default": 72,
        "unknown": 25,
    }[source_type]
    if update_available == "unknown":
        version_confidence = min(version_confidence, 65)
    if version_gap_level == "large":
        version_confidence = min(100, version_confidence + 5)

    weighted_values = {
        "source_confidence": source_confidence,
        "integrity_confidence": integrity_confidence,
        "behavior_confidence": behavior_confidence,
        "update_confidence": update_confidence if update_confidence is not None else 60,
    }
    trust_score = round(
        sum(weighted_values[key] * policy["weights"][key] for key in policy["weights"])
    )
    trust_score = clamp(trust_score)
    risk_level = risk_from_score(trust_score)

    change_lane, change_severity = classify_change_lane(diff_summary, baseline_status)
    why_flagged = None
    if update_recommendation == "block":
        why_flagged = "High-risk update or source policy violation requires blocking this skill for now."
    elif update_recommendation == "review":
        why_flagged = "This skill needs manual review because local drift or non-trivial changes were detected."
    elif update_recommendation == "update":
        why_flagged = "Only low-risk changes were detected, so this skill is safe to update."
    elif version_gap_level == "large":
        why_flagged = "The remote version appears far ahead of the local version, so this skill was prioritized for deeper checking."

    safe_next_step = "No action"
    if update_recommendation == "update":
        safe_next_step = "Preview or update this skill first."
    elif update_recommendation == "review":
        safe_next_step = "Review the flagged files before updating."
    elif update_recommendation == "block":
        safe_next_step = "Keep the current version and do not apply this update."
    elif baseline_status == "drifted":
        safe_next_step = "Inspect the local modifications before trusting future updates."

    top_risk_signals = [
        issue.detail
        for issue in sorted(issues, key=lambda item: (-severity_rank(item.severity), -item.points, item.file))[:3]
    ]
    if remote_state and remote_state.path_audit:
        for issue in sorted(remote_state.path_audit.issues, key=lambda item: (-severity_rank(item.severity), -item.points, item.file))[:3]:
            top_risk_signals.append(f"remote: {issue.detail}")

    version_status = {
        "local_version": local_version or "unknown",
        "latest_version": latest_version or ("unavailable" if upstream else "unknown"),
        "latest_tag": latest_tag,
        "latest_commit_date": remote_state.latest_commit_date if remote_state else None,
        "update_available": update_available,
        "version_confidence": version_confidence,
    }

    payload = {
        "skill_id": skill_id,
        "name": skill_name,
        "path": str(skill_path),
        "root": root,
        "host_adapter": host_adapter,
        "source_type": source_type,
        "upstream": asdict(upstream) if upstream else None,
        "version_status": version_status,
        "source_confidence": source_confidence,
        "integrity_confidence": integrity_confidence,
        "behavior_confidence": behavior_confidence,
        "update_confidence": update_confidence,
        "trust_score": trust_score,
        "risk_level": risk_level,
        "update_recommendation": update_recommendation,
        "confidence_explainer": explanation,
        "top_risk_signals": top_risk_signals,
        "baseline_status": baseline_status,
        "drift_summary": drift_summary,
        "local_modification_risk": local_modification_risk,
        "version_gap_level": version_gap_level,
        "change_lane": change_lane,
        "change_severity": change_severity,
        "why_this_update_is_flagged": why_flagged,
        "reputation_score": reputation_score,
        "historical_risk_trend": historical_risk_trend,
        "upstream_stability": upstream_stability,
        "safe_next_step": safe_next_step,
        "remote_check_reason": remote_check_reason,
        "requested_mode": requested_mode,
        "diff_summary": diff_summary,
        "local_issues": [asdict(item) for item in issues],
        "remote_issues": [asdict(item) for item in (remote_state.path_audit.issues if remote_state and remote_state.path_audit else [])],
        "metadata": metadata if isinstance(metadata, dict) else None,
        "local_fingerprint": local_audit.fingerprint,
        "latest_commit_full": latest_commit,
        "latest_tag_full": latest_tag,
        "latest_commit_date": remote_state.latest_commit_date if remote_state else None,
        "current_commit_full": current_commit,
        "current_tag_full": current_tag,
        "current_commit_date": current_commit_date,
        "baseline": build_baseline_snapshot(local_audit, upstream),
    }
    return payload


def update_lock_data(
    lock_data: dict[str, Any],
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    lock_data["version"] = LOCK_VERSION
    lock_data.setdefault("skills", {})
    for report in reports:
        entry = lock_data["skills"].get(report["skill_id"], {})
        if not isinstance(entry, dict):
            entry = {}
        entry["name"] = report["name"]
        entry["root"] = report["root"]
        entry["host_adapter"] = report["host_adapter"]
        entry["source_type"] = report["source_type"]
        entry["upstream"] = report["upstream"]
        entry["baseline"] = report.get("baseline") or entry.get("baseline")
        entry["baseline_status"] = report.get("baseline_status")
        entry["local_fingerprint"] = report.get("local_fingerprint") or entry.get("local_fingerprint")
        entry["current_commit"] = report.get("current_commit_full") or (
            report.get("latest_commit_full")
            if report["version_status"]["update_available"] is False and report["upstream"]
            else entry.get("current_commit")
        )
        entry["current_tag"] = report.get("current_tag_full") or report.get("latest_tag_full") or entry.get("current_tag")
        entry["current_commit_date"] = report.get("current_commit_date") or entry.get("current_commit_date")
        entry["last_known_local_version"] = report["version_status"]["local_version"]
        entry["last_known_remote_version"] = report["version_status"]["latest_version"]
        entry["last_known_local_version_date"] = report.get("current_commit_date") or entry.get("last_known_local_version_date")
        entry["last_known_remote_version_date"] = report.get("latest_commit_date") or entry.get("last_known_remote_version_date")
        entry["version_gap_level"] = report.get("version_gap_level", "unknown")
        entry["local_modification_risk"] = report.get("local_modification_risk", "low")
        entry["reputation_score"] = report.get("reputation_score")
        entry["historical_risk_trend"] = report.get("historical_risk_trend")
        entry["upstream_stability"] = report.get("upstream_stability")
        if report.get("remote_check_reason"):
            entry["last_remote_checked_at"] = now_iso()
        entry["last_checked_at"] = now_iso()
        entry["last_trust_score"] = report["trust_score"]
        entry["last_risk_level"] = report["risk_level"]
        entry["last_recommendation"] = report["update_recommendation"]
        entry["review_count"] = int(entry.get("review_count", 0) or 0)
        entry["block_count"] = int(entry.get("block_count", 0) or 0)
        entry["unknown_source_count"] = int(entry.get("unknown_source_count", 0) or 0)
        entry["drift_count"] = int(entry.get("drift_count", 0) or 0)
        if report["update_recommendation"] == "review":
            entry["review_count"] += 1
        if report["update_recommendation"] == "block":
            entry["block_count"] += 1
        if report["source_type"] == "unknown":
            entry["unknown_source_count"] += 1
        if report.get("baseline_status") == "drifted":
            entry["drift_count"] += 1
        lock_data["skills"][report["skill_id"]] = entry
    return lock_data


def render_markdown(payload: dict[str, Any]) -> str:
    reports = payload["reports"]
    roots = payload["roots"]
    review_updates = [report for report in reports if report["update_recommendation"] == "review"]
    blocked_updates = [report for report in reports if report["update_recommendation"] == "block"]
    safe_updates = [report for report in reports if report["update_recommendation"] == "update"]
    drifted = [report for report in reports if report["baseline_status"] == "drifted"]
    large_gap = [report for report in reports if report["version_gap_level"] == "large"]
    already_current = [report for report in reports if report["upstream"] and report["version_status"]["update_available"] is False]

    lines = [
        "# Skill Guardian Report",
        "",
        f"- Overall action: `{payload['overall_action']}`",
        f"- Mode: `{payload['mode']}`",
        f"- Scan roots: `{len(roots)}`",
        f"- Detected local skills: `{len(reports)}`",
        f"- Recognized upstream versions: `{len([r for r in reports if r['source_type'] != 'unknown'])}`",
        f"- Safe updates: `{len(safe_updates)}`",
        f"- Risky updates: `{len(review_updates) + len(blocked_updates)}`",
        f"- Local drift detected: `{len(drifted)}`",
        f"- Large version gaps: `{len(large_gap)}`",
        f"- Connectivity: `{'offline' if payload['offline'] else 'online'}`",
        f"- Policy: `{payload['policy']}`",
    ]
    if payload.get("skipped"):
        skipped_bits = []
        for root_path, names in payload["skipped"].items():
            skipped_bits.append(f"{root_path}: {', '.join(names)}")
        if skipped_bits:
            lines.append(f"- Skipped entries: `{' | '.join(skipped_bits)}`")

    lines.extend(
        [
            "",
            "## Action Summary",
            "",
        ]
    )
    if blocked_updates:
        lines.append("- Overall action: `Blocked`")
        lines.append(f"- Blocked now: `{', '.join(report['name'] for report in blocked_updates)}`")
    elif review_updates or drifted:
        lines.append("- Overall action: `Review required`")
        if review_updates:
            lines.append(f"- Review before updating: `{', '.join(report['name'] for report in review_updates)}`")
        if drifted:
            lines.append(f"- Local drift detected: `{', '.join(report['name'] for report in drifted)}`")
        if large_gap:
            lines.append(f"- Large version gap: `{', '.join(report['name'] for report in large_gap)}`")
        lines.append("- Next step: inspect the flagged skills before updating.")
    elif safe_updates:
        names = ", ".join(report["name"] for report in safe_updates)
        lines.append("- Overall action: `Safe to update`")
        lines.append(f"- Next step: update `{names}` first.")
    else:
        lines.append("- Overall action: `No action`")
        lines.append("- Next step: no update is needed right now.")

    lines.extend(
        [
            "",
            "## Decision Summary",
            "",
            f"- Already current: `{len(already_current)}`",
            f"- Safe to update now: `{', '.join(r['name'] for r in safe_updates) or 'none'}`",
            f"- Review before updating: `{', '.join(r['name'] for r in review_updates) or 'none'}`",
            f"- Blocked updates: `{', '.join(r['name'] for r in blocked_updates) or 'none'}`",
            f"- Local drift: `{', '.join(r['name'] for r in drifted) or 'none'}`",
            f"- Large version gaps: `{', '.join(r['name'] for r in large_gap) or 'none'}`",
        ]
    )
    if blocked_updates:
        lines.append("- Recommendation: keep blocked skills on their current versions and investigate before updating.")
    elif review_updates or drifted:
        lines.append("- Recommendation: review the flagged skills before updating anything.")
    elif safe_updates:
        names = ", ".join(report["name"] for report in safe_updates)
        lines.append(f"- Recommendation: update `{names}` first.")
    else:
        lines.append("- Recommendation: no update is needed right now.")

    if payload.get("summary_only"):
        return "\n".join(lines).strip() + "\n"

    lines.extend(["", "## Skills", ""])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        grouped.setdefault(report["root"], []).append(report)
    detail_candidates = [
        report
        for report in reports
        if report["update_recommendation"] in {"update", "review", "block"}
        or report["baseline_status"] == "drifted"
        or report["version_gap_level"] == "large"
    ]
    detailed_ids = {
        report["skill_id"]
        for report in detail_candidates[: payload.get("max_detailed_skills", 5)]
    }
    for root_path, root_reports in grouped.items():
        adapter = root_reports[0]["host_adapter"]
        lines.append(f"### {root_path}")
        lines.append("")
        lines.append(f"- Host adapter: `{adapter}`")
        lines.append(f"- Skills found: `{len(root_reports)}`")
        lines.append("")
        for report in root_reports:
            lines.append(
                f"- `{report['name']}`: `{report['update_recommendation']}` | gap `{report['version_gap_level']}` | baseline `{report['baseline_status']}` | trust `{report['trust_score']}`"
            )
            if report["skill_id"] not in detailed_ids:
                continue
            lines.extend(
                [
                    "",
                    f"#### {report['name']}",
                    "",
                    f"- Source type: `{report['source_type']}`",
                    f"- Version status: local `{report['version_status']['local_version']}`, latest `{report['version_status']['latest_version']}`",
                    f"- Version confidence: `{report['version_status']['version_confidence']}`",
                    f"- Update available: `{report['version_status']['update_available']}`",
                    f"- Trust score: `{report['trust_score']}`",
                    f"- Risk level: `{report['risk_level']}`",
                    f"- Update recommendation: `{report['update_recommendation']}`",
                    f"- Baseline status: `{report['baseline_status']}`",
                    f"- Version gap: `{report['version_gap_level']}`",
                    f"- Local modification risk: `{report['local_modification_risk']}`",
                    f"- Change lane: `{report['change_lane']}`",
                    f"- Remote check reason: `{report['remote_check_reason'] or 'not escalated'}`",
                ]
            )
            if report["upstream"]:
                lines.append(
                    f"- Upstream: `{report['upstream']['repo']}/{report['upstream']['path']}@{report['upstream'].get('ref', 'main')}`"
                )
            else:
                lines.append("- Upstream: `unknown`")
            if report["top_risk_signals"]:
                lines.append(f"- Top risk signals: `{'; '.join(report['top_risk_signals'])}`")
            if report["diff_summary"]["changed_files"]:
                lines.append(f"- Diff summary: `{report['diff_summary']['changed_files']}` changed/added/removed file(s)")
            if report["drift_summary"]:
                lines.append(f"- Drift summary: `{report['drift_summary']}`")
            if report["why_this_update_is_flagged"]:
                lines.append(f"- Why flagged: `{report['why_this_update_is_flagged']}`")
            lines.append(f"- Safe next step: `{report['safe_next_step']}`")
            if report["confidence_explainer"]:
                lines.append("- Confidence explainer:")
                for item in report["confidence_explainer"][:6]:
                    lines.append(f"  - {item}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_sarif(payload: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for report in payload["reports"]:
        for issue in report["local_issues"]:
            results.append(
                {
                    "ruleId": f"skill-guardian/{issue['category']}",
                    "level": {"low": "note", "medium": "warning", "high": "error", "critical": "error"}[issue["severity"]],
                    "message": {"text": f"{report['name']}: {issue['detail']}"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": report["path"]},
                            }
                        }
                    ],
                }
            )
        for issue in report["remote_issues"]:
            results.append(
                {
                    "ruleId": f"skill-guardian/remote-{issue['category']}",
                    "level": {"low": "note", "medium": "warning", "high": "error", "critical": "error"}[issue["severity"]],
                    "message": {"text": f"{report['name']} (remote): {issue['detail']}"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": report["upstream"]["repo"] if report["upstream"] else report["name"]},
                            }
                        }
                    ],
                }
            )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "skill-guardian",
                        "informationUri": "https://github.com/",
                        "rules": [],
                    }
                },
                "results": results,
            }
        ],
    }


def should_auto_upgrade_to_standard(report: dict[str, Any], lock_entry: dict[str, Any]) -> bool:
    if report["baseline_status"] == "drifted":
        return True
    if report["source_type"] == "unknown" and severity_rank(report["risk_level"]) >= severity_rank("high"):
        return True
    if report["version_gap_level"] == "large" and report["upstream"]:
        return True
    if not lock_entry.get("last_remote_checked_at") and report["upstream"]:
        return True
    if int(lock_entry.get("review_count", 0) or 0) >= 2 or int(lock_entry.get("block_count", 0) or 0) >= 1:
        return True
    return False


def candidate_priority(report: dict[str, Any], lock_entry: dict[str, Any]) -> tuple[int, str]:
    if report["baseline_status"] == "drifted":
        return (0, report["name"])
    if report["version_gap_level"] == "large":
        return (1, report["name"])
    if int(lock_entry.get("block_count", 0) or 0) > 0 or int(lock_entry.get("review_count", 0) or 0) > 1:
        return (2, report["name"])
    if report["source_type"] == "unknown":
        return (3, report["name"])
    return (4, report["name"])


def remote_check_reason_for(report: dict[str, Any], lock_entry: dict[str, Any]) -> str:
    if report["baseline_status"] == "drifted":
        return "local drift detected"
    if report["version_gap_level"] == "large":
        return "large version gap"
    if report["version_gap_level"] == "medium":
        return "medium version gap"
    if int(lock_entry.get("block_count", 0) or 0) > 0 or int(lock_entry.get("review_count", 0) or 0) > 1:
        return "historical review or block history"
    if not lock_entry.get("last_remote_checked_at"):
        return "never checked remotely"
    return "candidate update inspection"


def summarize_overall_action(reports: list[dict[str, Any]]) -> str:
    if any(report["update_recommendation"] == "block" for report in reports):
        return "Blocked"
    if any(report["update_recommendation"] == "review" for report in reports) or any(
        report["baseline_status"] == "drifted" for report in reports
    ):
        return "Review required"
    if any(report["update_recommendation"] == "update" for report in reports):
        return "Safe to update"
    return "No action"


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(is_truthy_path(args.config_file) or default_config_path(for_write=False))
    policy_name = args.policy or config.get("policy", "balanced")
    if policy_name not in POLICY_PRESETS:
        policy_name = "balanced"
    requested_mode = args.mode or "standard"
    if requested_mode not in {"quick", "standard", "deep"}:
        requested_mode = "standard"
    explicit_max_remote_checks = args.max_remote_checks is not None
    max_remote_checks = (
        args.max_remote_checks
        if explicit_max_remote_checks
        else default_max_remote_checks(requested_mode)
    )
    max_detailed_skills = args.max_detailed_skills if args.max_detailed_skills is not None else 5

    roots = discover_roots(config, explicit_roots=args.roots)
    lock_path = is_truthy_path(args.lock_file) or default_lock_path(for_write=bool(args.write_lock))
    history_path = is_truthy_path(args.history_file) or default_history_path(for_write=bool(args.write_lock))
    default_upstreams = load_default_upstreams(is_truthy_path(args.bundled_upstreams))
    lock_data = load_lock(lock_path)

    entries: list[dict[str, Any]] = []
    remote_metadata_by_skill_id: dict[str, RemoteState] = {}
    reports: list[dict[str, Any]] = []
    skipped: dict[str, list[str]] = {}
    for root in roots:
        local_entries, skipped_names = enumerate_local_skills(root)
        if skipped_names:
            skipped[str(root.path)] = skipped_names
        entries.extend(local_entries)

    for entry in entries:
        skill_id = entry["skill_id"]
        source_type, upstream, _ = resolve_upstream(
            skill_name=entry["name"],
            skill_path=entry["path"],
            config=config,
            lock_data=lock_data,
            default_upstreams=default_upstreams,
        )
        if upstream and not args.offline:
            remote_metadata_by_skill_id[skill_id] = inspect_remote_metadata(upstream)
        report = compute_report(
            entry=entry,
            config=config,
            lock_data=lock_data,
            default_upstreams=default_upstreams,
            policy_name=policy_name,
            offline=args.offline,
            remote_state=remote_metadata_by_skill_id.get(skill_id),
            allow_remote_check=False,
            requested_mode=requested_mode,
        )
        report["local_fingerprint"] = entry["local_audit"].fingerprint
        reports.append(report)

    effective_mode = requested_mode
    if requested_mode == "quick" and not args.offline:
        for report in reports:
            lock_entry = lock_data.get("skills", {}).get(report["skill_id"], {})
            if not isinstance(lock_entry, dict):
                lock_entry = {}
            if should_auto_upgrade_to_standard(report, lock_entry):
                effective_mode = "standard"
                break
    if effective_mode != requested_mode and not explicit_max_remote_checks:
        max_remote_checks = default_max_remote_checks(effective_mode)

    if effective_mode in {"standard", "deep"} and not args.offline:
        candidate_reports = []
        for report in reports:
            if not report["upstream"]:
                continue
            if effective_mode == "deep":
                candidate_reports.append(report)
                continue
            lock_entry = lock_data.get("skills", {}).get(report["skill_id"], {})
            if not isinstance(lock_entry, dict):
                lock_entry = {}
            if (
                report["baseline_status"] != "unchanged"
                or report["version_gap_level"] in {"medium", "large"}
                or int(lock_entry.get("review_count", 0) or 0) > 1
                or int(lock_entry.get("block_count", 0) or 0) > 0
                or not lock_entry.get("last_remote_checked_at")
            ):
                candidate_reports.append(report)

        if effective_mode == "standard":
            candidate_reports.sort(
                key=lambda item: candidate_priority(
                    item,
                    lock_data.get("skills", {}).get(item["skill_id"], {}) if isinstance(lock_data.get("skills", {}).get(item["skill_id"], {}), dict) else {},
                )
            )
            candidate_reports = candidate_reports[:max_remote_checks]

        candidate_lookup = {report["skill_id"]: report for report in candidate_reports}
        refreshed_reports: list[dict[str, Any]] = []
        for entry in entries:
            skill_id = entry["skill_id"]
            prior = next(report for report in reports if report["skill_id"] == skill_id)
            candidate = candidate_lookup.get(skill_id)
            if not candidate:
                refreshed_reports.append(prior)
                continue
            lock_entry = lock_data.get("skills", {}).get(skill_id, {})
            if not isinstance(lock_entry, dict):
                lock_entry = {}
            refreshed = compute_report(
                entry=entry,
                config=config,
                lock_data=lock_data,
                default_upstreams=default_upstreams,
                policy_name=policy_name,
                offline=args.offline,
                remote_state=remote_metadata_by_skill_id.get(skill_id),
                allow_remote_check=True,
                requested_mode=effective_mode,
                remote_check_reason=remote_check_reason_for(candidate, lock_entry),
            )
            refreshed["local_fingerprint"] = entry["local_audit"].fingerprint
            refreshed_reports.append(refreshed)
        reports = refreshed_reports

    reports.sort(key=lambda item: (-severity_rank(item["risk_level"]), item["trust_score"], item["name"]), reverse=True)
    overall_action = summarize_overall_action(reports)
    payload = {
        "generated_at": now_iso(),
        "policy": policy_name,
        "offline": bool(args.offline),
        "mode": effective_mode,
        "requested_mode": requested_mode,
        "summary_only": bool(args.summary_only),
        "max_detailed_skills": max_detailed_skills,
        "roots": [asdict(root) | {"path": str(root.path)} for root in roots],
        "reports": reports,
        "skipped": skipped,
        "overall_action": overall_action,
        "skills_safe_to_update": [report["name"] for report in reports if report["update_recommendation"] == "update"],
        "skills_requiring_review": [report["name"] for report in reports if report["update_recommendation"] == "review"],
        "skills_blocked": [report["name"] for report in reports if report["update_recommendation"] == "block"],
        "skills_with_local_drift": [report["name"] for report in reports if report["baseline_status"] == "drifted"],
        "skills_with_large_version_gap": [report["name"] for report in reports if report["version_gap_level"] == "large"],
    }

    if args.write_lock:
        updated = update_lock_data(lock_data, reports)
        write_json(lock_path, updated)
        append_history(
            history_path,
            {
                "generated_at": payload["generated_at"],
                "policy": policy_name,
                "offline": bool(args.offline),
                "mode": effective_mode,
                "overall_action": overall_action,
                "report_count": len(reports),
                "recommendations": {report["skill_id"]: report["update_recommendation"] for report in reports},
            },
        )

    return payload


def run_roots_list(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(is_truthy_path(args.config_file) or default_config_path(for_write=False))
    roots = discover_roots(config, explicit_roots=args.roots)
    return {
        "generated_at": now_iso(),
        "roots": [
            {
                "path": str(root.path),
                "host_adapter": root.host_adapter,
                "discovery_source": root.discovery_source,
            }
            for root in roots
        ],
    }


def run_config_init(args: argparse.Namespace) -> dict[str, Any]:
    config_path = is_truthy_path(args.config_file) or default_config_path(for_write=True)
    if config_path.exists() and not args.force:
        return {
            "created": False,
            "config_file": str(config_path),
            "message": "Config file already exists. Use --force to overwrite it.",
        }
    config = default_config()
    write_json(config_path, config)
    return {"created": True, "config_file": str(config_path), "config": config}


def run_mappings_sync(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(is_truthy_path(args.config_file) or default_config_path(for_write=False))
    roots = discover_roots(config, explicit_roots=args.roots)
    lock_path = is_truthy_path(args.lock_file) or default_lock_path(for_write=True)
    lock_data = load_lock(lock_path)
    default_upstreams = load_default_upstreams(is_truthy_path(args.bundled_upstreams))
    synced: list[dict[str, Any]] = []
    lock_data.setdefault("skills", {})

    for root in roots:
        entries, _ = enumerate_local_skills(root)
        for entry in entries:
            source_type, upstream, _ = resolve_upstream(
                skill_name=entry["name"],
                skill_path=entry["path"],
                config=config,
                lock_data=lock_data,
                default_upstreams=default_upstreams,
            )
            skill_id = entry["skill_id"]
            lock_entry = lock_data["skills"].get(skill_id, {})
            if not isinstance(lock_entry, dict):
                lock_entry = {}
            lock_entry["name"] = entry["name"]
            lock_entry["root"] = entry["root"]
            lock_entry["host_adapter"] = entry["host_adapter"]
            lock_entry["source_type"] = source_type
            lock_entry["upstream"] = asdict(upstream) if upstream else None
            lock_entry["last_synced_at"] = now_iso()
            lock_data["skills"][skill_id] = lock_entry
            synced.append(
                {
                    "skill_id": skill_id,
                    "name": entry["name"],
                    "root": entry["root"],
                    "source_type": source_type,
                    "upstream": asdict(upstream) if upstream else None,
                }
            )
    write_json(lock_path, lock_data)
    return {"synced": synced, "lock_file": str(lock_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-guardian",
        description="Audit local agent skills and review update trust.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Scan local skill roots and score update safety.")
    audit.add_argument("--offline", action="store_true")
    audit.add_argument("--format", choices=["markdown", "json", "sarif"], default="markdown")
    audit.add_argument("--roots", nargs="*", default=None)
    audit.add_argument("--mode", choices=["quick", "standard", "deep"], default="standard")
    audit.add_argument("--policy", choices=sorted(POLICY_PRESETS), default=None)
    audit.add_argument("--summary-only", action="store_true")
    audit.add_argument("--max-detailed-skills", type=int, default=None)
    audit.add_argument("--max-remote-checks", type=int, default=None)
    audit.add_argument("--write-lock", action="store_true")
    audit.add_argument("--config-file", default=None)
    audit.add_argument("--lock-file", default=None)
    audit.add_argument("--history-file", default=None)
    audit.add_argument("--bundled-upstreams", default=None)

    config_init = subparsers.add_parser("config", help="Manage config files.")
    config_sub = config_init.add_subparsers(dest="config_command", required=True)
    config_init_cmd = config_sub.add_parser("init", help="Create a default config.json.")
    config_init_cmd.add_argument("--config-file", default=None)
    config_init_cmd.add_argument("--force", action="store_true")

    roots = subparsers.add_parser("roots", help="Inspect detected skill roots.")
    roots_sub = roots.add_subparsers(dest="roots_command", required=True)
    roots_list = roots_sub.add_parser("list", help="List detected roots.")
    roots_list.add_argument("--roots", nargs="*", default=None)
    roots_list.add_argument("--config-file", default=None)
    roots_list.add_argument("--format", choices=["markdown", "json"], default="markdown")

    mappings = subparsers.add_parser("mappings", help="Work with upstream mapping state.")
    mappings_sub = mappings.add_subparsers(dest="mappings_command", required=True)
    mappings_sync = mappings_sub.add_parser("sync", help="Sync resolved upstream mappings into the lock file.")
    mappings_sync.add_argument("--roots", nargs="*", default=None)
    mappings_sync.add_argument("--config-file", default=None)
    mappings_sync.add_argument("--lock-file", default=None)
    mappings_sync.add_argument("--bundled-upstreams", default=None)
    mappings_sync.add_argument("--format", choices=["markdown", "json"], default="markdown")

    return parser


def render_simple_markdown(title: str, items: list[str]) -> str:
    lines = [f"# {title}", ""]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines).strip() + "\n"


def write_payload(payload: Any, output_format: str, stream: TextIO) -> None:
    if output_format == "json":
        stream.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    elif output_format == "sarif":
        stream.write(json.dumps(render_sarif(payload), indent=2, ensure_ascii=False) + "\n")
    elif output_format == "markdown":
        if isinstance(payload, dict) and "reports" in payload:
            stream.write(render_markdown(payload))
        elif isinstance(payload, dict) and "roots" in payload and "generated_at" in payload and "reports" not in payload:
            items = [
                f"`{item['path']}` ({item['host_adapter']}, discovered via {item['discovery_source']})"
                for item in payload["roots"]
            ]
            stream.write(render_simple_markdown("Detected Skill Roots", items or ["No roots detected."]))
        elif isinstance(payload, dict) and "synced" in payload:
            items = [
                f"`{item['name']}` -> `{item['source_type']}`"
                for item in payload["synced"]
            ]
            stream.write(render_simple_markdown("Synced Mappings", items or ["No skills were synced."]))
        elif isinstance(payload, dict) and "config_file" in payload:
            items = [f"Config file: `{payload['config_file']}`", payload.get("message", "Config initialized.")]
            stream.write(render_simple_markdown("Configuration", items))
        else:
            stream.write(render_simple_markdown("Skill Guardian", ["No output."]))
    else:
        raise ValueError(f"Unsupported output format: {output_format}")


def entrypoint(argv: list[str] | None = None, *, stream: TextIO | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if stream is None:
        stream = sys.stdout

    try:
        if args.command == "audit":
            payload = run_audit(args)
            write_payload(payload, args.format, stream)
            return 0
        if args.command == "config" and args.config_command == "init":
            payload = run_config_init(args)
            write_payload(payload, "markdown", stream)
            return 0 if payload.get("created") else 1
        if args.command == "roots" and args.roots_command == "list":
            payload = run_roots_list(args)
            write_payload(payload, args.format, stream)
            return 0
        if args.command == "mappings" and args.mappings_command == "sync":
            payload = run_mappings_sync(args)
            write_payload(payload, args.format, stream)
            return 0
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    parser.error("Unsupported command")
    return 2
