# skill-guardian

[中文说明](./README.zh-CN.md)

`skill-guardian` is a safety-first auditor for Agent Skills.

It scans local skill roots, resolves known upstreams, compares local snapshots with remote candidates, scores trust and update risk, and ends with a clear recommendation about whether a skill should be updated now, reviewed first, or blocked.

This repository ships two deliverables:

- a reusable Python CLI
- a publishable Agent Skill under `skills/skill-guardian/`

## Highlights

- Multi-root discovery for `~/.codex/skills`, `~/.agents/skills`, and custom roots
- Explainable scoring across source, integrity, behavior, and update risk
- Offline local audits when network access is unavailable
- Similarity and impersonation warnings for unknown-source skills
- Policy packs: `conservative`, `balanced`, `research`
- Machine-readable outputs: `markdown`, `json`, `sarif`
- Portable state storage outside installed skill directories

## Quick Start

Run directly from the repository:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --offline --write-lock
```

Initialize a user config:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian config init
```

List detected roots:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian roots list
```

## Default Scan Roots

- `~/.codex/skills`
- `~/.agents/skills`
- extra roots from `config.json`

## State Directory

Runtime state is stored outside installed skill folders.

- Windows: `%APPDATA%/skill-guardian/`
- macOS: `~/Library/Application Support/skill-guardian/`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/skill-guardian/`

The state directory contains:

- `config.json`
- `skills.lock.json`
- `history.jsonl`

Set `SKILL_GUARDIAN_STATE_DIR` to override the location.

## CLI

Main commands:

- `skill-guardian audit`
- `skill-guardian config init`
- `skill-guardian roots list`
- `skill-guardian mappings sync`

Example:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --policy balanced --format markdown --write-lock
```

## Output Model

Each skill report includes:

- `source_type`
- `version_status`
- `trust_score`
- `risk_level`
- `update_recommendation`
- `confidence_explainer`
- `top_risk_signals`
- `diff_summary`
- `local_issues`
- `remote_issues`

## Custom Upstream Mappings

User-defined upstream mappings live in `config.json`:

```json
{
  "upstreams": {
    "my-skill": {
      "repo": "owner/repo",
      "path": "skills/my-skill",
      "ref": "main"
    }
  }
}
```

## Publishing the Bundled Skill

The publishable skill is located in `skills/skill-guardian/`.

First, sync and verify the bundled runtime from the repository root:

```powershell
python .\scripts\sync_skill_runtime.py
python .\scripts\sync_skill_runtime.py --check
```

Then switch into the bundled skill directory and run the GitHub Skill commands there:

```powershell
cd .\skills\skill-guardian
gh skill preview
gh skill publish
```

## Development and Release Hygiene

Before opening a pull request or publishing the bundled skill, verify:

- `python -m unittest discover -s tests -v`
- `python .\scripts\sync_skill_runtime.py`
- no `.tmp-tests/` directory remains
- no `.skill-guardian-state/` directory remains
- no `__pycache__` or `*.pyc` files are present
- no machine-local state files are committed

## What "Safe" Means Here

`skill-guardian` is advisory. It does not auto-update installed skills.

By default it:

- never executes remote scripts while inspecting them
- treats script and executable changes as review-heavy
- keeps runtime state outside installed skill folders
- explains why an update is recommended, review-required, blocked, or skipped
