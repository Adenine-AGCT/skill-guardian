# skill-guardian

[中文说明](./README.zh-CN.md)

`skill-guardian` is a safety-first auditor for Agent Skills. It inspects local skill directories, explains where versions come from, checks known GitHub upstreams when available, and finishes with a clear recommendation about whether any installed skills should be updated now, reviewed first, or blocked.

## Why Use It

Installing or updating skills blindly is convenient, but it also hides risk. `skill-guardian` is designed for people who want visibility before they trust a change.

It helps you:

- discover local skills across supported agent roots
- understand which skills have known upstream provenance
- compare local skills with newer GitHub versions when possible
- flag risky changes such as script edits, executables, or unknown sources
- get a human-readable decision instead of raw diff noise

## Quick Start

Run a local audit directly from the repository:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --offline --write-lock
```

Initialize a user config:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian config init
```

List detected skill roots:

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian roots list
```

## Typical Use Cases

- You have multiple local skills and want a quick inventory before cleaning them up.
- You want to know whether an installed skill came from a known GitHub source or is locally modified.
- You want an update recommendation that distinguishes safe metadata changes from risky script changes.
- You want to audit skills on a machine without automatically changing anything.

## What the Results Mean

Each skill report includes a few core outputs:

- `trust_score`
  A 0-100 confidence score that combines source, integrity, behavior, and update signals.
- `risk_level`
  A simple label such as `low`, `medium`, `high`, or `critical`.
- `update_recommendation`
  One of `update`, `review`, `block`, or `skip`.
- `confidence_explainer`
  A short explanation of why the recommendation was made.

The tool is advisory by design. It does not auto-update installed skills.

## Default Discovery

By default, `skill-guardian` looks for skills in:

- `~/.codex/skills`
- `~/.agents/skills`
- extra roots declared in `config.json`

## Configuration

Runtime state is stored outside installed skill folders.

- Windows: `%APPDATA%/skill-guardian/`
- macOS: `~/Library/Application Support/skill-guardian/`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/skill-guardian/`

The state directory contains:

- `config.json`
- `skills.lock.json`
- `history.jsonl`

Use `SKILL_GUARDIAN_STATE_DIR` to override the default state location.

Custom upstream mappings can be added in `config.json`:

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

## Using the Bundled Skill

This repository also includes a publishable Agent Skill under `skills/skill-guardian/`.

When triggered as a skill, it runs the same audit engine and produces a user-facing summary instead of raw machine output. The bundled skill is useful when you want the audit experience inside a skills-capable agent rather than from the CLI.

## Safety Model

`skill-guardian` is intentionally conservative.

By default it:

- does not auto-update installed skills
- does not execute remote scripts while inspecting them
- treats changes under `scripts/` and executable additions as review-heavy
- still completes a local audit when remote inspection is unavailable
- keeps runtime state outside installed skill directories

## Development and Publishing

For contributors and maintainers, a minimal verification flow is:

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
gh skill preview
gh skill publish
```
