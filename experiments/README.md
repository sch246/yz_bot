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
production oplog FIFO without a model or sender. It prints its temporary
output path; `doctor --output /private/new-directory` selects one explicitly.
It requires a fresh process without Bot or storage background workers.

`run` uses the production center reader, context assembly, tool binding,
ordered unread set, oplog, cover/recall, hint, and default base prompt. It adds one common replay
task: formally read the frozen unread set in bounded pieces and organize it, while
leaving the choice of piece size and organization to the model. It also states
that the history has already happened and cannot be answered or changed. This
task is shared by every strategy arm; it is not a memory-management strategy.
The model is fixed at `deepseek/deepseek-flash`; a changed model, provider
failure, unreadable context, invalid tool call, or send failure stops the run
rather than becoming a successful chat reply. It requires an explicit two-field
JSON LLM configuration file outside this repository:

`--strategy baseline` adds nothing beyond that common task. The currently
available comparison arm, `--strategy progressive-index`, explains that `status`
only orients while `mentions` and `read_messages` arrange formal reading. It
also asks that accumulated cover conclusions be organized, and that repeated
indexing serve distinct future lookup purposes.
`--strategy bounded-hierarchy` additionally asks for 20-to-40-event takes and
for each batch to be organized before another is read, then folds several
same-level conclusions into a higher node.
`--strategy grounded-hierarchy` narrows takes to 16-to-24 events and explicitly
forbids guessing IDs from numeric continuity; bounded review uses the range
parameters of `recall_events` directly.
`--strategy grounded-loop` uses the same grounded IDs but gives each round a
short priority order: cover new input, otherwise fold several summaries,
otherwise take 20 events. It explicitly avoids replaying the whole history in
reasoning.
`--strategy serial-loop` additionally permits only one core action per output,
so a leaf cover cannot immediately take another batch and starve parent folding.
`--strategy serial-loop-reasoning` uses the same instruction but also persists
completed reasoning with its output until that output is covered. This flag is
isolated to the experiment and is recorded in `run_manifest.json`; it does not
change production reasoning storage.
`--strategy serial-loop-hint` keeps reasoning ephemeral and instead asks the
model to put only unresolved cross-request decisions in the replaceable hint;
derivable unread or graph state must not be copied there.
`--strategy serial-loop-checkpoint` makes that behavior observable: it requires
one initial hint checkpoint, then updates the hint only when the next unresolved
action changes and caps it at 200 characters.
`--strategy serial-loop-linked` changes a different variable: every retained
claim in a cover conclusion must cite the direct member event that supports it.
Leaf summaries cite input IDs; parent summaries cite child-summary output IDs.
This tests whether semantic next-hop labels reduce blind recall-tree enumeration
without retaining full reasoning or adding a separate index object.
The selected strategy and its text hash are recorded in `run_manifest.json`;
`resume` refuses a different strategy.

```json
{"base_url":"https://your-authorized-provider.example","api_key":"YOUR_KEY"}
```

Values are literal; no `.env`, production `config.json`, or production storage
is loaded. `run`, `resume`, and `doctor` require a fresh process with no Bot listener,
sender, LLM client, or storage background worker/observer; they refuse to swap
storage bindings in a partially started Bot. Put the configuration in a private
file with restrictive permissions.
`run` requires explicit paid-call confirmation and all three positive budgets:

```sh
python experiments/memory_replay.py run \
  --prepared /private/prepared --output /private/new-run \
  --kind group --target "$TARGET" --bot-id "$BOT_ID" --bot-name "$BOT_NAME" \
  --llm-config /private/replay-llm.json --confirm-paid \
  --max-calls 20 --max-prompt-tokens 200000 --max-completion-tokens 20000 \
  --max-output-tokens-per-call 12000
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
  --max-calls 20 --max-prompt-tokens 200000 --max-completion-tokens 20000 \
  --max-output-tokens-per-call 12000
```

The prompt budget uses the UTF-8 byte size of the outgoing request plus a
protocol margin as a conservative pre-request bound. The completion budget is
the whole segment's allowance; `--max-output-tokens-per-call` independently
caps one DeepSeek response. If API usage is missing, that request's reserved
allowance is charged before another request. `usage.jsonl` prefers
API usage when present and labels each segment. Budget, connection, and
incomplete-response stops create a resumable checkpoint only after durable
model actions are settled; other failures leave no new checkpoint and require
inspection rather than blind retry. `segment-NNNN.json` records each stop reason
and incremental usage; transcript and usage are appended, never overwritten.

The run creates its own `archive/`, `data/storage/`, `data/event_stream/`, and
`skills/` under the new output directory, leaving `prepared/` unchanged. Archive
events enter the real oplog in day/time/file order, start unread, and receive
formal IDs on reading. An arranged take without a next model request remains
unconsumed; it creates no durable input fact and is not part of a checkpoint.
Model outputs and tool results enter the same oplog; `say` records an intention
and a simulated echo with a synthetic ID, never calls the QQ sender. No listener,
NapCat client, Bot boot, or real model call is involved in `doctor`; only `run`
can call the explicitly configured model.

For safety, the model sees only historical archive/unread members, cover/recall, Skill
list/load/reload, hint editing, and dry `say`. Skills are copied from checked-in
Markdown files into the run directory. Skill *writing* is unavailable in this
baseline because production uses unrestricted host/file/code capabilities for
it; such an intention remains blocked rather than silently gaining a new tool.
Python tools, shell, browser, commands, remote history, image/network tools,
and arbitrary file access are unavailable. This is an environmental difference,
not a second implementation of memory rules.

These commands are currently a diagnostic replay substrate, not yet a complete
self-practice loop. They can preserve, resume, and fork an experience, but they
cannot yet let the model stage a candidate Skill from one failed run and make a
later treatment branch load that exact runtime-produced artifact. Adding that
restricted, versioned, reversible path is the next experiment; it must not be
implemented by exposing general production file or host access.

`resume` verifies the prepared manifest, every isolated file hash, model and
prompt mode, and a salted fingerprint of the supplied window/Bot identity
before touching runtime state. The manifest contains no plaintext account or
window IDs or chat content. A process lock prevents simultaneous writers.
The production oplog rebuilds unread members, formal IDs, cover and tool results;
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
