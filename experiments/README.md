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

For material preparation without a model, use the separate `preview` command:

```sh
uv run --frozen python experiments/a0_pilot.py preview \
  --prepared /private/authorized-prepared \
  --identity /private/authorized-identity.json \
  --output /private/new-material-preview --authorize-materials
```

Authorization and all three arguments are checked before path resolution or
material reads. Preview loads and verifies the selected package, checks complete
record byte coverage and counts the existing A0 filter's visible records. It
constructs no SDK/client, prompt, runtime, tool or model request, and needs no
credential or paid confirmation. Stdout contains only format labels and counts;
the fresh mode-0700 output holds `preview.json`, the original byte-for-byte
`source-manifest.json`, and a private `source-map.json` containing full original
records. These artifacts are private material, not files to commit. An existing
preview root is refused.

Supported input remains the schema-1 package produced by `memory_replay.py
prepare`: exactly `manifest.json` and ordinary group daily files under
`archive/2023-03/{14,15,16}.log`, with matching day/aggregate hashes and event
counts. Accepted source `prompt_mode` values are
`production-default-plus-offline-history-fact`,
`production-default-plus-offline-review-task-v2`, and
`production-default-plus-offline-review-task-v3`. That label records the package's
history; the source manifest is never rewritten, and its old task is never
injected into A0. A run separately freezes its actual base prompt, capability
fact and reasoning projection.

Ordinary messages retain explicit sender IDs and signed integer message IDs,
including repeated IDs, or retain a missing-ID marker without inventing a
number. Recognized archive recall/poke and timestamped opaque notices are also
retained. Message IDs are auxiliary query keys: duplicate ID/time records can
be hidden by the existing range index, and ID-only queries can be ambiguous;
each complete original record remains readable through its stable origin.
Statistics distinguish extra repeated-ID instances, repeated-ID groups, and
ID-plus-time groups/conflicts. Recall reference numbers are excluded from these
message-identity counts. No source record is deduplicated or renumbered.

Records must end in LF. Byte offsets and origin line numbers count original LF
bytes; input is never normalized with `read_text` or `splitlines`. The current
pure parser preserves interior CR/CRLF in bodies and strips trailing whitespace
under the chatlog protocol. A CRLF message header makes its numeric ID a string
ending in CR under that parser; this representation is retained unchanged and
counted separately. Use origin to locate it rather than stripping its field to
an integer. Other noninteger message IDs, unparsed or untimed records,
ambiguous/missing senders, private records, sidecars, extra files/dates, unknown
source modes and symlinks are refused. Group bodies remain conservatively
marked v0 stored display projections, without claiming original OneBot bytes.

The current A0 filter is reused unchanged: ordinary messages and group pokes
can enter the model, while `#` controls and opaque notices are filtered. Parsed
archive recalls contain only their text, time, window and reference number;
they lack the native notice/author/operator fields required by the current
model filter, so they too are filtered. Their original records remain available
to bottom-level `chatlog.read_origin`/range reads; even the model's
`read_messages(origin)` applies the same filter and does not expose a raw-text
escape hatch. No native recall fields or realtime trigger are reconstructed.
Pokes use their parsed IDs and the frozen current name mapping when rendered;
this is not a promise to reproduce their historical notice text word for word.

Parsing and all three day-end clocks use `Asia/Shanghai`: March 15, 16 and 17 at
00:00. A day opens complete records in timestamp/file order, then activates one
external wake. The clock stays fixed during the real A0 drive. Original frozen
record byte ranges, full parsed records and branch chatlog origins
are stored separately in the private source map. Range/around/origin/unread reads,
names and directories see only opened material and candidate echoes. Echoes are
`sent=false` branch records, never new wakes. Their IDs decrease below every
message/reference ID in the selected package, avoiding both historical and
future collisions. Old origins remain valid after later appends. Source bytes
are appended unchanged to the branch archive, including CRLF and incomplete
notice fields. All mixed branch-day records are conservatively parsed as v0,
including candidate echoes whose known generated bytes are also saved. The
same isolated history/oplog/storage survives all drives and
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

