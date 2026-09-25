# Offline memory replay: preparation and safety probe

This is a **prepare + doctor** slice, not a model replay. It never boots the Bot,
loads `.env`, connects to NapCat, or sends a QQ message. `run` is an explicit,
disabled entry point, including when `--confirm-paid` is present.

Run the synthetic safety probe with `python experiments/memory_replay.py doctor`
(or omit `doctor`). It prints an output directory outside the repository with a
synthetic `prepared/manifest.json`, `transcript.jsonl`, `usage.jsonl`, and isolated
`runtime/data/event_stream/`. The probe uses the production `chatlog.parse_log`,
`context.Mailbox`, and `oplog` for stable day/time/file-order reading, bounded
FIFO consumption, cover, and recall. Synthetic output/cover and `dry_run_say`
transcript rows are probe-only markers, **not**
an invocation of the production `say` tool. A temporary guard blocks and probes
the production send entry point; no listener or sender worker is started.
No model client is created; usage records zero
paid calls and zero tokens. `doctor --output /safe/private/path` keeps output at a
chosen location; otherwise it uses a new system temporary directory.

For an archive authorized by its maintainer, use:

```sh
python experiments/memory_replay.py prepare \
  --chatlog-root /path/to/chatlog --kind group --target "$AUTHORIZED_TARGET" \
  --from YYYY-MM-DD --to YYYY-MM-DD --output /safe/private/new-directory
```

`prepare` requires every daily `.log` file in the inclusive range, rejects
symlinked day files and existing output paths, and stores byte-exact snapshots
only under the chosen output directory. The output must be outside both the
repository and input archive. Its manifest contains dates, SHA-256 hashes,
event counts, fixed `deepseek/deepseek-flash` model, and a prompt-mode label;
it contains no target, account IDs, or message bodies. The label records the
intended baseline: production default prompt plus only the fact that this is
offline review of history already past, with no way to affect or reply to its
participants. It does **not** claim that this prompt has been instantiated.
Do not commit or share the output directory: snapshots and transcripts contain
chat content. No `config.json`, production storage, sidecar backfill, or
`v1_since` marker is read, so group bodies without a format marker are parsed
conservatively as v0 and a `.log` snapshot is not a complete NapCat history.

## Why `run` remains disabled

The production center cannot be called safely from this standalone directory:

- `mods/chat.py` builds the central provider from boot-populated globals,
  `context` turns, identity, and storage. A scoped construction path needs an
  isolated runtime root, explicit default prompt, replay clock/window, and
  fail-fast projection rather than production fallback behavior.
- `mods/tools/meta.py` implements `say` through `message.sendmsg` directly.
  Tool binding needs an explicit send sink that records intended sends and
  returns simulated results; otherwise even a frozen archive may reach QQ.
- `mods/llm/__init__.py` `Chat.chat()` converts exceptions into chat replies.
  Replay needs a fail-fast mode, a fixed model assertion before every request,
  and usage/output hooks so failures cannot be mistaken for completed replay.
- Oplog writes under `storage.root_path` and its numbered IDs use wall-clock
  days. Full replay needs a scoped runtime/clock injection while keeping
  existing FIFO, output/result, cover/recall, Skill and hint paths unchanged.

Until those points are available, implementing a shadow replay loop here would
test a different agent. `run --output ... --confirm-paid` therefore fails before
reading files, importing send modules, or calling a model.
