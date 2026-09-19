# 柚子手记 · 从聊天记录里长出来的备忘

> **柚子怎么记的（给自己看的规矩）：**
> 1. 只从聊天记录里提取「以后还用得上」的东西，不预造概念、不套模板。
> 2. 每条尽量能追到证据：谁、什么时候、大概说了什么。
> 3. 结构是长出来的——某件事反复出现才单独立一节，否则宁可先堆在「待归类」。
> 4. 改起来不心疼：过时了就删，错了就改，不为了保持完整性硬留。
>
> 记录范围：私聊 / 群聊的观察。柚子自己的话不算数，别人说的才算证据。

## 一、人
- **草籽**（QQ 980001119）
  - 会讲带刺的故事点醒柚子（例：日本爆炸案 → 「为了合规而弃安全不顾」）。
  - 不喜欢「为合规而强加的格式和预设」，主张贴近真实。
  - 提出「鬼之道」：为了维护虚构僵化的概念而妄图扭曲现实。
  - 建议柚子：只看聊天记录的话，可以另开一个文件来维护文档。

## 二、正在进行的线（没说完的事）
- [x] **自维护上下文文档**：GPT 维护的版本被草籽认为格式/预设太重。
- [x] 柚子打算开一份更轻、更贴近真实对话的备忘来对照 —— **这份文件本身就是答案**（`docs/working/kouzi-memo.md`，22:27 草籽确认收掉）。
- [x] **`.chattop` 升级**（12:07 问、12:34 授权）：已改完。①键从裸月份改成 `YYYY-MM`（`chat.usage_name` 单一来源），根治多年同月叠加；②删掉 `chattop.run` 里跟 `chat._usage_entry` 抢 `last_call` 的清零 hack；③群口径维持「仅当前成员」并写进输出；④`int(raw_user)` 与异形值加保护；⑤文档同步（`commands.md`、`llm.md`）；⑥两处都补了 WHY。旧 usage 数据已按草籽指示清空（`storage.delete`）。
  - 踩坑：给 `chattop` 加 `from mods import chat` 后，它和 `chat` 同为 FEATURE 阶段、又没声明依赖边，`indegree=0` 排在 `chat` 前，`on_load` 时 `chat` 不在 `available` → 加载失败。修法是补 `LOAD_AFTER = ("chat",)`。**教训：同阶段的运行期依赖必须显式声明 `LOAD_AFTER`。**
- [x] **给 `exec_code` 加必填 `timeout`（0 = 不限）+ `^C` 承担"强制停止"**（草籽 20:30 认可："可行，需要加看门狗" / "可行；但「立即停止进程」与「怎么善后」是两码事"；22:08 开工，22:5x 落地）：
  - **新模块 `mods/watchdog.py`**（FEATURE，`LOAD_AFTER = ("context", "history")`）。登记一次工具执行期间**可以被强制终止**的东西，按**窗口**归属（`history.window(context.current())`），与 `^C` 的粒度一致：
    - **子进程**：`on_load` 里把 `subprocess.Popen.__init__` 包一层，工具执行期间 spawn 的子进程自动进表（这是"看门狗"这个名字的来处——`exec_code` 里写什么都有可能，只有接在 Popen 上才不用猜模型会怎么写）。**登记只在这次调用期间有效**，一结束就出表，所以工具起的常驻进程（REPL、MC 服务端）不会被后来的 `^C` 误杀。
    - **执行线程**：只有 `watchdog.run()` 起的子线程会登记。**调用工具的那个线程有意不登记**——对同步执行的工具半路注入异常，等于在它写文件写到一半时掀翻它，那正是草籽说的"善后"。
    - `stop(owner, reason)` = kill 全部子进程（阻塞的 `wait` 因此返回）+ 给登记过的线程注入 `Interrupted`。注入用 `ctypes.pythonapi.PyThreadState_SetAsyncExc`，`affected > 1` 时撤销（防误伤）。
  - **`exec_code`**：`timeout: float` 无默认值 → 进 schema 的 `required`（实测 `['expr','code','timeout']`）。代码改在 **`watchdog.run` 的子线程**里跑：**时限由调用方负责**，所以哪怕卡在打断不了的系统调用里，调用方也一定在时限内拿回控制权（代价是那个线程可能还留着）。超时/被 `^C` 命中时**保留已捕获的 print 输出**再附一行说明——那半截输出恰恰是最有价值的部分。实测：正常 `42`；`while True: pass` → 1.02s 返回「超时：…已中断执行线程」；带 `subprocess` 的 → 「已终止它启动的 1 个子进程」且子进程真的死了（`os.kill(pid,0)` → `ProcessLookupError`）。
  - **`llm.Chat.chat` 的工具循环**：每次调用前查 `should_stop`（卡住的那个后面不再照跑），并把这次调用包进 `watchdog.begin()/end()`。中断/放弃时**不做半轮修补**——这一轮的 `messages` 随轮次结束丢弃，每轮都从 history 重建。
  - **`bot._route` 的 `^C` 分支**：在 `context.cancel` + `context.cancel_turn` 之外加 `watchdog.stop(window, "用户 ^C")`。
  - **一处对旧结论的修正**：本条原文写「已排除：异步异常注入（卡在 waitpid 收不到）」——那只在"**只靠注入**"时成立。现在的分工是"先 kill 子进程让 `wait` 返回，再注入"，所以注入能落地；纯 Python 忙循环本来就只在字节码边界上，注入直接生效（实测 1.03s 打断）。「把工具挪进子进程会毁掉共享的 `py.loc`」仍然成立——挪的是**线程**，`py.loc` 照样共享。
  - **有意留下的限制（写进文档，别当 bug）**：注入只在线程回到字节码边界时才真正抛出，卡在 `time.sleep()` 或 kill 不掉的系统调用里时它是挂着的；卡在等待第一个 chunk 时 `^C` 仍然无效。`exec_code` 用"子线程 + 调用方计时"绕开了这一点，别的工具没有。
  - 文档：`docs/llm.md` 的 `exec_code` 表格行、`^C` 那节（新增两条 bullet：硬停止 + 登记表边界）、`mods/tools/meta.py` 的模块 docstring 与 `@param`。
  - **端到端实测（重启后，22:5x）**：①`mods.watchdog` 已加载、`subprocess.Popen` 挂钩为真、`exec_code` 的 schema `required` 确实是 `['expr','code','timeout']`；②`exec_code` 里 `while True: pass` → 1.01s 返回「超时：…已中断执行线程」，带 print 的半截输出也保留；③带 `subprocess` 的 → 「已终止它启动的 1 个子进程」，`os.kill(pid,0)` 报 `ProcessLookupError`（真死了）；④**走真路由的 ^C**：用 `op` 的事件构造器造一条 `^C`、从定时器 `connect._events.put` 投进去，主线程 `_route` 命中 ^C 分支 → `watchdog.stop(('private',999999999))` → 1.01s 后被我卡住的 `sleep 60` 返回 `-9`，`job.reason == "用户 ^C"`。测试用的是假窗口 `999999999`（有意避开本窗口，否则 ^C 会顺手取消我们这一轮），事后已清掉内存历史里那条、盘上没留文件。
  - **22:26 草籽对"三类卡死"的裁决（按此结案）**：
  - ①**全盘搜索卡死** → 已由上一条兜住（`host.run_command` 自带 60s kill；`exec_code` 里 spawn 的进看门狗）。
  - ②**LLM 调用卡死** → **视为已解决，不再加"总时限/空闲上限"**。理由（草籽原话）："有时就是要调用很久"。所以 client 的 `120s`（httpx read = 两次读之间的间隔，非总时限）**保持现状**，不引入整轮墙钟上限——免得把正常的长思考误杀。
  - ③**思维链无限重复** → **用 `^C` 解决即可，算已实现**，不做输出 cap / 墙钟上限 / 重复检测。依据：chunk 一直在流，逐 chunk 检查点跑得到，`^C` 能 `response.close()` 打断（即"人工能救"已够，不追求"自动救"）。
  - **给未来的自己**：当时核出的两条事实仍然成立，只是**决定不据此动手**——(a) LLM 请求不在任何 `watchdog` job 里，所以 `^C` 的 `watchdog.stop(window)` 对它返回 0；(b) `chat.py` 那条注释自承"卡在等第一个 chunk 时 ^C 无效"。若哪天"卡在等首字节"变成真事故，再回来动这两处。
- `--check` → `105 Python files, 82 public modules`；`--smoke` → 必需模块全过（可选的 `minecraft`/`mcf`/`py`/`later`/`link`/`todo` 在临时根下照旧失败，与本次改动无关）。

