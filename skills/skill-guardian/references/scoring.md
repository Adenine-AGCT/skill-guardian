# Scoring

`skill-guardian` scores each skill across four dimensions:

- `source_confidence`
- `integrity_confidence`
- `behavior_confidence`
- `update_confidence`

Recommendations:

- `update`: low-risk remote diff, usually docs-only
- `review`: instruction, metadata, or meaningful behavior changes
- `block`: scripts, executables, destructive patterns, or blocked sources
- `skip`: no upstream, no change, or insufficient evidence
