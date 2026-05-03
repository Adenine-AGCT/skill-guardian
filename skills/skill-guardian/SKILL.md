---
name: skill-guardian
description: Audit local agent skill directories, explain current version provenance, compare known GitHub upstreams, score trust and update risk, and finish with a clear recommendation about whether any local skills should be updated now.
license: MIT
---

# Skill Guardian Skill

## Overview

Use this skill when the user wants a dedicated skill for auditing installed Agent Skills before making update decisions.

Skill Guardian is built for skill audit and update governance. It inspects local skills, checks known upstream provenance, reviews update risk, and ends with a clear recommendation about what should be updated, reviewed, blocked, or left alone.

This skill is advisory. It does not auto-update installed skills.

## Workflow

Run the bundled wrapper:

```powershell
python .\scripts\skill_guardian.py
```

Use offline mode when network access is unavailable:

```powershell
python .\scripts\skill_guardian.py --offline
```

The wrapper performs:

1. Multi-root discovery for local skill directories
2. Local structural and behavior audit
3. Known-upstream resolution and remote comparison when possible
4. Trust scoring and update recommendation
5. A final human-readable summary of what should or should not be updated
6. An action-oriented conclusion that highlights whether the user should update now, review first, or take no action

## Output Contract

The final response should contain:

- how many local skills were found
- which roots were scanned
- which skills have recognized upstream provenance
- which skills are already current
- which skills have updates available
- which updates need review or should be blocked
- whether the user should update anything now

Every per-skill summary should include:

- `source_type`
- `version_status`
- `trust_score`
- `risk_level`
- `update_recommendation`
- `confidence_explainer`

## Safety Rules

- Never auto-update a skill
- Never execute remote scripts while inspecting them
- Treat changes under `scripts/` or executable file additions as review-heavy
- Treat unknown-source skills as auditable but not strongly version-resolvable
- If remote inspection fails, still complete the local audit and say that the remote version check was unavailable
