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

## Synthetic A0 calibration

[`a0_calibration.py`](a0_calibration.py) is a separate, no-model entry for the
minimum synthetic A0 scope described below. It accepts
only its checked-in fictional fixtures and scripted responses. It never reads
an input archive, production configuration, credentials or existing model
outputs, and never constructs a provider SDK client. Its `run` is entirely
synthetic; the paid `memory_replay.py run` above is a different command.

```sh
uv run --frozen python experiments/a0_calibration.py doctor
uv run --frozen python experiments/a0_calibration.py run --output /tmp/new-a0-run
uv run --frozen python experiments/a0_calibration.py resume --output /tmp/new-a0-run
```

`doctor` is the default. It writes a detailed `doctor.json` with individual
assertions and observed refusals under a new external temporary root; `--output`
can select a fresh external root. `run` performs only the successful scripted
scenario. Three fixed external wakes share one isolation scope and the real A0
reader, base prompt, tool loop, oplog and storage. Each prefix is physically
appended to the branch archive; future dates/windows/names are not created.
Candidate `say` writes a verified branch chatlog echo without activation. Stable
origins remain readable after echoes and subsequent prefix appends. Every read
checks the private tree for symbolic links. The virtual clock is fixed at each
prefix in `Asia/Shanghai`; the process timezone, current event, history and all
swapped bindings are restored in `finally`. No production hint is evaluated.

[`a0_ledger.py`](a0_ledger.py) keeps one pilot/request ledger, with a durable
reservation before each fake transport attempt and settlement before any tool
is dispatched. Defaults are 3 wakes, 12 attempts, 4096 output tokens per request,
49152 cumulative output allowance and 5 CNY. The **synthetic** input context
limit is explicitly frozen at 262144 tokens, using full canonical UTF-8 request
bytes plus 1024 as the scripted interface's input upper bound. Input and output
limits are independent in this invented interface; this does not establish a
real model's tokenizer or context/output relationship. Complete requests are
saved under `requests/` for recomputation. Cached input is charged once; cost is
rounded upward to integer micro-CNY. These invented prices only calibrate the
accounting algorithm and are not provider quotes.

The manifest freezes fixture/schedule, code, base prompt/schema fingerprints,
model label, timezone, prices and limits. The root lock, ledger checksum and
complete-checkpoint file hashes reject concurrent writers, drift and corruption.
`resume` only verifies an already completed checkpoint and reports cumulative
attempts; it makes zero new attempts. Partial, stopped, unknown in-flight and
persistence-unknown states cannot continue or fork. This explicit refusal is
not a claim that partial runtime restoration is implemented. A directory-fsync
failure can leave the new ledger bytes in place: the persistence-unknown marker
still prohibits reopening. If persistence itself fails, stop evidence is best
effort and durability remains unknown.

The doctor covers legal past through range/around/origin/unread and actual
reader/tool requests, cross-wake state and binding restoration, output/input/
allowance/fee boundaries, missing/extra prices, cache, missing usage and
usage overreport, corrupt/drifting/locked roots, pre-tool transport/length/
cancellation/ledger-fsync failures, and cancellation or oplog fsync failure
**after the first tool completes** in a two-tool batch. It also checks swallowed
chatlog-write failures and corrupt echo readback stop before arrival or later
actions. The twelfth fake failure causes no retry or final request. Failed or
missing-usage requests retain their full reservation; overreport records the
actual charge and closes the pilot. This proves only the new scripted adapter's
attempt count, not production SDK retry settings, real cost, provider support,
memory benefit, A1–A3 or the full comparison protocol.

## Synthetic A0 with the real SDK

[`a0_sdk_calibration.py`](a0_sdk_calibration.py) calibrates the installed OpenAI
Python SDK and the production `LLMClient` generator/parsers against a closed
`httpx.MockTransport`. Its only command runs built-in fictional fixtures:

```sh
uv run --frozen python experiments/a0_sdk_calibration.py doctor
uv run --frozen python experiments/a0_sdk_calibration.py doctor --output /tmp/new-a0-sdk-run
```

The constructor fixes a fictional key, organization, project, webhook secret and
`.invalid` base URL. It supplies the mock transport, `trust_env=False` and
`max_retries=0`; there is no client, transport, credential, configuration, model
or real-input option. SDK operations see a controlled empty environment rather
than ambient key/proxy settings. The doctor blocks sockets and QQ calls, and
blocks writes outside its fresh synthetic root (apart from SDK platform probes
of `/dev/null`). Existing roots are refused before rewriting their manifest or
ledger. This entry has no resume/fork operation; T01's completed-checkpoint
verification remains separate, and partial restoration is unsupported.

