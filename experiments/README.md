# Offline memory replay

`memory_replay.py` has five commands: `prepare`, `doctor`, `run`, `resume`, and
`fork`. Never use a production runtime directory as an output. Outputs contain
chat bodies and model prompts; keep them private and do not commit them.

`prepare` freezes an explicitly selected range of daily `.log` files, checks
their parsing, and records byte hashes and event counts. It does not read
backfill sidecars or a production `v1_since` marker; unmarked group bodies are
parsed conservatively as v0. The manifest intentionally omits account and
window IDs, so `run` requires those facts again.
For old private records without a sender ID, supply the Bot's actual display
name; other historical Bot names cannot be inferred from the frozen range.

```sh
python experiments/memory_replay.py prepare \
  --chatlog-root /authorized/chatlog --kind group --target "$TARGET" \
  --from YYYY-MM-DD --to YYYY-MM-DD --output /private/prepared
```

`doctor` (also the default command) creates synthetic records and probes the
production Mailbox and oplog without a model or sender. It prints its temporary
output path; `doctor --output /private/new-directory` selects one explicitly.

`run` uses the production center reader, context assembly, tool binding, FIFO,
oplog, cover/recall, hint, and default base prompt. It adds only the fact that
the model is reviewing already-past history and cannot affect or reply to its
participants. The model is fixed at `deepseek/deepseek-flash`; a changed model,
provider failure, unreadable context, invalid tool call, or send failure stops
the run rather than becoming a successful chat reply. It requires an explicit
two-field JSON LLM configuration file outside this repository:

```json
{"base_url":"https://your-authorized-provider.example","api_key":"YOUR_KEY"}
```

Values are literal; no `.env`, production `config.json`, or production storage
is loaded. Put the configuration in a private file with restrictive permissions.
`run` requires explicit paid-call confirmation and all three positive budgets:

```sh
python experiments/memory_replay.py run \
  --prepared /private/prepared --output /private/new-run \
  --kind group --target "$TARGET" --bot-id "$BOT_ID" --bot-name "$BOT_NAME" \
  --llm-config /private/replay-llm.json --confirm-paid \
  --max-calls 20 --max-prompt-tokens 200000 --max-completion-tokens 20000
```

Each `run` or `resume` invocation has its own three budgets. To continue a
stopped run, pass the same prepared snapshot, window and Bot identity, literal
configuration, paid-call confirmation, and fresh budgets, but use `resume` and
the existing output directory:

```sh
python experiments/memory_replay.py resume \
  --prepared /private/prepared --output /private/existing-run \
  --kind group --target "$TARGET" --bot-id "$BOT_ID" --bot-name "$BOT_NAME" \
  --llm-config /private/replay-llm.json --confirm-paid \
  --max-calls 20 --max-prompt-tokens 200000 --max-completion-tokens 20000
```

The prompt budget uses the UTF-8 byte size of the outgoing request plus a
protocol margin as a conservative pre-request bound. The completion budget is
sent to DeepSeek as
`max_tokens` on each request; if API usage is missing, the whole reserved
completion allowance is charged before another request. `usage.jsonl` prefers
API usage when present and labels each segment. Budget, connection, and
incomplete-response stops create a resumable checkpoint only after durable
model actions are settled; other failures leave no new checkpoint and require
inspection rather than blind retry. `segment-NNNN.json` records each stop reason
and incremental usage; transcript and usage are appended, never overwritten.

The run creates its own `archive/`, `data/storage/`, `data/event_stream/`, and
`skills/` under the new output directory, leaving `prepared/` unchanged. Archive
events enter the real Mailbox in day/time/file order, start unread, and receive
formal IDs on reading. The production first-read floor is disabled only within
this offline scope so the beginning of the selected range is not skipped.
Model outputs and tool results enter the same oplog; `say` records an intention
and a simulated echo with a synthetic ID, never calls the QQ sender. No listener,
NapCat client, Bot boot, or real model call is involved in `doctor`; only `run`
can call the explicitly configured model.

For safety, the model sees only historical peek/FIFO, cover/recall, Skill
list/load/reload, hint editing, and dry `say`. Skills are copied from checked-in
Markdown files into the run directory. Skill *writing* is unavailable in this
baseline because production uses unrestricted host/file/code capabilities for
it; such an intention remains blocked rather than silently gaining a new tool.
Python tools, shell, browser, commands, remote history, image/network tools,
and arbitrary file access are unavailable. This is an environmental difference,
not a second implementation of memory rules.

`resume` verifies the prepared manifest, every isolated file hash, model and
prompt mode, and a salted fingerprint of the supplied window/Bot identity
before touching runtime state. The manifest contains no plaintext account or
window IDs or chat content. A process lock prevents simultaneous writers.
The production oplog rebuilds unread FIFO, formal IDs, cover and tool results;
isolated storage restores hint and active Skills. A killed process or tampered
run is not a safe checkpoint and is refused.

To compare independent continuations from one stopped checkpoint:

```sh
python experiments/memory_replay.py fork \
  --source /private/existing-run --output /private/new-branch
```

The new directory must not exist and must be outside the repository. `fork`
copies and verifies all files, writes a checkpoint-hash lineage record, and
does not share writable event-stream or storage files with its source. Resume
each branch separately with the same prepared snapshot and identity.
