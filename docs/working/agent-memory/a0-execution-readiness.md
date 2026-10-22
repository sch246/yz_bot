# A0真实小试执行包：官方补证与私有契约

2026-10-19，Asia/Shanghai。复用[D05](decisions/D05-裁定首轮验收取舍-样本范围和预算.md)、T01–T05和[运行单§8](a0-pilot-run-sheet.md#8-三天旧副本准备2026-10-19)，源码基准`74e38a7`。本次只核实公开官方资料、整理仓库外私有执行包，不读密钥、生产配置或新增聊天，不请求模型或扣费。三天原副本、身份及无模型验收沿用T04/T05，不重新选样本或重跑全部校准。

## 本次结论

官方文档支持既定Flash调用方式，人民币价目与现有容量预留政策一致。`1048576`的精确数字仍来自官方/models文档示例；模型价目页只写1M。本次没有认证查询/models或实测容量。一般失败、取消和断流的模型计费承诺仍未找到，必须保留未知，不能填成免费。

私有执行包冻结同一个pilot、输入引用、代码指纹、契约提案、未来命令和凭据准备步骤。契约提案保留文档容量证据状态，未冒充执行者已经作出目标适用声明；现有live验证器会拒绝该提案。未来取得具体执行授权后，再由执行者将已披露的容量依据写入单独的生效契约，不能抹掉提案或伪造真实/models证据。凭据和生效契约尚未提供，本次没有可直接启动的收费运行。

## 官方证据与适用范围

本次两名只读子代理分别核查容量/协议和计价/失败；主代理复核价目、模型schema、思考模式、错误码、保活和首次调用入口。检索为北京时间2026-10-19 17:47起。下表区分网页声明与尚未观测到的服务事实。

| 项目 | 官方声明与本包处理 |
|---|---|
| 模型与端点 | [首次调用](https://api-docs.deepseek.com/quick_start/)及[人民币价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)列明OpenAI格式端点`https://api.deepseek.com`、模型名`deepseek-flash`，当前对应DeepSeek-V4.1-Flash-1017。沿用项目selection `deepseek/deepseek-flash`，不核查生产路由。 |
| 容量 | [Models schema与示例](https://api-docs.deepseek.com/api/list-models/)定义context_window合计输入与输出；Flash示例1048576、max_output_tokens393216。[价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)只写1M/384K。1048576作为文档支持的候选容量假设，不是本账号已返回的元数据。 |
| 请求参数 | [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)支持thinking enabled/high、tool_choice auto、stream及include_usage；max_tokens4096在声明范围内，输入与生成合计受context约束。保持T03/T05冻结实参，不切换API或能力。 |
| 思考与usage | [思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)要求携带tools时回传此前reasoning_content；[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)将reasoning_tokens列在completion细分项。按此推断4096限制生成completion整体，不能承诺4096个最终答案token；思考不在completion之外重复计费。流末内容块携带usage的接收证据已由T02/T03保留。 |
| 别名与指纹 | [10月17日更新](https://api-docs.deepseek.com/updates/)说明Flash别名跟随最新模型。响应model和system_fingerprint只记录实际观测，不锁权重。本包预期响应model为文档示例的deepseek-flash；实际若不同，现有入口按漂移停止，不能自动接受新别名。 |
| 凭据入口 | [首次调用](https://api-docs.deepseek.com/quick_start/)链接到[官方API key管理入口](https://platform.deepseek.com/api_keys)。未登录、未检查账号配额或余额。本包只准备显式私有文件路径及人工填入步骤。 |

## 价目与费用条件

[官方人民币价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)当前每百万token如下；它保留改价权。[9月10日公告](https://api-docs.deepseek.com/news/news260910/)给出的该次调整生效点为04:00 UTC，即北京时间12:00，不是本包未来价格保证。

| 类别 | 空闲 | 高峰 | 本包预留/折算 |
|---|---:|---:|---:|
| 输入缓存未命中 | 1元 | 2元 | 2元 |
| 输入缓存命中 | 0.02元 | 0.04元 | 0.04元；预留全部按未命中 |
| 输出（包括completion中的思考） | 4元 | 8元 | 8元 |

价目定义的高峰为北京时间周一至周五、非中国法定节假日的09:00–12:00和14:00–18:00。执行包统一取高峰价，避免用2023年虚拟时钟、节假日判断或跨时段归属来降低预留；未查到跨峰谷请求按起止哪一刻计价的明文。保守折算与供应商实际账单分列。[缓存说明](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)不保证命中；usage中prompt已经包含缓存，不能重复相加。

在1048576容量假设及现有接口约束成立时，沿用每次预留：

`(1048576 × 2 + 4096 × 8) / 1000000 = 2.129920元`

容量本含输出，两项各取最大有意多预留，不称恰好最大账单。两份预留4.259840元；第三份累计6.389760元，被5元守卫拒绝。合法usage成功持久结算后可释放差额继续，仍最多12次HTTP尝试、3次日末唤醒、每次4096输出；不是固定只能请求两次，也不保证完成三天。5元仍是该次应用预算，不是供应商绝不超报的保证。

| 尚缺证据 | 已查官方资料及本包处理 |
|---|---|
| 4xx/5xx、超context的收费或退款 | [错误码](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes/)只列原因和处理，未找到模型失败免费承诺。任何实际尝试仍先占预留，错误停止，不照文档建议自动重试。 |
| 断流、客户端取消、usage缺失 | [保活说明](https://api-docs.deepseek.com/quick_start/rate_limit/)写明十分钟未开始推理会关闭连接，未说明收费；未找到客户端取消后是否继续生成的承诺。有合法usage依规则结算，未知保留全额预留并停止，实际费用写未知。 |
| 其它计费能力 | [Search/Fetch指南](https://api-docs.deepseek.com/zh-cn/guides/search/)的成功请求/失败免计费规则只属独立API，不能外推模型。本小试不启用Search/Fetch、图像、前置、图书管理员、judge或模型子任务。 |
| 供应商消费硬顶 | 本次未核验账号或每key硬限额，不新增必须具备该能力的要求；应用约束和上述未知均向执行者披露。 |

## 私有交付与未来执行步骤

仓库外独立0700执行包只保存本次运行元数据。文件名如下；实际输入/输出路径只在私有包内，不写进公开票据。

- `contract.proposed.json`：现有schema的live模式契约提案，含文档来源、时点、容量假设、失败计费披露及代码指纹。capacity_evidence.status保持documentation-example-not-live-model-query，不写成已查询真实/models。
- `execution-manifest.review.json`：将提案摘要、三天原manifest和identity文件摘要、T04脱敏结果、唯一pilot及全部命令参数绑定；标明未执行、无凭据与无收费授权。它不另存任务状态，票据仍为权威。
- `run-command.txt`及`run-argv.json`：相同的一条未来run命令，使用生效契约的预留路径、同一个pilot/root/permanent record及显式credential-file；只作审阅，不执行。生效契约、凭据、运行输出与永久占位本次均不创建。
- `credential-steps.md`：人工从官方平台取得key后，以本地隐藏输入写入owner-only文件的具体命令；本次不运行，不读取环境变量、生产config/.env或搜索旧key，不把key放进聊天、argv或记录其hash。
- `validation.json`：本次纯契约/命令/路径检查结果；不构造SDK、读取凭据或调用run。

未来具体执行先复核公开价目/容量依据、代码和输入摘要，保留提案，再按明确授权生成单独的`contract.approved.json`，设置现有代码要求的执行者适用声明；声明必须继续说明1048576来自公开示例、实际/models未查询，以及失败计费未知。它只是声明采用该条件，不是把研究结果升级为服务端实测。随后准备指定私有凭据，检查同一pilot的输出和永久占位均不存在，再执行已审阅命令；不能为了避开占位换目录或自动重跑。

`--authorize-materials --confirm-paid`会进入现有真实入口，传输获准的三天材料与必要身份信息给官方服务。本次只把标志写进未来命令，没有执行，也没有保存维护者已同意付费或传输材料的虚假答案。首个真实模型请求本身计入5元小试，不增加“免费探针”。实际服务接受、响应身份、usage、耗时和费用待那次请求留证。

D05继续保留完整四臂参数/预算待实测，D10依赖不变。本次没有新产品取舍；尚缺执行者适用声明、显式凭据与该次真实执行授权，不重问既有样本、供应商或人工投入。