The HTTP send hook captures the SDK's final serialized body, including all
history, schema, `max_tokens=4096` and streaming usage options. It durably
reserves the request before the mock is entered and checks the exact bytes and
pilot/request ID there. Provider selection `deepseek/deepseek-flash` and wire
model `deepseek-flash` are recorded separately. The fictional peer exercises the
[official Chat Completions contract](https://api-docs.deepseek.com/api/create-chat-completion/)
with thinking enabled, high reasoning effort, automatic tools and no beta
strict/Search/Fetch/image features. Installed SDK/httpx versions, code hashes,
limits and protocol sources are frozen in each manifest.

An observer retains raw SDK usage before passing chunks to the original parser.
The last SSE chunk has both nonempty `choices` and usage. Length errors,
disconnects, cancellation and parser-generator close preserve usage already
received; missing or invalid usage consumes the full reservation. Conflicting
valid snapshots retain their raw evidence and the largest observed charge.
Cache aliases agree or fail closed, and reasoning tokens remain part of the
inclusive output count. Parser outputs are buffered until settlement succeeds,
so usage overreport or ledger-fsync failure cannot dispatch tools. Connection,
429 and 500 fixtures each enter the mock once; the thirteenth attempt is refused
before transport. These retry claims apply only to this closed adapter.

The doctor also drives actual A0 `take` and dry `say` tools over two external
wakes. After two consecutive tool rounds it checks the third final HTTP body
for both assistant reasoning strings and matched tool results; the second wake
checks retained earlier native rounds as well. A no-tool assistant reasoning
round is retained through rebuild. Actual A0 cancellation fixtures assert that
the active turn is cancelled, known/missing usage is settled, and no action is
dispatched. Bindings and current-event state are restored on exit.

**This contract calibration uses `persist_reasoning=True`.** T01 still uses
`False`; this argument is recorded in the manifest. In the current central A0
path, model-tagged native outputs retain reasoning even with `False` when the
reasoning mode is `keep`. The argument alone therefore does not identify a
history-projection strategy. Freeze the actual reasoning mode, model/protocol,
native pairing and wire projection before a real comparison with the
[official thinking contract](https://api-docs.deepseek.com/guides/thinking_mode/).
This experiment does not freeze that real baseline or change the future loop's
decision to discard reasoning across wakes.

The ledger remains `synthetic=True`. The offline
[peak-price snapshot](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
uses CNY per million input/cache/output tokens of 2/0.04/8. At 180000 input and
4096 output for each of 12 requests, the candidate ceiling is 4.713216 CNY;
T01's 262144 input ceiling would instead give 6.684672 CNY. Here the reserved
input bound is canonical HTTP-envelope UTF-8 bytes plus 1024, a fake-interface
policy rather than a real tokenizer proof. A separate fixture rejects an input
that fits its input reservation but exceeds the joint context after adding
4096 output. No result establishes real input bounds, paid readiness, provider
availability, memory benefit or completion of the comparison protocol.

## A0 pilot with declared capacity reservations

[`a0_pilot.py`](a0_pilot.py) adds a separate daily A0 pilot. Its default `doctor`
uses only a generated fictional prepared package, fictional identities and
`MockTransport`. T01 and T02 retain their closed defaults. The new
[`a0_capacity.py`](a0_capacity.py) policy plugs into the existing durable ledger;
it does not change either earlier experiment's byte reservation policy.

```sh
uv run --frozen python experiments/a0_pilot.py doctor
uv run --frozen python experiments/a0_pilot.py doctor --output /tmp/new-a0-pilot-doctor
uv run --frozen python experiments/a0_pilot.py contract-template > /private/pilot-contract.json
```

`contract-template` reads tracked code and prints a template; it does not query
`/models` or read materials/credentials. Its capacity evidence initially says
`documentation-example-not-live-model-query`, so the template is deliberately
insufficient for `run`. It freezes the current commit, tracked diff hash and
source hashes, including new experiment files before they are tracked. Generate
it against the final reviewed checkout. Before any future authorized run, fill
in the capacity/price sources, verification time and target applicability,
response model identity, failure-billing disclosure, and
`pilot_binding: {"id": "unique-pilot-id", "root": "/private/new-run", "record":
"/private/permanent-pilot-claim.json"}`. Live capacity status must be
`operator-declared-target-applicability`. This is an explicit operator declaration,
not evidence that this program verified a real model or account. The fixed
policy rejects different prices/capacity/model/parameters or unknown fees.

The future command is parseable, but **this implementation ticket did not
authorize or execute it**:

```sh
uv run --frozen python experiments/a0_pilot.py run \
  --prepared /private/authorized-prepared \
  --identity /private/authorized-identity.json \
  --contract /private/pilot-contract.json \
  --credential-file /private/isolated-api-key \
  --pilot-id unique-pilot-id \
  --pilot-record /private/permanent-pilot-claim.json \
  --output /private/new-run \
  --authorize-materials --confirm-paid
```

Both authorization flags and every input are required before material or
credential reads or client construction. The identity file is strict JSON:
`{"schema":1,"kind":"group","target":123,"bot":{"id":456,"name":"Bot"},
"bot_names":{"Bot":456}}`. Numbers here are examples. Historical Bot aliases
and accounts must be mapped explicitly. The credential file contains one literal
ASCII key, is owner-only (for example mode 0600), is at most 512 bytes and cannot
be a symlink. No environment key, production config or `.env` is read. The key,
its hash, credential path and authentication headers are absent from manifests
and reports. SDK operations see the same controlled environment mechanism as
T02, including organization/project/admin/webhook fallbacks. Live transport fixes
`https://api.deepseek.com`, disables environment proxies and redirects, and sets
SDK retries to zero. Neither entry accepts a custom endpoint or transport.

The stable claim file is created exclusively and fsynced, binding the frozen
contract to one pilot and output root. Existing claims or roots are refused;
changing root or claim path cannot reuse that contract's binding. Keep this
file and the private output after a stop. There is no resume, fork, segment
reset or automatic rerun. A deliberately changed authorization/claim constitutes
a new pilot; the application cannot prevent an operator from issuing new grants.

The minimum supported input is the schema-1 package produced by
`memory_replay.py prepare`: exactly `manifest.json` and ordinary group daily
files under `archive/2023-03/{14,15,16}.log`, with matching day/aggregate hashes
and event counts. Records must be LF terminated, have explicit sender IDs and
unique positive integer message IDs across the selected range. Private records,
notices, unparsed lines, absent IDs, ambiguous identity, duplicate IDs, sidecars,
extra files/dates and symlinks are refused rather than skipped. Group text without
a format marker is conservatively parsed as v0 stored display text; it is not
claimed to recover original OneBot bytes. Parse statistics record format,
missing/guessed fields and exclusions (zero for an accepted package).

Parsing and all three day-end clocks use `Asia/Shanghai`: March 15, 16 and 17 at
00:00. A day opens complete records in timestamp/file order, then activates one
external wake. The clock stays fixed during the real A0 drive. Original frozen
record byte ranges, full parsed records and normalized visible chatlog origins
are stored separately in the private source map. Range/around/origin/unread reads,
names and directories see only opened material and candidate echoes. Echoes are
`sent=false` branch records, never new wakes; old origins remain valid after
later appends. The same isolated history/oplog/storage survives all drives and
bindings/timezone are restored on exit. Checked-in Markdown Skills and their
source hashes replace T01's fictional Skill; host/code/network tools and real
sending remain unavailable to the model.

The manifest records actual `reasoning_mode=keep`, `keep_reasoning=true`,
`preserve_native=true`, `persist_reasoning=false`, model/protocol, base prompt,
tool schemas/capability fact, initial state, Skills, SDK versions, schedule and
contract. The doctor checks reasoning and tool-result pairs in the actual final
wire bodies across drives. Existing reader cover/hiding rules still apply; this
is not a promise to retain every historical round. No old whole-archive reading
task is added.

The declared provider-context-cap policy reserves the full 1048576 context
capacity plus 4096 output at conservative peak CNY rates 2/0.04/8 per million:
**2,129,920 micro-CNY per attempt**. It does not count JSON bytes as real tokens.
The one pilot shares 5,000,000 micro-CNY, 12 attempts, 3 wakes and 49,152 output
allowances. Two full reservations fit; three do not. Successful small usage
releases the difference and can allow 12 requests. Two legal near-capacity
settlements also reject a third reservation. Usage exceeding the joint context
is recorded and charged before closure, even when its separate input/output
counts fit their individual ceilings. Request/settlement timestamps use the
real wall clock, separate from simulated 2023 time. This conservative conversion
is not a provider bill or an absolute guarantee against supplier overreporting.

The SDK's final serialized body is checked and durably reserved before each
transport attempt. Raw usage is saved before the original production parser
sees it. Missing, invalid or conflicting usage keeps the full reservation and
closes the pilot; valid usage on length/disconnect/cancel is charged before
stopping. No tools are released before normal durable settlement. Disk ledger
integrity and frozen code/contract/manifest/Skills are checked before another
request; a damaged ledger is preserved and poisoned rather than overwritten.
Stop reports distinguish local limits, protocol/usage failures and persistence
unknown states without copying SDK error bodies. The doctor exercises socket,
QQ and external-write sentinels, active A0 cancellation, failure/no-repair paths
and credential-error redaction. These synthetic probes prove only those paths;
they do not establish comprehensive safety of every real SDK/provider response,
service acceptance, actual billing, memory benefit or comparison-protocol
completion. Real material, credential and paid execution still require a
specific reviewed authorization and target-applicability evidence.