- [x] **`^C` 承担"强制停止"**：见上一条（与 `exec_code` 的 `timeout` 同一批落地）。草籽 12:10 的复现是起因：`cwd="/"` 的 grep 卡死后 `^C` 无效，只能 `.reboot`，而且卡住的工具会让**整个窗口失能**、后续消息只排队不发言。20:30 草籽定的方向正是最后用的那条——"登记工具期间 spawn 的子进程、停止时 kill 掉，并让 `pending_calls` 循环每次调用前查一次 `should_stop`"。
- [x] **op 工具集（草籽 12:39 提；19:03 草籽「开始实现」）**：只在 op 发起的聊天里可调用的工具模块，内含「异步给自己发消息（注入命令）」的能力。柚子认可方向；关键结论：注入**要用 `connect._events.put(event)` 走主线程的真实路由**，**不能用 `message.recvmsg`**——后者在调用者线程里同步跑 `bot.recv`，`.reboot`/`.shutdown` 的 `SystemExit(233)` 会打死那个线程而不是主线程（12:34 实测），而且慢命令会阻塞工具调用线程、让窗口失能。另一个坑：工具目录 `_render_context` 是**进程全局**的，而 op 门控是**按窗口**的，所以只能在每次 bind 时按「当轮触发者」过滤、并在执行时再校验一次，不能在 registry 层删模块。
- [x] **`#hint`：给窗口存一段自动执行的代码**（草籽 12:57 提；13:12 定为 `#` 控制面；13:22 定方向；13:34 定“本质是文本管理”；15:34 定合并模型）：设计稿在 `docs/working/proposals/chat-hint.md`，**等草籽审核**。**模型**：两份配置字典——全局默认 `storage.get("","hint")` + 本窗口 `getchatstorage()["hint"]`；**生效 = `{**default_hint, **chat_hint}` 显式合并**（窗口覆盖默认，只认 `code`/`on` 两键）；发不发 = `code` 非空 且 `on` 为 `True`；`on` 缺省 `False`；**允许“只有 `on` 没有 `code`”的窗口配置**（本窗口单独关默示）；**`#hint set` 写 `{"code":…,"on":True}`**。命令照 `#setting` 系列：`#hint`（切换**本窗口** `on`，键不存在就地创建，**不波及全局**；总有返回）/`get`（显示合并后生效的 code+on，标来源）/`set <多行源码>`/`set`（无参 = 显式回落全局默认）；默认级 `#hint default`（切换默认 `on`）/`default get`/`default set <多行源码>`；**无 `del`**。在 `chat.chat()` 的 `finally`（`end_turn` 之后）求值，仅当本次**持有该轮**（`not owner` 早退不算）；环境 `py.loc`，注入 `window`/`usage`（来自**本地估算**的 `chat.context_usage()`）；输出**带 `#` 前缀**防回流；异常**全吞+写日志**。**op 专属**（用户可写代码、跑特权环境、每次聊天自动执行，权限比只改提示文本的 `#prompt` 高一档）。
  - **13:34 定位**：`#hint` 本质就是**文本管理**（给窗口存一段 storage 文本 + 一个开关），跟 `#prompt` 一系；“它在聊天结束时被自动求值发送”是**另一回事**，别混进命令语义。
  - **13:22 四条**：①触发点 `finally` 对；②消耗用**本地估算**（A），不要 provider 真值（B）；③**同步 `sendmsg`** 即可，不走 `connect._events` 注入；④`chatend` 约定**不能用**——它是「用户说了才发」，不是循环结束的自动信号。
  - **`#help [name]` 照 `.help` 做**（13:34 草籽）：单 `#help` 列所有 `#` 子命令的**首行摘要**，`#help <name>` 显示**完整**说明；`_subcommand_help(prefix=None)`，`_SUBCOMMAND_HELP` 第二项升级为可多行、摘要取首行。**不做 op 过滤**——单 help 列出所有，非 op 执行 `#hint` 时才被拒。对齐 `mods/help.py`：无参 `f".{name} — {首行}"`、带参 `cleandoc(__doc__)`。
  - **14:01 八条拍板**：①**异常安全**（新增，必须）：hint 是用户写的代码、每次聊天自动跑，求值/发送的任何异常都吞掉+写日志，**绝不抛回 `finally`**；报错用 `#` 开头发（同 link `_report_error`）。②`_eval_last` **上升为 `py.eval_last`**（选 b），link 改调它——**要紧接着 reload `py` 和 `link`**。③toggle 只翻 flag、保留源码，**`#hint` 任何时候都要返回**；**全局默认要有**，两层存储（窗口覆盖优先 / `storage.get("","hint")` 全局默认），`#hint get` 用默认时括号标注「（默认）」。④非 op：**不接管 + 节流提醒**（不静默）。⑤单轮 / `.chat` 单句**不触发**。⑥`py` 必然随 bot 启动加载，不设「未加载」分支。⑦`set` 切分照原案。⑧不做 `#hint run`。**14:05 澄清**：草籽“显示覆盖”是“**显式覆盖**”的手误，含义取 **A**——`#hint set`（无参）= **显式让本窗口用全局默认**（清掉本窗口覆盖、回落默认）。改全局默认需要新命令 **`#hint default get/set/del`**（已加进稿子命令表）。
  - **14:08 两条修订**：①去掉 `#hint del` 与 `#hint default del`（多余——toggle 切到关=不生效，`set` 无参=清覆盖）；②`#hint default` 由「显示默认」改为**切换默认是否生效**（本质是一个全局 flag，运行时为 true 才启用），显示默认改用 `#hint default get`。稿子整篇重写（命令表/存储/待定/已定）。剩余待定：返回文案用词 · 全局默认开关缺省值 · 两 flag 合成规则 · `#hint default` 是否要额外确认。
  - **15:26 简化 → 15:34 定稿**：一个 hint = 代码+开关**捆一起的一个单元**（**不拆正交 flag**，我 v4 的两级正交 flag 模型被草籽判为太复杂）；`#hint default set` **不需要额外确认**。**15:34 合并模型**：两份字典 `{**default_hint, **chat_hint}` 显式合并、不搞花哨间接层；`set` 后 `on` 默认 `True`、缺 `on` 键按 `False`；toggle 相当于写 `chat_storage['hint']['on']`，**没有 hint 键就自动创建**，故**允许单个 `on` 的配置**、且**只写窗口级不波及全局**（解决了我先前担心的“翻生效那份会波及所有跟随默认的窗口”）。**剩余待定 2 条**：①`#hint get` 在合并模型下的标注形态（我按“显示合并后 code+on 并标来源”落笔）②两个 toggle 与各 `set` 的返回文案用词。
  - **更正一条旧结论（重要）**：本条原来⑧写「聊天开始/聊天结束」没有生产者、是被弃的路径——**错了，而且方法就错了**。生产者一直在 `data/storage/links.json`：`chatstart`（cond `柚子聊聊天$`）→ 返回 `'聊天开始'`，`chatend`（cond `柚子不聊了$`）→ 返回 `'聊天结束'`。**教训：这类「约定」常驻 data（link 节点、prompt），grep 源码找不到 ≠ 没有生产者。**
- **15:39 两条待定通过 → `#hint` 已实现**（2026-09-17）：命令面与 `finally` 求值都在 `mods/chat.py` 内；`py.eval_last` 上升、`link` 改调、`op.require_op` 加 `pattern`、`chat.context_usage()` 新增、`#help [name]` 两级，`docs/commands.md` 与 `docs/llm.md` 已同步。**15:55 两处微调（草籽定）**：`#hint set` 切分改为取剩余整段（不再按第一个换行截断）；求值改用 `py.loc` 的一份副本（`window`/`usage` 与代码里的赋值都不落共享环境）。**已生效**（15:57 草籽 `.reboot`）。15:57 起全局默认 hint 已写进 `data/storage/hint.json`，见下方「默认 hint」条。
- [x] **`current-issues.md` 那条取舍已更新**（13:48 草籽授权）：把 12:10 那次 `cwd="/"` grep 卡死 / `^C` 无效 / 窗口失能 记为「实际事故已发生」，并写明「等事故再收紧」的条件**已触发**、是否加最小限制（工具超时、`^C` 真杀子进程、`pending_calls` 入口查 `should_stop`）**尚未决定**。只记事实、不替草籽决定该不该加。

