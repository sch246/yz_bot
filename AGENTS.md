# Repository guidance

This repository is a live personal QQ Bot connected to the device's NapCat instance. Treat ignored runtime files as production data, not test fixtures.

## Before changing anything

- Start at [README.md](README.md). User-visible behavior is authoritative in [docs/interaction-model.md](docs/interaction-model.md); current implementation structure is in [docs/architecture.md](docs/architecture.md); deployment and test hazards are in [docs/runtime.md](docs/runtime.md).
- For rewrites, module redesigns, or architecture proposals, read [docs/design-principles.md](docs/design-principles.md) before proposing a target structure. First distinguish user-visible invariants, proven small mechanisms, historical accidents, and unresolved choices. Do not infer that “rewrite from scratch” means adopting conventional layers, DTOs, managers, or class-based framework primitives.
- Documents under `docs/working/proposals/` are unimplemented proposals, not current contracts. `docs/working/link-reactions.md` is a dated snapshot, not live authority.
- Preserve unrelated working-tree changes. Never assume existing modifications are disposable.

## Production and privacy boundaries

- Do not inspect, modify, delete, migrate, or commit `data/`, `chatlog/`, `log/`, `app.log*`, `config.json`, `.env*`, key files, model outputs, local virtual environments, or other ignored runtime state unless the task explicitly requires that exact data. `log/llm.log` carries chat bodies, prompts and tool arguments, and every stream file carries the interaction ids of the chats it served; treat them exactly as `chatlog/`.
- Never copy account IDs, group IDs, chat content, credentials, private endpoints, or machine-specific secrets into documentation, scratch scripts, logs, or responses.
- Do not POST synthetic events to the running `5701` listener, change `.post` routing, send QQ messages, restart the Bot, or exercise host-control commands unless explicitly authorized.

## Runtime hazards

- Do not run `main.py`, and do not call `mods.boot()`, against this checkout. That is the step which binds the HTTP listener, starts scheduler/worker state, loads real storage, and registers exit-time writes.
- Plain `import mods` is inert and safe: it only exposes the lifecycle helpers. Importing individual modules to call their pure functions is a legitimate way to check something.
- Load order is behavior, but it is declared, not incidental: `PHASE` plus `LOAD_AFTER` / `LOAD_BEFORE` decide when each `on_load` runs, and modules reverse-import names from a partially initialized `mods` package. Do not reorder module-level imports as formatting cleanup, and do not "tidy" those declarations.
- Commands, `.py`, link actions, shell, file operations, and ops share a host-level trust domain. A missing permission check exposes the capability by default.

## Validation

- This repository deliberately has no test suite, and adding one is not a default part of any task. A checked-in test freezes wording and shapes that are meant to keep moving, and then wins arguments against the source it was supposed to serve; the maintainer would rather read the code. If a change genuinely needs proof, write a throwaway script outside the repository (a differential run of old versus new behavior is usually the strongest one) and report what it showed. Propose a permanent test only when the maintainer asks for one.
- `uv run --frozen python run.py --check` compiles every `mods` source and re-reads the module-name rule from `mods.module_names()`. Run it after any Python change.
- `uv run --frozen python run.py --smoke` boots the whole application in a temporary runtime root against an in-process fake OneBot, then exits. It is the end-to-end check and touches no real data, ports, or account. Compare its `available/ctx` count and optional-failure list against the same command before your change; some modules fail to load on machines without the device's Minecraft or screen setup, and that is not your regression.
- Do not start a second real instance against the same data, logs, ports, or NapCat account.
- For documentation changes, run `git diff --check` and verify all relative Markdown links. Report current behavior separately from intended behavior when they differ.