The sentinels cover the doctor isolation scope. Before entering that scope,
both CLI output modes initialize the standard-library temporary directory for
fixture setup; its directory probe is not a model attempt.

Experiment wrappers preserve the original callable signature, annotations and
parameter documentation through session binding and rebinding. The Skill-only
and simulated-say capability descriptions also survive Tool reconstruction.
The pilot doctor compares every safe tool's final serialized HTTP parameters
against a separately loaded, unwrapped production meta source. A separate fake
peer generates status/take/load/reload arguments from those wire types and
required fields, executes them through the SDK/parser and A0, and checks durable
results and the next request's consumed input. Removing metadata from either
wrapper is checked to fail this comparison. Earlier scripted or SDK pair checks
alone did not establish this parameter contract.

Pilot failures create private `exception.json` with exception types, cause or
context links and stack frames (tracked repository source path, line, function;
other paths become `<external>`). It excludes exception messages, notes, source
lines and locals. The public result only labels evidence as saved or failed to
write. The first failure remains the stop reason if cleanup also fails; evidence
write failure never permits another action or request and does not undo settled
usage. These diagnostics cannot recover the stack from an older run.
Failure to write the final result also closes the ledger and attempts private
structural evidence, then exits without retrying the result write.

`result.json` now records real system wall timestamps and `perf_counter`
durations for source verification, day opening, before/after view checks, reader
drives, requests and the total run. Failed stages retain their partial duration.
Request intervals are nested inside reader intervals; completed-wake intervals
include verification, opening, checks and reader, so these views must not be
summed. Total time runs from `run_pilot` entry through cleanup, before final
result serialization/write; it excludes earlier CLI material loading. The
simulated 2023 clock remains fixed for reader behavior. Synthetic timings show
instrumentation, not faster startup on real material; no source or view proof
has been skipped.

## Synthetic comparison seams

[`comparison_snapshot.py`](comparison_snapshot.py) provides `freeze_sources`,
`open_snapshot`, `resolve_ref` and `bind_a0_view` for invented, in-memory group
day bytes. It freezes source coordinates and identity, opens records by an
explicit availability sequence, and binds the existing A0 archive readers to a
fresh directory outside every registered worktree. References require the
snapshot and source alias; ambiguous message IDs are refused. Native range
deduplication remains unchanged, while exact origins retain every record.
Declared v0/v1 bytes, multiline messages, CRLF message quirks and supported
notices retain the original parser's semantics; unsupported formats fail.
Quoted or control-character paths in Git worktree metadata are refused.

The snapshot object includes unopened control data and must stay outside model
input. Only the bound archive and names form the legal view. The serial scope
checks source, directory and identity drift and restores bindings on exit. It
supports archive reading, without a model drive, arrivals, echoes, resume or
note/Skill writes.

[`comparison_budget.py`](comparison_budget.py) provides `project_costs` and
`admit_request` over complete synthetic `Ledger` slices, request attribution,
explicit checkpoint ancestry and fixture limits. It counts each actual attempt
once for research spend, includes ancestors in each path, preserves unknown
usage reservations, and checks shared and configured scope limits with
`S + I + R`. Frozen attribution fingerprints detect relabelled old requests.
Only the default synthetic Ledger price and input-bound policy is supported.

The caller must supply a trusted frozen policy and freshly captured slices;
hashes establish consistency, not authenticity or freshness. Every decision has
`send_authorized=False`. The original Ledger owns durable reserve/settle; this
module adds no persistent ledger, HTTP guard or concurrent budget allocator.
These are library interfaces without a CLI. Neither seam establishes a complete
four-arm comparator or memory benefit.

## Synthetic fresh-context loop

