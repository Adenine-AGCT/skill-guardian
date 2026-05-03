# Skill Guardian Skill

[中文说明](./README.zh-CN.md)

`Skill Guardian Skill` is an installable GitHub skill for governing local Agent Skills before you update them. It is designed to stay lightweight by default: start with a quick audit, detect provenance and local drift first, and only escalate to deeper remote checks when the evidence justifies it.

## Install the Skill

Install the published skill with GitHub CLI:

```powershell
gh skill install Adenine-AGCT/skill-guardian
```

Once installed, use it when you want a low-overhead trust and update review for local skills instead of blindly updating them.

## Why It Does Not Waste Tokens

Skill Guardian is intentionally staged:

- `quick` mode is the default path for the published skill
- local provenance, baseline, and drift checks run before heavier remote analysis
- deeper checks are only triggered for risky cases such as local drift, blocked history, or large version gaps
- markdown output stays compact by default and expands only the most important skills

This keeps the skill useful for frequent checks without paying the cost of a full deep audit every time it is triggered.

## What This Skill Does

After installation, Skill Guardian can help you:

- discover local skills across supported agent roots
- identify which skills have known upstream provenance
- detect local drift from the last trusted baseline
- estimate whether a local version is far behind GitHub before doing a deeper comparison
- classify risky changes such as script edits, executables, or unknown sources
- turn all of that into a clear update decision

## What You Can Expect

A typical run starts with a compact summary such as:

```text
Overall action: Review required
Mode: quick
Local drift detected: analytics-skill
Large version gap: deploy-skill
Review required: analytics-skill
```

The most important per-skill fields include:

- `baseline_status`
- `version_gap_level`
- `trust_score`
- `risk_level`
- `update_recommendation`
- `safe_next_step`

## Audit Modes

Skill Guardian now uses three audit modes:

- `quick`
  Default for the published skill. Focuses on local inventory, provenance, baseline, and drift. Uses only lightweight remote metadata when needed.
- `standard`
  Runs a limited number of deeper remote checks for the most relevant candidates.
- `deep`
  Performs full remote comparison and richer explanation across all relevant candidates.

Large version gaps can automatically promote a run from `quick` to `standard`, but they do not automatically mean an update is safe.

## Local CLI Backend

This repository also ships the CLI and Python backend that powers the published skill.

Run a local audit directly from the repository:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --offline --write-lock
```

Run an explicit quick audit:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --mode quick
```

Run a deeper audit when you really need it:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --mode deep --max-remote-checks 999
```

Initialize a user config:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian config init
```

## Discovery and Configuration

By default, the backend looks for skills in:

- `~/.codex/skills`
- `~/.agents/skills`
- extra roots declared in `config.json`

Runtime state is stored outside installed skill folders:

- Windows: `%APPDATA%/skill-guardian/`
- macOS: `~/Library/Application Support/skill-guardian/`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/skill-guardian/`

The state directory contains:

- `config.json`
- `skills.lock.json`
- `history.jsonl`

Use `SKILL_GUARDIAN_STATE_DIR` to override the default state location.

## Safety Model

Skill Guardian is intentionally conservative.

By default it:

- does not auto-update installed skills
- does not execute remote scripts while inspecting them
- treats changes under `scripts/` and executable additions as review-heavy
- prefers quick audits and escalates only when needed
- still completes a local audit when remote inspection is unavailable
- keeps runtime state outside installed skill directories

## Development and Release

Minimal verification flow:

```powershell
python -m unittest discover -s .\tests -v
python .\scripts\sync_skill_runtime.py --check
```

To publish the bundled skill, first verify the runtime from the repository root:

```powershell
python .\scripts\sync_skill_runtime.py
python .\scripts\sync_skill_runtime.py --check
```

Then switch into the skill directory and run:

```powershell
cd .\skills\skill-guardian
gh skill publish --tag v0.2.0
gh skill preview Adenine-AGCT/skill-guardian skill-guardian
```

After publishing, keep `v*` tags protected with a GitHub tag ruleset.
