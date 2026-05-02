---
name: skill-guardian
description: Audit local agent skill directories, explain current version provenance, compare known GitHub upstreams, score trust and update risk, and finish with a clear recommendation about whether any local skills should be updated now.
license: MIT
---

# Skill Guardian

## Overview

Use this skill when the user wants a safety-first review of installed Agent Skills instead of blindly updating them.

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