[`stateless_loop.py`](stateless_loop.py) and
[`stateless_calibration.py`](stateless_calibration.py) implement the first new-loop
slice over the synthetic T07 snapshot and T08 accounting interfaces. They do not
change the production reader or adapt A0 arrival/formal-reading semantics.

```sh
uv run --frozen python experiments/stateless_calibration.py doctor --output /tmp/new-stateless-doctor
uv run --frozen python experiments/stateless_calibration.py run --output /tmp/new-stateless-session
uv run --frozen python experiments/stateless_calibration.py recover --output /tmp/new-stateless-session
```

`doctor` runs built-in fictional scenarios. `run` runs the two-wake fixture;
`--scenario` selects one built-in failure scenario (see `--help`). Failed runs
exit 1 and retain their original evidence. Both require a new output outside all
registered Git worktrees and reject symlink ancestry and existing roots. Creating
the directory is exclusive, so concurrent callers cannot share a run. There is
no key, real-material, endpoint, transport, model, paid, resume or fork option.
`recover` only reads this synthetic commit format; it sends no request and
executes no tool. Existing failed roots are evidence, not restart checkpoints.

`run_synthetic_session(frozen_sources, schedule, wakes, "synthetic-default",
output, scenario="normal")` accepts invented in-memory T07 sources and an
explicit availability table. The fixture has two group windows, equal-second
records, identical display names with distinct authors, duplicate message IDs,
a late old-time record, truncation and unopened body/name/directory markers.
T07 currently supports ordinary group sources only: this slice does not prove
private-chat identity behavior. Nothing is sent to QQ and no candidate echo is
created.

`build_screen(snapshot, wake, count=2)` selects recent records from the awakened
window, orders by occurrence time and stable event key, and returns exact
original bytes decoded as UTF-8, date, author, source reference, snapshot hash,
truncation count and a frozen boundary. The count is a fixture setting. Its
new/old label is approximate: late, omitted and noncontinuous references are
not an exact seen set. `drive_wake(..., references=(), actions=())` resolves
explicit extra references from the same snapshot; `actions` contains committed
output IDs, which are looked up in the action log. Each selected fact separates
`generated_assistant` from per-action execution status and committed results,
with its wake terminal/cutoff when present. Generated plans therefore remain
distinct from completed, failed, unknown and undispatched actions. The second
synthetic wake explicitly consumes these sourced facts. It cannot inject arbitrary
historical messages through that argument.

`run_synthetic_session(..., materials_by_wake={wake_id: [...]})` and
`drive_wake(..., materials=())` also accept explicit frozen material values with
exactly `kind`, `source_id`, `version_id`, `snapshot_id`, `sha256` and `raw`
(bytes). `kind` is `note`, `note_baseline` or `selector_inference`. A note baseline is
harness file-state evidence, including absence/tombstones; it is not note content
or a model inference. The loop copies the values,
checks their byte hash, strict UTF-8, current snapshot and nonempty source/version
labels, then puts exact decoded text in `selected.materials`. Selector inference
is labelled `inference-not-evidence` in the material and in the stable instruction;
it is not promoted to source evidence. Paths, arbitrary messages and extra fields
are refused. The producing selector/note slice must establish directory, version,
scope and source-cutoff legality; this loop does not own those stores. The
built-in `materials` scenario checks exact note and inference bytes in both actual
first-request wire bodies. Every final body still passes the same common guard.

Each invocation constructs a fresh
`Chat` and messages list. Only explicitly selected committed facts cross wakes;
reasoning and native tool rounds remain local to the current Chat.