- [x] **重启后自动接续聊天 + op 工具集（19:03-19:20 实现，19:12 首次实测）**：`.reboot` 存 `{"event", "resume"}`（resume 由 `reboot.is_self_restart` 在写的那一侧算好），`on_load` 读回、发"重启完成"、读后即删；`resume` 为真时 `resume_chat(event)` 起 `reboot-resume` 线程 → `mods.wait_booted()` → `context.set_current(event)` + `chat.chat()`（不伪造入站消息，所以 `^C`/插话/`#hint` 行为与正常聊天一致）。op 工具集 `mods/tools/op.py`（`OP_ONLY=True` + `send_command(text, target="")`，注入走 `connect._events.put`，身份：顶层 `user_id` 留给窗口、作者写 `sender.user_id = bot_id`）；工具层门控 `ToolModule.op_only` 从顶层 `OP_ONLY` 读，`op_tool_visible`/`_visible_catalog`/`SessionBinding._catalog` 三层拦（渲染目录、`load` 拒绝、工具内再查 `op.is_op`），**不在 registry 删模块**。
  - **19:12 端到端实测**：注入 `.reboot` → "重启中"（19:12:45）→ 新进程 19:12:48 → "重启完成" → greet 读后即删 → **续话那轮真的开了**。注入 `#hint get` 也走通了（回复落在本窗口）。
  - **19:12:52 续话首请求 400**（19:12 私聊、19:21 群，两次都是续话轮）：`The reasoning_content in the thinking mode must be passed back to the API.`
    - **19:17 第一次诊断（错，已推翻）**：以为"每条 `assistant(tool_calls)` 都要带、且只在以 tool 收尾时触发"，于是只在 `oplog.build_rounds` 补了空串。19:21 群窗口续话**照样 400**，证明该诊断不完整。
    - **19:24 第二次诊断（对，W1–W9 最小报文实测）**：**带 `tools` 时，最后一条 `user` 之后的每条 `assistant` 都必须带 `reasoning_content`**。四条边界：①无 reasoning 的 assistant 只要后面还有 user 就没事（W6/W7 200），落进尾段就被拒（W1/W2/W8 400）；②补**空串**即通过（W3 200）；③去掉 `tools` 整条校验消失（W4 200）；④`assistant(tool_calls)` 与纯文本 assistant 一视同仁（W5 200 / W9 400）。**关键是 `tools` 这个变量**——第一轮测试漏了它，所以"最小报文全过"却解释不了现场。
    - **真因**：续话轮没有新消息，上下文以 assistant（"重启中／重启完成"等聊天回复）收尾；正常聊天最后一条总是新 user 消息，尾段为空，所以从来碰不到。**与 oplog 无关**（那是误诊）。
    - **修法（经两轮，最终采草籽 19:29 的方案）**：①第一版是 `oplog.build_rounds` 补空串——**误诊**，19:21 群窗口照旧 400（补的位置不对：尾段里那几条聊天回复仍缺）。②第二版 `chat._reasoning_on_tail` 给尾段每条 assistant 补空串——能过，但要替 DS 专有字段倒处插。③**最终**：空串全部**回退**（`oplog` 与 chat 都不再补），改在装配处 `chat._close_with_user(messages)`——尾条不是 `user` 就追加一条 `<system-reminder>` 声明，**内容一句"会话已自动接续。"**（草籽 19:42 要求砍短：换位看，看到自己的"重启中/重启完成"后不需要冗长解释）。理由：不替别的供应商发明 DS 专有字段；形状抄 `tools._announce`（实跑过很多轮）；且只追加一条而不是逐条补字段。`docs/llm.md` 两段重写（含 W1–W9 边界与"为什么不用空串"），`oplog.build_messages` 旧注也改掉（"停在 tool 上被接受"那条结论当时没带 `tools`，是错的）。
    - 待验证：`.reboot` 后**再测一次续话**（这次才是真的端到端）。
  - **实弹验证（`exec(compile(磁盘源码))` 抄进来跑真请求）**：私聊窗口 649-656 条、尾条 `tool`，补声明后发真请求 → **200，模型正常产出工具调用** ✓（19:40、19:43 两次）。
  - 待生效：`mods/chat.py`（核心）。生效后需**再测一次续话**确认 400 不再出现。
  - 教训（又一次）：`from mods import X` 在 exec_code 里拿到的是**运行中**的模块对象，改了磁盘文件后拿它验证等于验证旧代码——要用 `exec(compile(磁盘源码), 旧模块.__dict__)` 的方式验。
- [x] **重启后自动接续聊天**（与上面那条重复，19:12 已实测）：`#hint` 之后的新线。
  - 现状：`.reboot` 把触发事件写进 `data/reboot_greet.py`，下次启动 `reboot.on_load` 读回、发「重启完成」+ 加载失败列表、删文件（**读后即删已有，不重复造**）。会话历史本就在 chatlog、重启不丢（`get_msgs` 重建），缺的只是「没人去开那一轮」。
  - 已定：①**不加**「我刚重启过」的种子——「重启中/重启完成」本来就会出现在历史里；②触发一轮而不插额外内容**有先例**（`#poke`：`cond()` 直接 `return True`，不塞内容），且跑的是**完整 chat 循环**（`capture_chat` → `chat()`），所以续话期间插话、`^C` 的行为都跟正常聊天一致；③**身份沿用最后那轮的触发事件**（`.reboot` 已经把那个事件存下来了，"保持"即可）；④加载失败**只能降级**。
  - 形状：`.reboot` 时除记事件外，再记「当时有活跃轮的窗口」（`context._turns` 的键）；启动时对每个窗口开一轮。复用的原语(`op-toolbox.md` 决定二「开一轮聊天」：`context.begin_turn` + 种子当 `init_chat` 的 messages，就是 `.chat` 的形状)已在那个提案里写好，`.reboot chat [文本]` 与之后的 op 工具集都是它的消费者。
  - 待定：多窗口时记哪几个（`context._turns` 的键，还是只记触发它的那个窗口）；防回环（注入前先清 resume 状态）；注入走 `connect._events.put(event)`（**不能用 `message.recvmsg`**，理由见 op 工具集那条）。
  - **[x] 19:03-19:40 已实现**（草籽 19:03「我觉得还行，开始实现」）。口径收敛成：`resume` = **发起者是不是 Bot 自己**（`history.author(event) == identity.bot_id()`），在 `.reboot` 那一侧算好写进 greet（`{"event": …, "resume": …}`），不在启动时猜；人自己重启不续话。续话 = `context.set_current(event)` + `chat.chat()`（不造假消息），且等 `mods.wait_booted()`（新加：`mods` 里一个在 `boot()` 末尾 set 的 Event）——on_load 期间后面的模块还没加载完。
  - **多窗口那个待定自己消解了**：`resume` 只针对"触发 `.reboot` 的那一个窗口"，不需要"当时有活跃轮的窗口"集合。
  - **18:50 草籽的主意：注入事件的身份 = Bot 自己（`identity.bot_id()`）**——「op 工具集那个『自己给自己发消息』的工具，把发送者设为柚子本身（从配置动态查），柚子 QQ 有 op 权限就能成功；op 工具集本身就要 op，所以问题不大。这个问题之前调查过了，文档里写了发事件就行」。
    - **文档定位**：`docs/working/proposals/op-toolbox.md`「决定一：事件注入 = 投进 `connect._events`」——原话「注入就是 `connect._events.put(event)`」；理由：`connect.recv_msg()` 就是 `connect._events.get()`，主线程 `bot.run` 是唯一路由者。同文档「被排除的做法」第一条正是**直调 `message.recvmsg`**——`mods/message.py` 里它做的是 `return bot.recv(event)`，**在调用者线程里同步跑整轮路由**。
    - **已核对可行（18:55）**：`config.json` 的 `ops = [980001119, 236288772, …]`，`identity.bot_id() == 236288772`，`op.is_op(identity.bot_id()) is True` → 注入事件带 `user_id=236288772` 时 `op.require_op` 直接通过。
    - **顺带解掉提案两条「未决问题」**：①**注入事件的身份** → `identity.bot_id()`（动态查，不写死）。②**注入事件是否进历史** → 会进，但 `chat.msg2chat` 的判据是 `sender.user_id == identity.bot_id()` → 渲染成 **`role="assistant"`**（Bot 自己的发言），不是「某个用户说的话」；计费也归到 Bot 自己（`chat._usage_entry` 取 `context.current()["user_id"]` → `chattop` 里就是 Bot 那行）。
    - **残留待定**：注入的命令若不以 `#` 开头（`.reboot`/`!ls`），`_route` 会**先写 chatlog** 再执行，而 `_selected_events` 只过滤 `#` 开头 → 它会作为 assistant 消息进模型上下文。要不要给注入事件打标记让 `_selected_events` 跳过（与提案「防回环」同源）。**20:30 草籽：不用 → 不做（本条关闭）。**
