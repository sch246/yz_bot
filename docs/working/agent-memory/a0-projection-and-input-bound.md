# A0 历史投影与请求前预留补证

2026-10-19，源码基准`c5a04439f6a9186d6020269f2b4e886296d60d4a`。承接[D05](decisions/D05-裁定首轮验收取舍-样本范围和预算.md)、[T02](tickets/T02-实施A0真实SDK与假HTTP传输校准.md)和[供应商研究](a0-provider-readiness.md)。本轮只做源码/公开资料研究、仓库外合成探针与纯算术；未读生产材料、配置或凭据，未调用模型、扣费或修改生产代码。

## 1. 订正：布尔实参不等于实际思考投影

上一轮把T01的`persist_reasoning=False`与T02的`True`列为尚待处理的历史投影差异，证据不足。当前中心A0路径会同时给`oplog.output`传`model=session.model`；有model标签且assistant含reasoning字段时，False也保存它。出处：`mods/chat/__init__.py:319–324`、`mods/oplog/__init__.py:849–852`。

真正决定请求形状的还有`reasoning`设置、同模型/协议标签、原生配对是否完整、固定起点与cover等。源码对缺省/无效reasoning值归一化为`keep`（`chat/__init__.py:77–83`），并据此打开`keep_reasoning/preserve_native`（`:264–266`）；这不证明生产存储当前值。重建与投影见`chat/agent.py:157–162`、`chat/view.py:188–235,353–355`，同轮处理见`llm/__init__.py:743–758,806–813`。

主代理用T02的真实SDK/MockTransport及真实A0 drive做定向反证：分别在normal、plain-third两个场景中将实际`run_offline`实参改为False/True，每种运行两次drive。每对六份完整最终HTTP body逐项相等；旧/新工具reasoning、结果配对及无工具assistant思考均通过原fixture断言。共24次mock尝试、24次结算、socket哨兵0。

- 命令：`uv run --frozen python /tmp/agent-memory-a0-projection-probe.py`
- 证据：`/tmp/bot-a0-projection-jzdk8lib/probe.json`；脚本SHA-256为`62e321e1d91a889ed1d9475281815d64a1a905a6ec07137def9f4afffd43f2d3`。
- 探针明确记录实际覆盖实参；复用类的原manifest仍描述默认True，不能把该manifest冒充正式False运行清单。

结论只限当前中心、keep、同model原生路径及这些fixture；不外推无model输出、drop、异模型、旧格式或不完整配对。T01脚本响应原本不含reasoning，不能单靠其通过证明两值等效。这里的反证纠正原推断，没有证明真实官方服务接受性。

## 2. 正式A0的冻结候选

沿用现有reader和源码缺省keep作为A0候选，无需为兼容而新选drop模式。下表是未来manifest须明确的内容，不是已生成的真实运行清单。