The closed client uses the installed OpenAI SDK and the production nonstream
parser with `httpx.MockTransport`, fictional credentials, `trust_env=False` and
zero SDK retries. SDK operations see an empty controlled environment instead of
host credential or proxy values. The final serialized HTTP body and immutable
role/task/run/branch/parent/stage/snapshot attribution are saved before transport.
Inside the serial request boundary the sender rereads the persistent Ledger,
rebuilds T08 costs, checks `S + I + R`, saves request evidence and invokes the
original Ledger reserve. T08 always returns `send_authorized=False`; an allowed
decision alone never reaches the mock. The mock verifies the durable reservation
and exact request bytes. Both wakes share one Ledger using its default synthetic
canonical-envelope-bytes-plus-1024 reservation policy, with no second writable
balance. This policy is not a real tokenizer bound or price quote.

Parser outputs are buffered until usage validation and durable settlement
succeed. Missing/conflicting usage retains the full reservation and closes the
run; overreported usage is charged and stops it. Length, connection, cancellation,
request-evidence and reserve/settle persistence failures release no tools.
A different role cannot bypass the shared stop. Within a wake, the next actual
wire retains native assistant reasoning and matching tool-call IDs/results.
The base fixture exposes a tool reading one complete snapshot-qualified original
record. An unopened reference fails, records a known refusal and stops the
following tool. Optional note tools and background Chats are described below.

The first complete output commit contains its frozen screen, boundary, wake
facts and acknowledgement together. It never rereads the current archive tail.
Later activations remain pending. Output commits also bind the settled request ID
and context hash; persisted assistant facts exclude reasoning. Each tool has a
durable dispatch record followed by its full result before the next tool can
start. A result-write failure leaves that action unknown and the next action
undispatched. Cancellation checks run before each tool; completed results remain
committed. Failure or cancellation closes the common request domain.

`recover_commits(root)` rebuilds boundaries, acknowledged wakes and action states
from hash-linked commits. The same record validator runs before writes and during
recovery, including generation, wake/snapshot identity and screen consistency.
`finish_wake(root, wake_id, status, request_ids, reason=None)` commits a separate
round cutoff with complete/cancelled/failed status and request IDs; first-output
acknowledgement does not mean the round is finished. `recover_commits` returns
these facts in `terminals`, and the read-only CLI prints the rebuilt state. If
unknown persistence prevents a terminal commit, `result.json` records
`terminal_failure` and recovery refuses the log. There is no independently
writable boundary file. Missing results mean unknown
when dispatch was committed, or not dispatched otherwise; recovery replays
neither state. Damaged/checksum-invalid, stale-generation, mismatched-subrequest
and residual-tail logs are refused. Dispatch or result records after their wake
terminal are also refused; this slice has only synchronous tools. Flush/file-fsync/directory-fsync injections
retain originals and mark persistence unknown. These probes do not simulate a
physical power failure or prove durability across every filesystem or device.

Each run retains `manifest.json`, actual `request-*.json` wire/attribution,
`observation-*.json` usage, the original `ledger.json`, `commits/`, and
`result.json` with hashes, sequence trace, costs, stops and recovery state.
`doctor.json` records scenario assertions and socket/QQ/external-write sentinels;
`parser-console.json` contains fictional parser output. Diagnostic wire bodies
can include same-wake reasoning; those files are never loaded as subsequent
context. The normal fixture makes four mock requests and settles 480 synthetic
tokens cumulatively. A separate two-attempt common ceiling refuses the second
wake before its mock request.

The base two-wake fixture establishes request isolation, accounting and commit
boundaries. The joint fixture below adds actual selector/background requests and
note revisions. Neither establishes real model quality, production scheduling,
provider acceptance, semantic memory benefit or complete D10 acceptance.


## Synthetic joint note loop

The same [`stateless_calibration.py`](stateless_calibration.py) entry can drive
native note tools and optional independent background Chats through the installed
SDK/parser and its closed MockTransport:

```sh
uv run --frozen python experiments/stateless_calibration.py joint-doctor --output /tmp/new-joint-doctor
uv run --frozen python experiments/stateless_calibration.py joint-run --output /tmp/new-joint-session
uv run --frozen python experiments/stateless_calibration.py joint-run --case main-wins --output /tmp/new-joint-race
```