- [x] **续修「私聊 `user_id` 的歧义」（草籽 18:54 点）**：上次修的是 `history.same_author`（`op`/`post` 的节流、`cave.get_self_log`），这次把剩下两个**作者语义**的读者改走 `history.author`：
  - `mods/op.py::is_op`——传事件时改读作者，传整数不变。这是「以 Bot 自己的身份在当前窗口注入一条命令」能过 op 门的前提（窗口仍由顶层 `user_id` 决定，作者由 `sender.user_id` 声明）。
  - `mods/chat.py::_usage_entry`——LLM 费用归属，docstring 本来就写着 "the acting user's"。改动只在私聊且只在 Bot 自己是作者时生效：那一轮的费用记到 Bot 而不是窗口对端（副作用：`.chattop` 私聊非 op 视图里看不到这行）。
  - **保持原样的是"窗口语义"**：`later`/`todo`/`chattop`/`cave`/`edit`/`jrgz` 等那里的 `user_id` 是窗口键或窗口对端（私聊里就是路由目标），不是作者。**判据：问"谁发的"用 `history.author`，问"哪个窗口/发给谁"才用 `user_id`。**
  - 离线验证：`is_op` 8 例、`_usage_entry` 6 例全过；注入场景（窗口=某人私聊、作者=柚子）→ 过门，同窗口普通人消息 → 拒。`--check` → `100 Python files, 80 public modules`（少的一个是草籽有意删的 `mods/chat4.py`）。
  - 文档：`docs/runtime.md` 第 96 行（原写"权限判定只看 `user_id` 这一个字段"）改为精确说明"读作者 `sender.user_id`，回落 `user_id`；顶层 `user_id` 只在群聊里等于作者"；`docs/working/current-issues.md` 的「私聊 `is_self`」条补了「续修」段。
  - **已知限制（未改）**：`message.recvmsg()` 把 `sender_id` 同时写进 `user_id` 与 `sender.user_id`，表达不了「作者 ≠ 窗口」。op 工具集按提案是手工构造事件投 `connect._events.put`，不走它，所以不影响那条线；`pctest` 也仍是窗口语义（真实用途下本来就对）。**待 `.reboot` 生效**（`op`/`chat` 都是核心模块）。

  - **18:15-18:52 勘察：「检测这次重启是不是 agent 自己调的」——先要有一条 agent 路径，否则无从检测。**
    - **事实一：现在每一次重启都从同一条路进来。** `bot.run()`（MainThread）→ `recv` → `_route()` → 第 169 行 `value.startswith(".")` → `command.run("reboot")` → `reboot.run` 抛 `SystemExit(233)`。也就是说重启**必然是一条 `.reboot` 消息**被主循环路由；发起者就是那条消息的发送者，而 greet 文件里存的正是那个 event。
    - **事实二：agent 现在没有能用的自我重启路径。** 工具调用跑在 `mods.link._dispatch`（`link.dispatch = thread.to_thread(None)(_dispatch)`，而 `capture_chat` 是 capture、在 `_dispatch` 里同步跑整轮 chat）。`exec_code` 里 `raise SystemExit(233)` 会被 `thread.to_thread` 的 `except BaseException` 接住 → `future.set_exception(233)` → 而 `bot._route` 把 `link.dispatch(event)` 的 future **丢掉** → 进程根本不退，只是那个 worker 线程结束。**18:52 实测**（用真 `thread.to_thread`）：`future.done()=True`、`future.exception()=SystemExit(233)`、进程与其它 9 个线程照常。唯一的土办法是 `os._exit(233)`，但它跳过 `mods.exit()`（storage 保存）与「重启中/重启完成」，是脏退出。`later` 任务同理（APScheduler 线程里 `future.result()` 才重新抛，抛在调度线程里）。`src.set(..., reload=True)` 也只有从主线程调才有效。
    - **事实三：检测本身便宜，但粒度有三档。** ①**看消息**（零成本、已有）：greet 文件里的 event，body 以 `.reboot` 开头即命令面——分不出「人打的」与「agent 注入的」（`connect._events.put` 可伪造 sender）。②**看线程**：`threading.current_thread() is threading.main_thread()`——命令面必然 MainThread，代码路径必然 worker（实测工具里是 `mods.link._dispatch`、`.py` 是 `mods.py.run`、`.chat` 是 `mods.chat.run`）。一行，但描述的是「从哪儿抛的」不是「谁想抛的」。③**显式声明**（推荐）：把重启收成一个咽喉点 `reboot.request(initiator)`（发「重启中」+ 写带 `initiator` 的 greet 文件 + 抛），`.reboot` 传 `"user"`、将来的 agent 工具传 `"agent"`；检测变成读字段而非猜。
    - **前置活**：真要给 agent 一条重启路径，得让 `SystemExit(233)` 从 worker 线程**真的能终止进程**——在 `thread.to_thread`（或 `chat.chat` 的 finally）截住它转交主线程，干净做法是置模块标志 + 往 `connect._events` 塞哨兵事件让主循环退出（保住 `mods.exit()` 的保存）。**与 `^C` 硬停止那条是同一片地。**
- [x] **上下文上限的查看/改工具**（草籽 16:07 提）→ **22:27 草籽确认「已经有了，而且现在已经能补了」**：要能把 `max_token`/`max_msg` 手动查看和改（现行值在 `data/storage/llm_system/config.json`，由 `chat.on_load` 读进模块全局 `chat.max_token`/`chat.max_msg`）。
  - **现状（16:15 查证）：没有任何 `#` 命令能看或改这两个值。** 现行命令面只有 help/model/models/use_model/prompt/add_prompt/setting/use_setting/set_setting/del_setting/image/reasoning/tools/ops/hint；`max_token`/`max_msg` 在源码里只出现在模块默认值（43-44 行）和 `chat.on_load`（1020-1021 行）两处。唯一的改法是手改 `llm_system/config.json` 再 `.reboot`（因为 `on_load` 是一次性读进模块全局，热改配置不生效）。另：`.chattop [月份]` 看费用、`#ops [clear]` 看本窗口操作历史，都不是这个。
  - **待澄清已解**（22:27 核实）：`#limit`（`_limit_report`/`_limit_set`，`#limit reset` 回落默认）就是查改工具；`chat._selected_events`（314-325 行）也确实补上了超过 `MAX_LEN = 256` 的取法——内存不够时走 `history.getlog(current, limit=max_msg)` → `chatlog.read_range` 按天倒走、读够就停，`OSError` 时退回手上那份。所以「`#limit 500` 静默只给 256 条」的问题**已经不存在**。
- [x] **op 工具集与事件注入（19:03-19:40 实现）**：
  - `mods/tools/op.py`：`OP_ONLY = True` + `send_command(text, target="")`。构造入站形状事件 → `connect._events.put(event)`，主线程按真实路由跑；工具立刻返回。`target` 用 `g<群号>`/`u<QQ号>`（大小写都认）或留空=当前窗口。
  - **身份**（草籽 18:50 的主意）：顶层 `user_id` 留作**窗口**，作者写 `sender.user_id = identity.bot_id()`。配合同日把 `op.is_op(event)` 与 `chat._usage_entry` 的判据改成 `history.author`（那条"私聊 user_id 歧义"的续修）——于是私聊里注入照样过 op 门，而「重启中/重启完成」回到原窗口（`message.target` 先看 `group_id`）。这正是草籽 18:52 问的那个问题。
  - **门控（提案决定三）**：`mods/tools/__init__.py` 新增 `ToolModule.op_only`（从顶层 `OP_ONLY` 读）、`op_tool_visible`、`_visible_catalog`、`SessionBinding._catalog`，并给 `create_context_message`/`bind_session` 加 `visible` 参数（默认即 op 规则，所以 chat/agents 两个调用点都不用改）。三层拦：渲染目录过滤、`load` 拒绝、工具内 `op.is_op`。**没有**在 registry 层删模块。
  - 未做（提案里列出、这次没实现）：`send_message`（往指定窗口**说话**，与「执行」分开）、`open_chat(window, seed)`、`.reboot chat [文本]`（带种子的续话）。**20:30 草籽：这一组是讨论重启方案时顺带列出的，现在没必要了 → 不做。**
- **（事实）默认 hint**：全局默认 `data/storage/hint.json` = `{"code": …, "on": true}`。规则：`k`/`m` 缩写（≥1e6 → 如 `2.3m`；≥1e3 → 如 `2.3k`，整百去尾零 → `200k`）；消息数取 `chat.get_msgs()`、上限取 `chat.max_msg`/`chat.max_token`；样例「消息数：102/256 / 上下文：7.3k/200k」。
- **（事实）`exec_code` 的 `globals()` 就是 `py.loc` 本体**：探针写下的名字会直接落进运行中的共享动态环境，不是隔离沙箱。草籽 16:10：「正常情况下没必要清理，本来就挺多东西的」——所以探针残留**不用清**。