| 项目 | 候选与验证责任 |
|---|---|
| 当前循环 | 原reader/base prompt、固定起点、正式阅读、cover/recall及原工具状态；冻结commit、必要diff、prompt/schema/能力hash |
| 思考与原生历史 | 显式`reasoning_mode=keep`、`keep_reasoning=true`、`preserve_native=true`；`persist_reasoning=false`沿用T01实参，另记录实际wire形状，不能以该布尔值命名策略 |
| 模型与接口 | 官方Chat Completions、selection=`deepseek/deepseek-flash`、wire=`deepseek-flash`，thinking enabled/high、auto工具、4096输出；冻结版本依据与参数，不读生产路由 |
| 历史边界 | 同模型/协议标签、完整assistant/tool配对、原生块降为文本及隐藏/cover规则均照现有机制；不承诺保留被原reader合法省略的全部历史 |
| 新循环 | 各臂另冻结请求投影；跨唤醒不保留思考沿[无状态提案§6的D4](../proposals/stateless-agent-loop.md#6-维护者的裁定2026-10-19)，同次唤醒内仍满足工具协议 |

上轮文案把“提案D4”简称为“D04”，容易与本目录的[D04行为研究票](decisions/D04-把既有记忆目标连接到可观察行为和证据.md)混淆；后续使用完整引用。未发现需要重开维护者方向裁定的新反证。

## 3. 官方tokenizer仍缺什么

[官方Token指南](https://api-docs.deepseek.com/quick_start/token_usage/)提供文本计数demo，并说明实际usage为准，没有给完整API输入上界契约。只读子代理在内存检查官方链接的[公开压缩包](https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip)，未执行包内代码：

- HTTP Last-Modified为2026-08-28，1,911,504字节；SHA-256为`e7310d1dafe0a86d8a5629fe78a7c763760f651db9b8682718a1781dcd6fe495`。
- 配置的`model_max_length=16384`，类名`LlamaTokenizerFast`；demo只编码文本。模板处理消息/历史tool calls，但未引用顶层tools schema或reasoning_content。
- 包本身未绑定10月17日的Flash版本；这不足以证明兼容或不兼容。版本更新依据见[官方更新](https://api-docs.deepseek.com/updates/)。

因此不执行demo来包装“完整输入已计量”，也不把`JSON字节+1024`升格为真token上界。少量以后获准的usage对齐能发现反例，不能单独证明所有请求的上界。

## 4. 可研究的替代：按官方上下文容量粗预留

[Models文档](https://api-docs.deepseek.com/api/list-models/)把`context_window`定义为输入与输出合计容量，Flash示例为1048576；[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)也规定输入与生成总长受上下文限制。这支持研究粗预留：在目标模型确实适用该容量、供应商遵守契约的已接纳请求中，输入不超过1048576，输出不超过4096。两者同时取最大值是有意多预留。

但1048576目前取自公开示例，未访问真实`/models`；不能冒充已核实账户/模型元数据。未来须核实目标模型的适用容量及价格，冻结时点/来源。按[官方人民币价表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)本日高峰数字，条件算术为：

`R = (1048576 × 2 + 4096 × 8) / 1000000 = 2.12992元/请求`

| 情形 | 5元规划内的条件结果 |
|---|---|
| 每次都消耗全部预留 | 两次4.25984元；第三次预留前拒绝，三次需要6.38976元 |
| 已知usage远小于预留 | 成功结算后释放差额，可继续预留，仍受12次候选调用上限约束；不能误写为“固定最多两次” |
| 错误或断流 | 均闭锁；已取得合法usage则据其结算，缺usage保留全额预留，不追加修补调用 |
| 价目/容量漂移、额外能力或供应商超报 | 不自动换价/增margin/重试；拒绝或记录实际异常并停 |

纯算术证据：`/tmp/agent-memory-a0-cap-arithmetic.json`。两个最坏预留后第三次被拒；沿T02合成usage的282微元结算，12次可持续预留，实际合成合计3384微元。这是余额规则的反例检查，不是已实现的live费用守卫或真实费用预测。

公开[错误码说明](https://api-docs.deepseek.com/quick_start/error_codes/)未明确超context/拒绝请求的计费边界；本次未找到模型失败请求全部免费的承诺。Search/Fetch单独按次收费的失败规则不能移用于模型。以上算术只覆盖其声明的契约条件，不能保证供应商账单绝不超5元；这与[D09费用边界](decisions/D09-确定比较器可复用部分与最小离线补齐范围.md#7-费用估算可复算上限不编当前报价)一致，不新增绝对不超报的承诺。

## 5. 接续与实际停点

本轮可确定：保留A0源码缺省keep的候选，冻结实际投影而非布尔标签；不把公开tokenizer当完整计量依据；保留“官方容量粗预留”作为有条件替代，18万/4.713216元仍是较紧但未获输入上界证明的旧候选。二者均未成为实付参数。

后续若做无模型补件，最小范围是：manifest实参与投影一致、声明容量来源的预留政策、余额释放/缺usage闭锁/漂移拒绝的合成校准；T01/T02现有byte政策不等于这套真实费用适配。不需要改生产代码或实现四臂。

真实小试仍未获材料/凭据/收费运行授权；启动前须把具体冻结材料、实际适用的供应商容量/价目、失败计费边界与运行范围整理成可审阅清单。若要求独立于供应商遵约的绝对费用硬顶，须有相应供应商侧约束，应用事后停机不能提供它。D05/D10保持未完成；完整四臂预算与收益依然未测。当前没有需要维护者重新选择的方向问题。