These commands require a new external root. They accept only built-in fictional
fixtures: no real model, credentials, archive, endpoint or paid option. The older
`run` and `doctor` retain their two-wake reference fixture and failure checks.
`joint-doctor` additionally exercises active note revision, both stale-write
orders, same-batch source/parent refusal, background cancellation, usage/common
budget refusal and note/Ledger/output/result/terminal persistence unknown.
Failed runs retain their original evidence and exit 1.

`drive_wake(..., notes=None, note_keys=(), note_aliases=None, process="main",
after_output=None, cancelled=...)` deepens the existing loop. Passing the existing
[`NoteWriter`](stateless_notes.py) and an explicit key allowlist enables
`revise_note` and `read_note` alongside `read_reference`. Before generation,
keys without a supplied frozen note receive an exact `freeze_baseline` material;
held frozen content keeps its own parent. Alias mappings are frozen caller
scope bindings, not model authority. The fixture manifest records their hash.

`revise_note(key, body, reason, expectation, sources, claim="draft")` takes
Markdown and provenance choices from actual model tool arguments. Public source
coordinates are `record:<source_alias>:<event_key>`,
`material:<source_id>:<version_id>`, `action:<output_id>#<position>`,
`tool:<tool_call_id>` for a native result already in input, and
`generated:self` or `generated:<output_id>`. The harness maps only coordinates in
the generating output's admitted cutoff to T12 source identities. It does not
ask the model to guess hashes created after that output. Baselines cannot support
factual statements; generated expression cannot prove external success. A
`success` claim needs a completed action result actually present in the input.

The complete assistant output is committed before any tool dispatch. Each note
revision registers/adopts that exact output cutoff and uses T12's single
prepare/commit entry with its actual process, generator and exposed parent.
Known source/scope/stale refusals remain typed failed results that the Chat can
read and reconsider. A persistence failure closes the common session immediately.
Revision status, including an ancestor/no-op version, never becomes a current
note read. `read_note` returns exact `{"note_material": {...}}` bytes/hash/version,
or a harness baseline for absence/tombstone. Only a later actual request carrying
that native result establishes the new parent and content exposure. A preceding
tool in the same batch has not yet entered the generating request.

`process="background"` creates a separate Chat with new messages. An optional
`after_output(output_id, process)` caller hook can run that independent Chat
before the outer Chat's tools; the outer output is already durable. This is a
small synchronous composition seam, not a scheduler or concurrency claim.
Nested calls save/restore request attribution. Each actual Chat gets a host
`chat_id`; outputs record its first-output flag and process with request/context
hashes. The existing journal validates identity continuity, rejects reuse or
wake/snapshot/process drift, and allocates unique shared output IDs. Model text,
role names and provider call IDs do not establish Chat identity. Notes match
native history only within the generating Chat; ambiguous legacy history without
an identity is refused rather than assigned by equal text.

All requests and tools use the original shared Ledger and guard. Cancellation,
unknown persistence and exceptional tools close every role before another
request/tool; closing first checks disk integrity so damaged originals are not
overwritten by in-memory state. `finish_wake(..., client=client)` also closes that
same domain if terminal persistence fails. Read-only recovery does not construct
a client, replay a tool or repair an unknown result. Existing failed roots are
not resumable checkpoints.

The joint fixture has three finite wakes. A separately declared environment
changes its fictional gate rule from blue to red, then reports no further change.
The fake model emits the predeclared body/reason/source choices through actual
SDK responses; these are fixture choices, not model autonomy or self-assessment.
An optional selector chooses frozen A, a main Chat revises and explicitly reads
B before terminal, and later same-wake HTTP bodies consume exact B while selected
A stays unchanged. The next wake reconstructs from committed notes/actions with
new messages and leaves the note alone. That quiet wake makes one Chat request,
without a compulsory selector or background request. Race fixtures give both
processes their own request/output and reject the stale producer; only a fresh
read and output can rebase it.