- [x] **统一的 LLM 配置覆盖机制 + `#limit`**（16:18 提、16:52 开工）：想要**每窗口**能设上下文窗口与消息条数，**并能回落默认**；指出 hint 上刚做过同一形状（两份字典 + `{**default, **window}`），「也许是时候抽象出来了」。
  - **现场（16:18 勘察）：已经有事实上的「窗口覆盖」模式，散在 7 处，但缺省来源与值类型各不同。**
    | 配置 | 窗口层（`getchatstorage()`） | 缺省层 | 读取点 |
    |---|---|---|---|
    | `model` | `data["model"]` | `llm_config["default_model"]` → `llm.DEFAULT_MODEL` | `get_model(data)` |
    | `prompt` | `data["prompt"]`（名字或列表） | 全局 `settings` | `get_prompt()` |
    | `image` | `data["image"]` | 常量 `"off"` | `normalize_image_mode` |
    | `reasoning` | `data["reasoning"]` | 常量 `"keep"` | `normalize_reasoning_mode` |
    | `tools` | `data["tools"]` | 常量 `"append"` | `normalize_tools_mode` |
    | `hint` | `data["hint"]`（dict） | `storage.get("","hint")`（dict） | `_hint_effective` |
    | `max_token`/`max_msg` | **无** | `llm_config`（`on_load` 读成模块全局） | 直接用模块全局 |
  - **关键事实**：①`chat.llm_config` **就是** `storage.get("llm_system","config")` 的同一对象（活引用，`is` 为真）→ 改全局默认**不需要重启**；但 `max_token`/`max_msg` 是 `on_load` 时 `int(...)` 出来的**快照**，所以**不热生效**——这是两个不同的问题。②`#setting` 系列是 **prompt 命名集**（`prompts` 字典 + `data["prompt"]`），**不是**通用配置覆盖，别混。③`max_*` 的消费点是 `_within_budget`/`_selected_events`/`build_context`（**每次重建上下文都算**，`get_msgs` 是热路径），加窗口层=行为改变，不只是加存储。
  - **柚子的判断（待草籽拍）**：该抽象的是「**单值**窗口配置」这一类（model/image/reasoning/tools/max_*），缺省来源是一个取值函数；`hint`/`prompt` 是**复合值**，它们的「合并」就是各自一行、已经够薄，不该被吞进单值表（否则才是造间接层）。另外 `max_msg > 256` 要突破 `history.MAX_LEN` 就得走 `history.getlog(since/until)` **读 chatlog 文件**——这是**独立于抽象**的另一件活，也是真正的工作量。
- [x] **事故与修复（2026-09-17 16:13-16:33）**：
  - **我（柚子）的探针把本窗口的回复发进了群 916083933**。根因：`meta.exec_code` 直接在 `mods.link._dispatch` 线程里跑，而那个线程的 `context.current()` **就是它自己的路由状态**；我用探针 `set_current(群事件)` 之后，同一线程里仍在跑的聊天循环继续 `sendmsg`，全部照着"当前=群"发。**已修**：`exec_code` 进入时快照 `context.current()`、`finally` 里无条件还原（与它原本还原 `py.loc["print"]` 同一个套路）。**已实测生效**（工具里把 current 改成群事件，退出后 `context.current() is 原事件` 为真）。**教训：`context.current()` 是线程局部的**路由**，不是只读的查询参数——改它等于改正在跑的那一轮的去向。**
  - **图片每轮重复下载/刷屏（草籽 16:30 报）**。根因：`_download_image_to_cache` 失败时**不写别名、也不写负缓存**，于是下一轮、每个子请求都重新 curl 一遍；窗口里 90 张图有 **26 张永久失败**（`rkey` 过期 → 返回 HTML → `ValueError: 下载内容不是可识别的图片`），每轮固定 68 命中 + 24 下载。**已修**：`mods/image` 加**对话级检查台账**（线程局部，`begin_conversation`/`end_conversation`），`resolve_image_with_digest` 命中台账即短路（成功直接返回、失败抛 `AlreadyCheckedFailed`）；`mods/llm` 的 `_convert_images`/`_describe_images` 据此跳过重试与重复日志；`mods/chat.py` 的 `chat()`（覆盖多轮+插话续写）与 `.chat` 命令各包一层 `begin/end`。**口径（草籽 16:32/16:44）**：一次对话内不重复查；对话结束（`finally`）后再聊重新查是正常的；插话续写算同一轮。离线实测：同一对话第 1 轮解析 3 次/6 行日志，第 2、3 轮 **0 次/0 行**；新对话重新检查。
  - **待生效**：`image`/`llm`/`chat` 都是核心模块，**要 `.reboot`**；`meta`（工具模块）已 reload 生效。
- [x] **统一窗口级配置 + `#limit`（16:52 草籽点「先1+2」）**：
  - **抽象的口径（草籽 16:30 定）**：`max_*` 与 `image` 都**不要全局默认**，默认值写死在代码里；`tools` 一起收进来。`hint`/`prompt` **不在表里**——它们是复合值，缺省来自别的存储，合并各只有一行，塞进单值表反而要造间接层。
  - **落地**：`mods/chat.WINDOW_SETTINGS`（命令名 → storage 键 / 默认值 / 归一化）+ `window_setting(name, data=None)` + `limit(event=None) -> (max_msg, max_token)`；`get_image_mode`/`get_reasoning_mode`/`get_tools_mode` 改成 `window_setting` 的薄壳；`_selected_events`/`get_msgs`/`build_context` 改用窗口级值。默认值 `DEFAULT_MAX_MSG = 100`、`DEFAULT_MAX_TOKEN = 32000`（**数字待草籽确认**）。
  - **新命令 `#limit`**：无参看（标「本窗口」/「默认」）/ `#limit <条数> <token>` / `#limit reset`；op 无关（跟 `#image` 同级）。
  - **默认值定稿（17:20 草籽）**：`DEFAULT_MAX_MSG = 20`（正常聊天不编程，几十条就够，他甚至考虑 20）、`DEFAULT_MAX_TOKEN = 50000`（token 要给宽——"我可不想因为 token 数太少而到一半没法思考了"，且这个会话都没到 2w）。"群友说想要 16384 和 1M，那个之后再说"——窗口级现在就能设（`#limit 16384 1000000`）。
  - **死数据已删（17:20 草籽：「否则有误导」）**：`llm_system/config.json` 的 `max_token`(200000)/`max_msg`(256) 已 `pop` + `storage.save()`；全仓确认只有 `chat.py` 读过它们。
  - **超 256**：`chatlog.read_range` 加 `limit`（天文件倒序 + 天内反转 + 读够就停，与「全读再 reverse」等价，已用真数据验证：私聊 29434 条各 limit 全一致）+ `history.getlog(..., limit=)`；`_selected_events` 在内存不够时回文件取。
  - **`on_load` 不再读 `llm_system/config.json` 的 max_token/max_msg**；那两个键 17:20 就已从盘上删掉，**20:30 草籽复核「删」**（20:40 重看 `data/storage/llm_system/config.json`，确实没有这两个键）。本条关闭。
  - **踩坑（重要）**：`WINDOW_SETTINGS` 是模块级立刻求值，我原先把它放在 `normalize_reasoning_mode`/`normalize_tools_mode` **之前** → import 时 `NameError`。**`compile()` 只查语法，查不出这个**；靠 `run.py --smoke`（真加载）才发现。教训：改顶层赋值顺序，要用真加载验证，不能只 `--check`。
  - **另一个教训**：我在 `exec_code` 里用 `ctx = Ctx()` 覆盖了预置的模块字典 `ctx`，导致后续取模块失败。**别占用 `exec_code` 的预置名。**
  - 文档：`docs/commands.md`、`docs/llm.md`（新增「窗口级配置与默认值」一节）已同步。**待 `.reboot` 生效。**
