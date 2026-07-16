# Repository guidance

This repository is a live personal QQ Bot connected to the device's NapCat instance. Treat ignored runtime files as production data, not test fixtures.

## Before changing anything

- Start at [README.md](README.md). User-visible behavior is authoritative in [docs/interaction-model.md](docs/interaction-model.md); current implementation structure is in [docs/architecture.md](docs/architecture.md); deployment and test hazards are in [docs/runtime.md](docs/runtime.md).
- For rewrites, module redesigns, or architecture proposals, read [docs/design-principles.md](docs/design-principles.md) before proposing a target structure. First distinguish user-visible invariants, proven small mechanisms, historical accidents, and unresolved choices. Do not infer that “rewrite from scratch” means adopting conventional layers, DTOs, managers, or class-based framework primitives.
- Documents under `docs/working/proposals/` are unimplemented proposals, not current contracts. `docs/working/link-reactions.md` is a dated snapshot, not live authority.
- Preserve unrelated working-tree changes. Never assume existing modifications are disposable.

## Production and privacy boundaries

- Do not inspect, modify, delete, migrate, or commit `data/`, `chatlog/`, `config.json`, `.env*`, key files, model outputs, local virtual environments, or other ignored runtime state unless the task explicitly requires that exact data.
- Never copy account IDs, group IDs, chat content, credentials, private endpoints, or machine-specific secrets into documentation, tests, logs, or responses.
- Do not POST synthetic events to the running `5701` listener, change `.post` routing, send QQ messages, restart the Bot, or exercise host-control commands unless explicitly authorized.

## Runtime hazards

- Do not import or run `_code/main.py` for a local unit test. Importing the runtime binds the HTTP listener, starts scheduler/worker state, loads real storage, and registers exit-time writes.
- `main.py` import order is behavior: commands and modules reverse-import names from a partially initialized `main`. Do not reorder imports as formatting cleanup.
- Commands, `.py`, link actions, shell, file operations, and ops share a host-level trust domain. A missing permission check exposes the capability by default.

## Validation

- The repository has no authoritative automated runtime test suite. Prefer read-only inspection and focused pure-function checks.
- Syntax-check changed Python files without importing the runtime. Do not start a second instance against the same data, logs, ports, or NapCat account.
- For documentation changes, run `git diff --check` and verify all relative Markdown links. Report current behavior separately from intended behavior when they differ.
