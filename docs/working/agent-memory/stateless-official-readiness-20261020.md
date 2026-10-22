# DeepSeek 非流式公开合同补证

核查区间：2026-10-20 15:13:45–15:15:35 UTC（工具实时时钟；文档网页读取）。仅访问官方公开文档；未调用 api.deepseek.com 的任何API、账户或模型，未读取凭据、生产、私有执行包。按 research skill 定向补证，不改变票状态、供应商、正式预算或生产权限。

## 来源与本次观察

下列 L 为本次 web 工具提取文本的行号，便于重定位，非服务器版本号。网页当前内容不等于实际账户／目标请求验证。

| 官方页 | 本次观察／官方断言 |
|---|---|
| [首次调用](https://api-docs.deepseek.com/quick_start/pricing) L24–33、43–81 | 此无尾斜杠英文URL实际返回首次调用页，并非价格表。OpenAI兼容base为 https://api.deepseek.com；模型名deepseek-flash，目前由DeepSeek-V4.1-Flash-1017服务。官方Python例直接组合stream=False、reasoning_effort=high、extra_body.thinking.type=enabled；HTTP例为POST /chat/completions。deepseek/deepseek-flash是仓库内部provider选择标识，非该例wire模型名。 |
| [模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) L29–55、74–78 | Flash表格声明上下文1M、最大输出384K、支持Tool Calls；缓存命中／未命中输入／输出按百万tokens高峰价0.04／2／8元，空闲减半。高峰为北京时间工作日（除中国法定节假日）9–12、14–18；价格可变。原文短引：“模型调用费用 = token 消耗量 × 模型单价”。当前历史保守峰值候选数值相符；不是本次账单验证。 |
| [Lists Models](https://api-docs.deepseek.com/api/list-models/) L73–79、157–199 | schema将context_window定义为输入加输出总容量；max_output_tokens为服务器接受的max_tokens最大值。短引：“counting both input and output tokens”。展示的Flash JSON example为1048576／393216；**精确整数位于Example，不是本次GET /models结果**。非示例的价格表只写1M／384K。 |
| [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) L250–281、318–327、391–413、662–718 | model接受deepseek-flash；thinking enabled与effort high有文档支持；max_tokens范围1–393216，输入加生成受context约束，故4096合法。stream_options仅配stream=true，否则400；strict是Beta；thinking下tool_choice auto可用，required／指定函数不支持。响应文档有model／system_fingerprint及usage；prompt=hit+miss，total=prompt+completion，cached_tokens对应hit，reasoning_tokens列为completion细分。短引：“The maximum number of tokens that can be generated”。 |
| [Token usage](https://api-docs.deepseek.com/quick_start/token_usage/) L24–30 | 字符换算仅约数，实际tokens以返回usage为准；bytes+1024不能当真实token上界。 |
| [错误码](https://api-docs.deepseek.com/quick_start/error_codes/) L25–38 | 说明400/401/402/422/429/500/503；500/503建议稍后重试，但未在该页承诺失败免费或重试幂等。 |
| [限速与隔离](https://api-docs.deepseek.com/quick_start/rate_limit/) L89–97 | 非流式等待时可返回空行保活，JSON解析须容忍；10分钟未开始推理服务器断连。不是生成最长时限或失败计费保证。 |

[FAQ入口](https://api-docs.deepseek.com/faq/)本次跳向[官方静态FAQ](https://static.deepseek.com/faq/index.html?lang=en)，工具返回无可提取正文；因此未从其确认任何计费条款。

## 与仓库历史候选的差异／适用边界

- **公开支持的请求形状**：官方固定route、wire deepseek-flash、非流式、thinking enabled、high、4096输出和auto工具选择仍相符。不添加strict-beta、search/fetch、视觉或其它能力；当前价目另列Search/Fetch按请求收费（中文价格页L60–76），不能以token峰值包住未授权新能力。
- **精确容量证据仍分层**：公开模型表支持1M上下文；1048576来自官方示例，其字段语义明确，但本次未重新验证实际目标metadata。不能把这份文件写成“本次官方GET已确认1048576”。此前已有目标metadata证据可由主代理核其版本／适用性；本研究没有读取私有原件。
- **推断**：按历史1048576上下文加4096 allowance计保守token预留、用峰值2/0.04/8计价，是应用侧保守策略；模型合同的实际联合输入+输出上限仍为context。不能将1052672预留量称作服务器允许的上下文，也不能保证供应商绝不超报。
- **响应身份限制**：公开文档定义fingerprint为backend配置；未承诺整个实验期间恒定，亦未承诺model响应字段永远等于wire别名。严格一致拒绝可作为应用保守停止策略，不能说这是官方不变性保证。
- **usage限制**：公开结构支持当前prompt/cache/total一致性检查，但未知／缺失／显式null的保守拒绝属于应用政策；本次未证明所有实际SDK/provider响应形状均被现有normalizer覆盖。

## 明确unknown与T16实际影响

本次可读的定向官方来源未找到：失败HTTP、断线、客户端取消／timeout、未收usage的退款或零计费保证；重试请求去重／幂等免费保证；服务端取消完成确认。错误码页的重试建议不能补这些保证。保留failure_billing未知；应用应无自动重试，未知usage留全额预留并闭锁，合法usage先计费再停。这是保守工程政策，不是供应商计费证明。

主代理需裁定／落实的工程影响：T16合同应把本次公开复核与历史目标metadata分别绑定，写明精确容量适用性证据；冻结route/model/request、源码与manifest，真实执行前完成唯一运行及阶段预算预占。无需据本补证重新让维护者选择厂商／兴趣／正式规模；本次补证没有实施live构造、取key或付费执行；后续代码准备与实际执行分别沿原票及既有授权处理。若旧证据不足以支持精确目标容量，应显式保留该缺口，不能默默把Example升级成实测保证。