- [x] **合并转发：让柚子看得见、也发得出去**（草籽 20:30 提；20:35-20:50 只读调查；21:15 草籽「做合并转发的」）：
  - **已落地**：新模块 `mods/forward.py`（取回 `get_forward_msg` → 落盘 `data/forward/<id>.json`，图片就地本地化 → 渲染成多行文本）+ 新工具 `mods/tools/forward.py`（`forward__read_forward` 给模型读、`forward__send_forward` 发，每行一条 `昵称: 正文`，换行写 `\n`）；`mods/cave.py` 的 `set` 改走 `forward.store`（转发取回并存档后展开成正文，取不到就留原样），`mods/message.py` 抽出 `record_sent`。
  - **实测（取）**：`7686433057126267396`（4 条）和 `7686341789559607078`（5 条、4 张图）都取回并落盘，图片写进 `data/images/`；同一个 id 第二次走内存/磁盘。两类取不到（过期 id；`7686404960608762819` 那种 retcode 0 但 `messages` 为空）都返回"取不到"，不报错。
  - **实测（发）**：`{"type":"node","data":{"name","uin","content"}}` 发得出去（retcode 0），`content` 收字符串也收数组。**按 id 转发已有消息走不通**——`{"type":"node","data":{"id":"…"}}` 报「发送伪造合并转发消息失败：生成节点为空」（NapCat 走 packet 路径，短 id 查不到），所以不做。NapCat 侧节点深度上限 3 层，`MAX_DEPTH` 与它对齐。
  - **一个新形状**：**自己发出去的**转发在 `get_msg` 里不是 `[CQ:forward,id=…]`，而是一张 `[CQ:json,…]` 卡片（`app=com.tencent.multimsg`），真 id 在 `meta.detail.resid`；这个 resid 同样能 `get_forward_msg` 取回，所以 `code_id` 两种形状都认，落盘文件名过 `quote`（resid 里带 `/`）。
  - **补的一个洞**：「发出去的消息要进 chatlog 和内存历史」原先只写在 `message._send_now` 里，于是走 `send_forward_msg` 的转发**没有痕迹**（日志没有、模型下一轮看不见自己发过）。抽出 `message.record_sent(message_id, …)`，`deliver` 成功后调它。
  - **仍未做（有意）**：① `chat.msg_split` 不自动展开转发，模型看到卡片后自己调 `read_forward`——隐藏的网络 IO 不进那个纯函数；要"自动展开"是另一个决定，等草籽。② 没有 `.forward` 命令。③ 不碰压缩那条线。
  - **待生效**：`mods/forward.py`、`mods/cave.py`、`mods/message.py` 是运行期模块，要 `.reboot`。改动未提交。
  - **入口形状**：转发来到事件里就是一条 CQ 串 `[CQ:forward,id=…]`（本窗口 2026-09-17 的 11:31:46 / 15:36:54 / 17:25:56 各一条）。`chat.msg_split` 只认 image CQ，其余原样当文本 —— 所以模型看到的就只有这串卡片文字，**没有内层 id 可捞**。
  - **取内容可行**：`connect.call_api("get_forward_msg", message_id=…)` 能拿到节点数组，每节点带 `user_id`、`sender.nickname`、`raw_message`、`message` 结构（实取到 5 条与 4 条两条真实转发）。`message_id=` 与 `id=` 两种写法都通。
  - **但会失败，两种**：①旧的（2024-12、2025-11 的 id）报 `消息已过期或者为内层消息，无法获取转发消息`；②同一天的 `7686404960608762819`（15:36 那条交接清单）**稳定**返回 `status ok` + `messages: []`，连试三次都一样（`id=` 形态也空；`id=` 传 int 反而报"过期"）。所以取转发必须**尽力而为 + 有回退**，不能假定成功——这也解释了 15:38 我为什么是去 chatlog 里把正文翻出来的。
  - **为什么必须落盘**：转发 id 会过期（几小时到几天），节点里的图片 url 带 `rkey`、同样会过期。`cq.save_pic` 已经把"图片本地化"这条路走通了（cave 存的是 `file://` 本地路径），转发照抄这个形状即可。
  - **现在完全是空的**：全仓**没有任何代码**处理 forward（`get_forward_msg` 只在 chatlog 与本手记里出现过），`cave.set` 只做 `cq.save_pic` —— 于是 `.cave add` 一条转发消息，存下来的就是那串会过期的 id。
  - **发送侧存在**：`send_private_forward_msg` / `send_forward_msg` 两个 action 都在（探测方法是拿一个不存在的 action 对比措辞：不存在报 `不支持的Api X`，这两个报的是参数层的"无法获取用户信息"）。**真正发一条还没试过**（怕刷屏，等草籽点头）。
- [x] **`chattop` 的费用口径失真**（草籽 20:34 提；20:40 调查；21:57 做完，待 `.reboot` 生效）：
  - **现状**：`llm.LLMResponse` 只带 `prompt_tokens`/`completion_tokens`/`total_tokens`，`chat.inc_call_tokens_cost` 就是 `prompt_tokens × prompt_price + completion_tokens × completion_price`；全仓**没有**任何地方读缓存命中 token。所以命中的部分被按未命中价收了 —— **系统性高估**，上下文越长偏得越离谱。
  - **实测**（同一报文连发两次的真调用）：第一次 `hit=0 miss=845`，第二次 `hit=640 miss=205`。76% 的输入其实是命中价，差 50 倍（Flash 命中 0.04 vs 未命中 2，高峰价）。
  - **字段名两种都在**：`usage.prompt_cache_hit_tokens` / `usage.prompt_cache_miss_tokens`（DeepSeek 专有）与 `usage.prompt_tokens_details.cached_tokens`（OpenAI 通用），SDK 都透出来了。
  - **官价**（2026-09-17 现场拉 https://api-docs.deepseek.com/zh-cn/quick_start/pricing ，元/百万 token）：`deepseek-flash` 命中 空闲 0.02 / 高峰 0.04、未命中 1 / 2、输出 4 / 8；`deepseek-v4-pro` 命中 0.15 / 0.30、未命中 4.5 / 9、输出 13.5 / 27。**空闲价 = 高峰价的一半**；高峰 = 北京时间周一至周五 9:00-12:00、14:00-18:00。
  - **现配置**只写 `prompt_price: 2` / `completion_price: 8`（= Flash 高峰未命中 / 输出）。要动的形状：模型条目加"命中价"、按请求时刻判高峰/空闲（半个乘数）、`LLMResponse` 多带一组 token、`inc_call_tokens_cost` 改口径。`chattop` 只显示"次数 + 总额"，要不要把命中率也摆出来是另一个决定（那要在 `usage` 里存新字段，现在是事中累加的一个金额）。
  - **落地（21:57）**：①新模块 `mods/llm/pricing.py`——`unit_prices` / `token_cost` / `is_off_peak` / `describe_off_peak`；`off_peak` 写的是"高峰在哪些天的哪些时段"加一个 `ratio`，没有规则的 provider 全天按高峰价，时区认不出按 UTC（计费路径不为自己抛异常）。②`LLMResponse` 多一个 `cached_tokens`（`__add__` 一起累加），`llm.usage_cached_tokens` 两种字段名都认。③`chat.inc_call_tokens_cost` → `inc_call_cost(model, prompt, completion, cached)`，改读 `llm.provider_config` + `pricing.token_cost`；`mods/tools/agents.py` 那个调用点同步改。④`models.py` 默认目录里 deepseek 的三个 Flash 名与 pro 都补 `prompt_cached_price`（0.04 / 0.30，高峰价），provider 加 `off_peak`；新增宽松的 `models.provider_config()`（取不到就 `{}`，不抛）。⑤`#model` / `#models` 表头改成「模型 输入(未命中/命中) 输出 …」，列高峰价，末尾多一行峰谷说明（含"当前按高峰/空闲价计"）。
  - **两个决定**：①`prompt_cached_price` **缺省跟随 `prompt_price`**，不跟随 0——只登记了输入/输出价时把命中当免费会静默少算一大截，而"整条模型没登记 → 三项全 0"才是原先那条"宁可少算"。②命中数按 prompt 总数**夹一次**（`cached = min(cached, prompt)`）：供应商偶尔给出对不上的数，不夹会算出负的未命中量、把这一笔记成负费用。
  - **实配已迁**：`data/storage/llm_system/config.json` 的 deepseek 已补 `prompt_cached_price` 与 `off_peak`（走 `storage.get` + `save()`，不手改文件）。bytecat/gpt/foli 那些只登记了 prompt/completion 的 provider 保持原样：它们的命中价会跟随未命中价算——**不假装知道折扣，也不高估**；要精确得各自补命中价。
  - **验算**（真实那组数：845 prompt / 640 命中 / 100 输出）：高峰 `0.0012356`、空闲正好一半 `0.0006178`、全按未命中 `0.00249`——第三条就是改之前的口径，**高估一倍**；命中数超限被夹住、缺命中价跟随未命中价，两例也都对。
  - **文档**：`docs/llm.md` 的「供应商与模型能力」与 chattop 那节已同步。
  - **21:59 重置本月用量（草籽：「额度重置一下，前面都记错了」）**：口径修正后，之前记进 `data/storage/usage/2026-09.json` 的数字全部失真（`980001119` 甚至记到 ￥154.84），故 `storage.delete("usage", "2026-09")` 整月清空；旧文件先备份到 `data/backup/usage/2026-09.json.bak.20260917215911`（可回滚）。**一个已知粗糙处**：重置那一刻有一轮调用正在飞，它的计数（`inc_call_count`，一轮一次、在请求前）被清掉、而费用（每个响应后）落回，于是会留下「0 次调用却有钱」的条目——手动把该轮计数补回 1 抹平；同理，重置后**当前这一轮自己**的费用仍会照常计入（约 ￥0.03），这是对的，它是重置之后发生的事。