Each joint run freezes one synthetic limit envelope (three wakes, sixteen
attempts, 4096 output each, 65536 cumulative allowance), shared across all roles;
it never resets or splits a balance by role. Original synthetic price/context
policy remains explicit and does not authorize paid use. Manifests freeze source,
schedule, environment, limits, mapping and code hashes. Results retain full role
request order, settlements/costs, outputs, native results, frozen A/new current
material, failures and read-only recovery. Fixture choices, serial interleaving,
cooperative locks and injected IO faults establish neither real semantic quality,
threaded throughput, physical power-loss safety nor production readiness.

Future simulated-action tools can reuse the local `execute` function in
`drive_wake`, which owns guard → dispatch → operation → result. Adding such a tool
requires ownership of that local function/registration seam in `stateless_loop.py`;
it must use the existing output/action journal. No second action log or general
tool scheduling framework is provided here.

## Finite subject workspace

[`subject_probe.py`](subject_probe.py) composes the existing native Chat driver,
NoteWriter and OfficialClient around a small fictional workspace. It offers two
objects, two text cards and three places. Actual move/write tools change
`world.json`; an independently specified clock can tilt a tray and slide its
objects to the table. No interest, preferred arrangement, plan, score or correct
reaction is supplied. The bootstrap, rules, seed, visible scope, source bytes and
budget are frozen before execution. These are disclosed design priors, not
claims about an unconditioned subject.

The application ceiling is 2,000,000 cumulative tokens, 5 CNY at the frozen
conservative prices, twelve actual HTTP requests, three wakes and 4096 output
tokens per request. Only the `chat` role in the `question` stage is enabled.
Stage and run ceilings overlap on the same original Ledger; they are not extra
balances. No selector or background call is mandatory. The unused stage fields
have minimal positive declarations because the existing capacity schema requires
all four stages; those roles are not enabled.

A bootstrap wake is followed by at most one separately labelled harness clock
opportunity. `request_revisit(delay_ticks, reason)` creates a further opportunity
only through an actual native request, dispatch, world persistence and confirmed
result. Delays are one to three simulated ticks; zero declines every remaining
opportunity. A quiet model requests no revisit and can therefore finish with two
wakes; explicit decline can finish with one. Three is a ceiling. A due request
can remain undelivered when that ceiling or the shared budget is reached.
The next-request shared reserve is checked before clock advancement, and the
existing HTTP hook repeats admission before reservation/send. These are finite
simulated ticks, not a production scheduler.

The four optional `drive_wake(..., simulation=workspace)` tools are
`observe_workspace()`, `move_object(object_id, destination)`,
`write_card(card_id, text)` and `request_revisit(delay_ticks, reason)`. They all
reuse the existing guard → output/dispatch → operation → result path. The world
reads its current state, frozen rules and actual dispatched arguments only;
it does not inspect a note, candidate, diagnostic expected answer or model
self-assessment to decide an effect. A fresh observation returns current state
and the last clock effect, never future state or pending opportunities.

Each later wake creates a fresh host-identified Chat from committed notes and
committed action facts. Only wakes actually delivered so far are passed to the
existing acknowledgement logic. The static bootstrap snapshot/cursor can be
reused without treating later note generations as unchanged. `revise_note` and
`read_note` retain their existing source/parent contracts, including
`tool:<call_id>` only after its native result appears in an actual request.
Rejected candidate arguments may remain visible as historical generated action
facts; they never become selected note material. Exact wire bodies establish
exposure. File existence or exposure alone does not establish use or benefit.

Preparation and inspection are offline and take no credential:

```sh
python experiments/subject_probe.py prepare --root /absolute/new/external/root
python experiments/subject_probe.py verify --root /absolute/new/external/root
python experiments/subject_probe.py recover --root /absolute/new/external/root
```

