# A0 官方供应商契约与离线补件

核实日期：2026-10-19。当前代码基准 `db3780e`。来源为已追踪源码和公开官方文档；未读取本机连接配置、凭据、真实材料或历史模型输出，未调用模型。

维护者已选择后续A0小试固定DeepSeek官方直连。此选择确定供应商契约，没有授权真实请求或收费。完整答案与历史归[D05](decisions/D05-裁定首轮验收取舍-样本范围和预算.md)；新无模型补件范围见[SDK离线范围](a0-sdk-offline-spec.md)和[T02](tickets/T02-实施A0真实SDK与假HTTP传输校准.md)。

## 模型身份与请求边界

`deepseek/deepseek-flash`是项目的provider/model选择字符串，前半选择配置键，后半原样发送；它不能证明当前生产连接到哪里。出处：`mods/llm/models.py:48–87`、`mods/llm/__init__.py:268–277,506–525`。后续实验另固定官方接口，不更改或核实生产路由。

官方人民币页面列出的当前版本为DeepSeek-V4.1-Flash-1017；调用名仍为`deepseek-flash`。[价表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)与[10月17日更新](https://api-docs.deepseek.com/updates/)相互支持这一对应。它是会更新的调用别名，不能据名称不变声称权重版本固定。未来须冻结核实时点与文档指纹，记录响应model/system_fingerprint；发现版本漂移应停止配对比较，响应指纹也不等于不可变权重证明。

采用官方Chat Completions契约。参数明确冻结4096输出、thinking enabled/high、auto工具选择；默认值也显式记录。标准接口不误用beta strict，不加入服务端Search/Fetch、图像或新能力。带tools的后续请求须保留既有assistant思考内容；原生工具调用和结果配对另作实际body检查。来源：[请求接口](https://api-docs.deepseek.com/api/create-chat-completion/)、[工具指南](https://api-docs.deepseek.com/guides/tool_calls/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。这是待校准契约，不是已通过真实服务验证。

## 人民币快照与可复算候选

下表按每百万token，不能直接写回生产默认配置。未来启动仍要重新核实价格与适用时段；价表保留变动权。

| 类别 | 空闲时段 | 高峰时段 | 请求前保守取值 |
|---|---:|---:|---:|
| 输入未命中缓存 | 1元 | 2元 | 2元 |
| 输入命中缓存 | 0.02元 | 0.04元 | 0.04元 |
| 输出 | 4元 | 8元 | 8元 |

来源：[官方人民币价表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。为本小试建议统一按高峰价预留，避免虚拟聊天日期或节假日判断影响资金守卫；实际usage按真实计量时刻和冻结规则对账。

纯算术建议：每请求可信输入预留不超过180000，输出最多4096，12次的高峰、无缓存上界为：

`12 × (180000 × 2 + 4096 × 8) / 1000000 = 4.713216元`

若照搬T01合成输入上限262144，同式为6.684672元，不能声称12次全额预留均能装进5元。这不修改T01：其参数是合成接口，不是真实报价。18万只是未来pilot的技术建议，未实际截断输入或发请求；若完整输入不适配就停止，不删原文/schema救活，也不保证到达全部唤醒。

额外收费必须另核实。本阶段没有Search/Fetch能力；官方页面另列其按次计费及生效安排，因此不能把有服务端工具的未来请求仍套成纯token费用。未知额外项目拒绝启动，不能默认0。

## 计量仍有边界

官方说明上下文为1M，输入与输出合计受上下文约束，输出最大393216；4096是本小试限额。T01的输入/输出独立合成接口不能直接代替这个关系。来源：[价表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[max_tokens定义](https://api-docs.deepseek.com/api/create-chat-completion/)。

官方token指南给的是字符估计比例并链接离线tokenizer，最终用量以usage为准；这些不足以证明完整Chat Completions消息、工具schema与思考历史的严格请求前上界。因此`完整JSON UTF-8字节数+1024`在本阶段仍只作假接口预留政策；没有把它升为真实DeepSeek计数证明。[Token指南](https://api-docs.deepseek.com/quick_start/token_usage/)

原始usage须在parser可能抛length前保存；prompt总数已含缓存，两个缓存字段为同一分项，reasoning是completion分项，均不重复加总。最后SSE块可同时有非空choices、finish_reason与usage，不能只收独立usage块。来源：[usage与流式定义](https://api-docs.deepseek.com/api/create-chat-completion/)。无usage全预留、冲突/超报闭锁，计费时钟与模型虚拟钟分开。

## SDK假传输切片

现有生产接缝的具体缺口：request_policy尚非最终body；正常yield的usage不能覆盖length早抛；SDK构造未禁重试。出处：`mods/llm/__init__.py:268–277,523–535,550–611,625–650`。T01直接绕过SDK，不能证明这些点。

[SDK官方说明](https://github.com/openai/openai-python#retries)指出默认对部分连接、429和服务端错误重试，`max_retries=0`才关闭。T02在真实SDK最终HTTP边界使用封闭假transport核对实际尝试数、完整body和异常usage，并缓冲至结算成功再交工具。冻结实际安装SDK版本；不能因为最新文档的依赖形状改变就擅自升级本仓库。

此补件无需生产改造或真实凭据。后续真实pilot仍缺明确冻结材料及读取范围、可信完整输入上界、最终请求/接口相容证据、凭据供应方式和具体收费运行授权；完整四臂预算继续待实测。它们没有由供应商选择或当前“继续”自动补成已获准。

后续[投影与预留补证](a0-projection-and-input-bound.md)已核对当前中心A0中False/True实参的实际等效范围，并静态审查官方tokenizer的字段缺口；另提出按官方总上下文容量作粗预留的条件方案。该方案不需要声称本地tokenizer精确，但模型适用容量和失败计费边界仍须明确，不能从文档示例直接宣布真实pilot已就绪。