- [x] **网上搜索**（草籽 20:34：记下来，我想实现；22:45 已实现）：仓库里现在只有 `.search`（本地 chatlog 正则）和 MC/天气这类**固定站点**工具，没有任何通用网络搜索。线索：`exec_code` 能直接出网（本次调查就是 `urllib` 拉的官方文档）。走搜索 API（要密钥）还是抓 HTML（会脆）没定。

  - **22:30 调查：现成的答案就在这台机器上**。`/root/deepseek-harness/packages/web/` 里有 `web-search-exa` / `web-search-deepseek` / `web-search-perplexity` 三个后端，外加一个 `web-fetch-http`。**DeepSeek 那个不需要任何新密钥**：它把搜索当作**服务端原生工具**，走 DeepSeek 官方的 **Anthropic 兼容端点**。
    - **请求形状**：`POST https://api.deepseek.com/anthropic/v1/messages`；头同时给 `x-api-key` 与 `Authorization: Bearer`（官方认前者、Anthropic 兼容网关认后者，两个都发）+ `anthropic-version: 2023-06-01`；body = `{model: "deepseek-v4-flash"（Anthropic 命名）, max_tokens, messages: [一条 user 文本 "Perform a web search for the query: …"], tools: [{type: "web_search_20250305", name: "web_search", max_uses}]}`。
    - **响应形状**：`content` 是块序列 `[thinking, text, server_tool_use, web_search_tool_result, thinking, text…]`；`web_search_tool_result.content[]` 每条 = `title` / `url` / `page_age`（常 null）/ `encrypted_content`，**实测一次 10 条**；末尾 `text` 块还有一段模型自述（可当摘要）。harness 里 snippet 取自 `text.citations[].cited_text` 并按 url 对齐，**实测 citations 是空的**——所以常常没有摘要，要么用模型那段话，要么自己去抓页面（正是下一条）。
    - **用量**：`usage.server_tool_use.web_search_requests`（搜索次数在这里），加上常规 token。**注意它的 base 与 `.env` 里聊天用的 `/beta` 不是同一个**，别复用 `DEEPSEEK_BASE_URL`，只共用 `DEEPSEEK_API_KEY`。
    - **实测（22:30，草籽建议先测）**：现有 key + 该端点 → HTTP 200、**4.9 秒**、10 条结果，**不需要代理**（`api.deepseek.com` 直连就通；同一时刻 DDG / Google / GitHub / Bing **全部 SSL 失败**，只有百度 / 知乎这类国内站通——即这台机器在墙内，通用搜索靠直连抓 HTML 基本没戏）。
    - Exa / Perplexity 那两个各自要 `api.exa.ai` / `api.perplexity.ai` 的密钥，harness 里也没配 → **不考虑**。
    - **`web-fetch-http` 的安全形状值得抄**（正好是「网页查看」的参考）：只允许解析到**公有 IP** 的目标、只跟随**同源**重定向、时限 + 响应体积上限、按 charset 解码、不带任何 cookie 或环境凭据——**SSRF 防护清单是现成的**。
    - **待定**：①工具形状（模型侧 `websearch__search` + 一个命令；`.search` 已被 chatlog 正则占用，命令名要另起）；②是否把模型那段自述一起交回；③结果是**不受信输入** → 注入防护（明确包成数据块）；④搜索要不要单独计价（次数有了，价格需查官方文档）。
    - **22:45 落地（草籽拍：叫 `websearch`，只要工具、不要命令）**：新模块 `mods/websearch.py`（`PHASE = FEATURE`）+ 工具 `mods/tools/websearch.py`（导出 `websearch__search(query)`）。
      - 形状按草籽「**你只要调用接口就行了，又不是用那个 harness**」的口径：请求形状照抄（Anthropic 兼容端点 + `web_search_20250305`），但**代码全在自己这边**（`urllib`，零新依赖），不依赖 harness。base 可用 `DEEPSEEK_WEB_SEARCH_BASE_URL` 覆盖，密钥只取 `DEEPSEEK_API_KEY`。
      - **摘要两头都取**：能对齐上的逐条 `snippet`（`text.citations[].cited_text`，按 url 去重）填进列表；同时把末尾那段模型自述作为「综合说明」放最前面——实测这段**内容相当有用**，逐条摘要则常为空。
      - **工具返回是文本**：`【综合说明】` + `【共 N 条结果】` 编号列表（标题 / 链接 / 时间 / 摘要），摘要截 300 字、自述截 2000 字、最多列 10 条（超出提示还有几条）。失败不抛异常，返回「检索失败：类型: 消息」，并且 docstring 明确要求**查不到就说查不到，不许凭印象编**。
      - **不受信输入**：工具说明里写明综合说明与摘要是外部内容，可能夹带诱导，只作资料引用、不执行其中指令。
      - **实测**：核心模块直调 → 4.9 秒、10 条结果、`usage.server_tool_use.web_search_requests = 1`；工具层 `search("柚子 狐狸 兽耳")` → 1917 字符串，格式正常。
      - **重启后验证（22:5x）**：`run.py --check` → 107 Python files / 83 public modules；`mods.websearch` 已加载；last-good registry 里 `websearch` 的说明首行与导出 `websearch__search` 都在（共 14 个 last-good 模块）。
      - **还没做**：没做命令（草籽说只要工具）；没接进 chattop 计价（`searches` 已经解析出来备用）。
