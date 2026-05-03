# Skill Guardian Skill

[中文说明](./README.zh-CN.md)

`Skill Guardian Skill` is a publishable GitHub skill for auditing installed Agent Skills before you update them. It helps you understand where local skills came from, what changed upstream, how risky an update looks, and whether you should update now, review first, block a change, or leave everything alone.

## Install the Skill

Install the published skill with GitHub CLI:

```powershell
gh skill install Adenine-AGCT/skill-guardian
```

Once installed, use it when you want a safety-first review of local skills instead of blindly updating them.

## What This Skill Does

After installation, Skill Guardian can help you:

- discover local skills across supported agent roots
- identify which skills have known upstream provenance
- compare local versions with newer GitHub versions when available
- flag risky changes such as script edits, executables, or unknown sources
- turn audit results into a clear update decision

## What You Can Expect

A typical run ends with a concise decision summary such as:

- `No action`: nothing needs to be updated right now
- `Safe to update`: low-risk changes are available
- `Review required`: one or more updates need human review first
- `Blocked`: a change looks risky enough that it should not be applied automatically

Each per-skill report also includes:

- `trust_score`
- `risk_level`
- `update_recommendation`
- `confidence_explainer`

## Typical Use Cases

- You have multiple local skills and want a quick inventory before cleaning them up.
- You want to know whether an installed skill came from a known GitHub source or is locally modified.
- You want an update recommendation that separates docs-only changes from risky script changes.
- You want to audit skills on a machine without automatically changing anything.

## Local CLI Backend

This repository also ships the CLI and Python backend that powers the published skill.

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

## Safety Model

Skill Guardian is intentionally conservative.

By default it:

- does not auto-update installed skills
- does not execute remote scripts while inspecting them
- treats changes under `scripts/` and executable additions as review-heavy
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
gh skill publish --tag v0.1.2
gh skill preview Adenine-AGCT/skill-guardian skill-guardian
```

After publishing, keep `v*` tags protected with a GitHub tag ruleset.