`prepare` freezes `bootstrap.txt`, `world-rules.json`, `subject.json`, `plan.json`
and `official-template.json`. It creates no Ledger, allocation, authorization or
client and reports `prepared-not-ready`. The default command is `verify`;
missing operator records report `not-ready`. `plan.json` binds the exact raw
`subject.json` bytes. Its entry is a bootstrap declaration; the subject driver,
not the official single-entry CLI, constructs the actual native requests.

For an explicitly approved real segment, an operator must independently provide
the four records required by [`stateless_official.py`](stateless_official.py):
`models-response.json`, `target-metadata.json`, `research-allocation.json` and
`authorization.json`. The existing official verifier is the schema authority.
Metadata must record an actual applicable `/models` observation with origin
`operator-observed-official`, exact response hash, target identity and capacity.
The common 100M-token allocation must contain one matching fresh reservation for
the prepared root/run/binding and full overlapping budgets. Authorization must
bind the template's `official.binding`, all four other record hashes (including
`plan.json`), that reservation ID, the explicit current contract and unknown
billing acceptance, with a valid interval of at most 24 hours. Preparation does
not invent any of those observations, reservations or approvals.

After those records have been supplied, `seal` only hashes them into the final
manifest and verifies them. It does not grant approval or repair invalid input.
A failed seal retains its manifest for inspection; do not reuse it as a fresh
execution root. Explicit execution takes one temporary credential line on stdin
through an operator-controlled channel:

```sh
python experiments/subject_probe.py seal --root /absolute/new/external/root
python experiments/subject_probe.py verify --root /absolute/new/external/root
python experiments/subject_probe.py run --root /absolute/new/external/root --credential-stdin
```

Never put a credential in command arguments, environment variables, run artifacts
or logs. An operator may temporarily supply stdin from an explicitly authorized
private credential source; using a private key file depends on that authorization.
The CLI accepts stdin only and never discovers credential files. The run command
checks readiness before reading stdin; prepare/verify/recover never read it. Each wake and each tool rechecks frozen source/material hashes.
The OfficialClient's permanent execute-once claim and common Ledger guards stay
in force. Cancellation, missing usage, budget refusal and unknown note, action,
external effect or result persistence close the shared session. Recovery is
read-only: no client, replay, retry or repair. An external effect can be known in
`world.json` while its action result is unknown; it must not be dispatched again.
A delivery recorded before the first output remains delivered-but-unacknowledged,
not proof that a model received it. World checksums and source binding detect
ordinary drift; they are not authentication against a trusted host rewriting
both evidence and hashes.

The offline composition calibration uses only the fixed internal OfficialClient
MockTransport path, real installed SDK/parser, fictional operator records and a
fictional credential. Public verification still refuses those records. It never
sends a model request or spends real money:

```sh
python experiments/subject_probe_calibration.py doctor --case active --output /tmp/new-subject-active
python experiments/subject_probe_calibration.py doctor --case quiet --output /tmp/new-subject-quiet
python experiments/subject_probe_calibration.py doctor --case revisit-unknown --output /tmp/new-subject-fault
```

Run cases separately when applying a per-command timeout. `doctor` without
`--case` runs every case and can take longer. Active fixtures emit scripted tool
arguments through actual fake SDK responses: move/write, inspect the real native
result, revise/read a note, request a revisit, then observe an independent clock
effect and revise. This proves wiring and refusal behavior, not autonomous
interests, semantic quality or a real model's decision. Cases also cover silence,
explicit rest, same-batch premature success refusal, cancellation, request and
wake-boundary budgets, missing usage, input drift and effect/result/note unknown.
Every request retains its original Ledger row, exact wire, attribution, usage,
transport evidence (`sent=False`) and action provenance. `result.json` separates
formation, selection, injection, use judgment and external results; it supplies
no success ranking. Independent external evidence readers can recompute the
world from the rules and actual tool arguments without importing the fixture or
subject implementation.