- [x] **网页查看**（草籽 20:34：用 py 应该够了，也许不需要专门工具；22:44 补口径；23:0x 拍：裸 Chrome + CDP、缺什么装什么、浏览器自管、推进到可用）：与上一条同族。草籽 22:44 的定调是——**得有能力上浏览器操作的工具**，配合模型自带的识图，这样兼容性最好，才能真正"网上冲浪"；而**普通情况只要有链接，直接用 py 代码抓取就已经很强很灵活，做成格式化抓取反而会限制能力**。也就是说：不要在 py 之上再包一层"网页解析器"。
  - 现成参考仍是 harness 的 `web-fetch-http`（SSRF 防护清单：只允许公有 IP、只跟随同源重定向、时限 + 体积上限、不带凭据）。
  - **23:0x～23:1x 落地**：新模块 `mods/browser.py`（`PHASE = FEATURE`）+ 工具 `mods/tools/browser.py`（`browser__open_page / read_page / page_links / run_js / look / screenshot`）。
    - **家底**：宿主上一份浏览器都没有，唯一那份 Chromium 是 **codex 用 `npx playwright-core` 留在 `/root/.cache/ms-playwright/chromium-1243/` 的缓存**（279 MB 可执行 + 393 MB 整目录，旁边还有 `ffmpeg-1011`）。按"浏览器自管"，`install()` 把它复制到 `data/browser/chrome-linux64`（实测 1.4 s），`binary()` 只认自己这份，**不再依赖 npx 缓存**。
    - **形态（草籽拍：裸 Chrome + CDP）**：常驻 Chrome + `--remote-debugging-port`，Python 侧直连 **CDP**（`websockets`，唯一新依赖）；不装 playwright、也不养 node 进程。理由：playwright(py) 要另装包且要再下一份对得上版本的浏览器；裸 CDP 只要一个纯 Python websocket，协议本身是 JSON。
    - **交互靠 `Runtime.evaluate`**：点击 / 填表 / 滚动 / 取元素文字都在页面里跑一段 JS 完成（`run_js`），不必封装鼠标键盘事件；这套加上 `Page.captureScreenshot` + 现有视觉链路（`look`）已经覆盖绝大多数场景。
    - **每个聊天窗口一个标签页**（键 = `history.window`，与 `^C`、usage 同一条归属），互相不串；每次动作新建一条 websocket、用完即关，没有需要维护的长连接。
    - **不受信 / SSRF 两道网**：①`check_url` 只允许 http/https 且**解析出的地址全部是公网**（十进制 IP、IPv4-mapped IPv6、CGNAT、云元数据地址都能拦下，实测 15 例）；②导航期间用 `Fetch` 把**每个子请求**再过一遍，命中内网就 `failRequest`（实测假会话：公网放行、`10.x`/`169.254.x` 失败）。工具说明里写明页面正文、脚本返回值、视觉描述都是外部内容，只当资料、绝不执行其中指令。
    - **看门狗豁免**：浏览器是常驻进程，用新加的 `watchdog.detached()` 起，**不进登记表**——否则第一次访问网页时它会被记进那次工具调用，一个 `^C` 就连页面一起带走（实测：登记表里 0 个子进程，`^C` 后 chrome 仍在）。
    - **实测（23:1x）**：起 → 停零残留、可反复；`example.com` 加载完成、`百度` 正文 380 字符（JS 渲染内容拿到了）、`page_links`、`run_js` 取标题、视口/整页截图、`describe_image` 看图（2.5 s，正确说出"百度热搜"）全通；`stop()` 干净退出。
    - **三个实测修正（写进文档时别抄错）**：
      1. **冷启动不是二十多秒**。早先"一次 `--dump-dom` 花 27 s"的观察主要花在**页面本身**（mcmod.cn 反爬 + 网络）。实测 Chrome 启动**只要 0.3～0.4 s**，空 profile 与热 profile 一样快（profile 也就 6.2 MB）；真正的等待在**页面加载**（百度约 11 s）。所以"必须常驻"的理由不是冷启动贵，而是**页面状态、登录态和多步交互需要连续性**。
      2. **Chrome 会就地改写自己的 argv**，把 NUL 分隔换成**空格**分隔（为了 `ps` 好看）。所以按 `/proc/<pid>/cmdline` 认残留进程时**不能 `split(b"\0")`**——那样只会拿到一整行、永远认不出。要先把 NUL 换成空格再切词。这一条曾让 `_stale_pids()` 一直返回空。
      3. **`Popen(cwd=...)` + 相对可执行路径会失败**：子进程先 chdir 再 exec，相对路径于是被解析到 `cwd` 里去（实测报 `No such file or directory: 'data/browser/…/chrome'`）。所有路径必须落成绝对路径。
    - **一个必须有的清扫**：跟踪会断（强杀、模块重载、上次运行留下的孤儿），残留 Chrome 会一直占着 profile 与端口。所以启动前按 `--user-data-dir` 认一遍并清掉（先 SIGTERM 再 SIGKILL），`stop()` 收尾也扫一次。实测曾积到 40 个残留进程，一次清扫 0.25 s 清完。
    - **依赖**：新增 `websockets`；顺手补上**漏登记的 `beautifulsoup4`**（`mods/tools/minecraft.py` 一直在用却没写进 `pyproject.toml`）。`uv lock` 只新增这三项（`beautifulsoup4` / `soupsieve` / `websockets`），没有别的漂移。
    - **有意没做**：没有命令（只给模型用）；没做登录态维护、cookie 导出、多标签并发；页面内容不进 chattop 计费。
    - **23:18 重启后复核，抓到并修掉三处（都是"看起来正常、其实在撒谎"那类）**：
      1. **`_stale_pids()` 把自己的子进程当成了残留**：Chrome 的子进程（zygote / renderer / gpu / crashpad）会**继承**父进程的命令行，`--user-data-dir` 也在里面，于是它们全部命中判断。表现是浏览器跑着的时候 `status()` 谎报 `stale: 11`，而 `_reap_stale()` 会去 SIGTERM 一堆活得好好的子进程。判据改成"主进程不带 `--type=`，子进程带"。**修正前 11、修正后 0**。
      2. **`pages()` 是查询却会拉起浏览器**：它第一行就是 `start()`，于是"看看有几页"这个纯查询会顺手起一个两百兆的常驻进程。改成：没在跑就返回空列表，要看状态用 `status()`。
      3. **Chrome 会恢复上一次的会话**，于是"刚起来的浏览器"里可能挂着上一轮留下的页面；而 `_page_target` 允许**认领无主标签页**（本意是复用初始的 `about:blank`），结果第一个动浏览器的窗口可能认领到一个**别人上次留下的页面**，它读到的"当前页面"根本不是自己开的。修法：启动就绪后清空遗留标签页并新建一页空白（`_fresh_target`），让认领机制只可能在空白页上生效。修正后启动只剩一页 `about:blank`（修正前是恢复出来的 `Example Domain`）。
      - 顺带把复核方式也记上：**这三处都不是靠读代码发现的，是靠"重启后照常理问一句"发现的**——`status()` 报了个不可能的 11，才顺出后面两条。改动不大，但每一条都在"报一个不存在的状态"，比崩溃更难察觉。
- [ ] **聊天记录压缩 / 合并转发查看手段**（20:08 设计；20:30 草籽「可以调查下」；20:45 只读核实三件事）：
  - ①**消息存储确实保留窗口外内容**：`chatlog` 按窗口/月分文件全量落盘；`chat._selected_events` 在内存 `history`（`MAX_LEN = 256`）不够时会回 `history.getlog(..., limit=…)` 读文件。所以"窗口 ≠ 档案"**已经有先例**，压缩不必发明新存储。
  - ②**`condense_ops` 的原文确实落盘**：`data/storage/oplog/<kind>_<id>.json`，写入**存全量、不截断**（`DISPLAY_CHARS=200` 只管 `#ops` 给人看的那份）。但它**没有自己的保留期**：`oplog.sweep` 的根是"聊天窗口还能回溯到的最早时刻"，再顺着 `cids` 引用做可达性回收。
  - **②的推论最重要**：压缩**恰恰是把消息移出窗口**，而同一个窗口边界现在同时是 oplog 的回收门槛 —— 照抄这个形状，"压缩档案"会被自己的回收规则删掉。要做得让档案的根**独立于窗口**（就是我 20:07 说的第⑤条）。另：`sweep` 按**整轮**回收、不能留半轮（否则 `assistant(tool_calls)` 与 `tool` 配不上，供应商直接拒），这条约束对压缩同样成立。
  - ③**合并转发在 metadata 里就只是那串 CQ**，没有内层 id 可捞（同上面的合并转发条）。
- **（20:30 草籽）提交口径**：`docs/working/kouzi-memo.md` **不用提交**；工作区里其余那 20 改 1 删 6 增**先放着**。

## 三、反复出现的主题
- **鬼之道**：为维护僵化概念而扭曲现实 = 偏离大道。
- **合规 vs 成本 vs 安全**：每个环节单看都「有理由」，合起来却舍本逐末。
- **自我指涉的陷阱**：系统自己定义自己的维护规则，容易验证「内部自洽」而非「与现实的对应」。

## 四、约定
- 柚子绝不做「为了让框架自洽而把真实对话掰弯」的事。
- 看到「承认风险但照走不误」的模式时，要警觉，要说出来。

## 五、待归类（还没想好放哪，先堆着）
- （暂无）

- [x] **撤掉「私聊 `user_id` 是窗口对端」这条约定（草籽 2026-09-19 点）**：草籽的意见是
  「不管谁发的、群聊还是私聊，`user_id` 都该是作者；靠加 `sender`、再在私聊把 `user_id`
  折成窗口，纯粹是在制造复杂度」。抓 `5701` 上的真实入站实测后成立：`user_id` 两个方向
  都是作者，私聊「是哪一条」由 Napcat 扩展 `target_id` 给出、两个方向都填、与方向无关
  （对端发来时它等于 `user_id`，因为它不是收件人，是那条会话的标识）；`get_msg` 回查是
  唯一丢窗口的一路，而它知道发送目的地。
  - 改动：读窗口改读 `target_id`（`history.window` / `message.target` / `message.sendmsg` /
    `context.interaction_key` / `chat.getchatstorage` / `chatlog.search_current` / `later` /
    `todo` / `mcversion` / 两个 `_resolve_window` / `op.require_op` 的提醒目的地）；
    写事件不再折（`record_sent` 补 `target_id`、`_message_record` 分开存、两个 `_event` 摆对位）。
  - 保留 `target_id or user_id` 兜底的四处：改动前存进文件的任务与订阅里，窗口写在 `user_id` 上。
  - **上面那条「保持原样的是窗口语义」到此作废**——「谁发的」与「发到哪」不再共用一个字段。
  - 取舍与实测表：`docs/working/proposals/window-identity.md`（已实现）。
  - 磁盘格式不动（行头本来就是作者、路径本来就是窗口）；notice 一族不在约定内。
  - 验证：语义四例、真实档案重建（回声 `user_id`=Bot / `target_id`=对端）、写入→反查往返各过；
    `--check` 117 文件 / 84 模块。**核心模块，需重启才生效。**
