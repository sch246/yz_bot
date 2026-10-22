---
{
  "id": "T05",
  "kind": "implementation",
  "status": "done",
  "owner": null,
  "blocked_by": [
    "T03"
  ],
  "invalidated_by": [],
  "title": "兼容旧三天prepared材料并做无模型A0校准"
}
---
## 来源、授权与目标

维护者要求继续既有无模型实验工作，并已明确授权仅定位/准备原主窗口2023-03-14至16日及必要身份映射、不调用模型。T04已原样私有复制旧prepared并核对hash，共8716条；在必要元数据范围内，24份同输入摘要旧实验清单的身份摘要与选定三天候选均匹配同一组窗口/Bot身份。未读生产配置/凭据、其它日期或旧模型正文。

实际材料超出T03声明的最小格式：8490普通v0、132撤回、89 poke、5不透明notice；普通消息4263个负号、8个重号实例和1条缺号；含57处CRLF；旧prompt_mode与当前v3不同。不能删消息、删notice、改正号、造缺号或重写旧manifest来凑通过。T03现有范围内证据不失效，本票是完成已授权输入准备/核验所需的实验兼容补件，不扩成四臂或生产循环。

来源：[T04](T04-准备三天A0私有输入并离线核验.md)、[T03](T03-实施A0容量预留与小试入口的无模型验收.md)、[D07](../decisions/D07-确定画面-位置与行动日志如何接管旧职责.md)、[运行单](../a0-pilot-run-sheet.md)。当前源码2d6f031。本票只运行合成/MockTransport，不给worker真实包、身份或历史输出，不执行真实run/收费。

## 所有权与交付

worker在主代理指定独立worktree修改experiments/a0_pilot.py及README；必要时最小修改a0_calibration.py的候选回声分配/隔离接缝，保持T01/T02默认封闭及既有行为。不改mods、价格/额度/ledger或任务文档。主代理独占真实私有包、helper、地图、审查与集成。

按完整原始记录保留旧包导入/验证，能够对所选三日只做load/verify/preview而不构造SDK或触发模型/动作；显式材料授权先于读取，stdout只脱敏统计，private report/source map留私有目录。旧prepared模式标签是输入来源历史，本次实际prompt仍由A0冻结，二者分开，不把旧整段任务加进当前A0。

复用当前纯解析、源origin与正式阅读机制，完成最小A0日末开放/历史回查/候选回声的材料兼容；不能只放宽loader而让open_day仍把notice写成消息。保持本次A0原有过滤和归档可查职责。对缺失/不可还原字段显式保留标记与原文，不能从本地化notice推断不存在的原生事件字段。若源码证据不能确定一种记录如何保持当前A0行为，先报主代理事实/影响，不自行扩大产品语义。

## 验收

- 旧manifest和全部源bytes/hash保持，旧模式标签与实际运行prompt分别记录；只接受明确支持的旧schema/mode，未知漂移拒绝。按原始LF行位置计算byte范围并覆盖CRLF，不先全包normalize。
- 普通有号/负号/重号/缺号及recall/poke/opaque notice全部保留，记录级来源映射和时间截止可机械核查；不得把撤回引用号当新的消息身份。消息号只作辅助查询，原始origin稳定且与窗口/时间消歧，缺字段不补造。
- 默认模型可见行为沿当前A0的消息/相关poke/撤回和控制行过滤，不把所有notice直接升级为可见消息；原文在archive仍可查。对无法等同在线原生事件的归档投影明确声明和实证，不声称重建当年实时触发。
- 候选回声标记sent=false、沿当前隔离单分支行为，不与任一历史正/负/重号碰撞，不修改源记录。跨三次drive、同秒、来源回查和所有读取/目录/名字的未来不可见继续通过。
- 增加严格no-model材料检查入口及合成故障探针；真实run保持授权/固定契约/5元与12尝试等T03边界，异常仍禁止工具、后续唤醒和隐性重试，不因格式兼容扩大能力。
- 默认doctor仍不接受真实路径；新合成fixtures至少覆盖CRLF、负号与重复号、缺号、撤回引用同号、poke/opaque、控制行和历史Bot、source查回及echo。worker不能读取真实材料来调fixture。
- 新doctor及受影响旧doctor通过；Python改动须check，主代理集成smoke与83/85（mcf/minecraft）基线比较。无永久测试框架；公开日志不含真实正文/账号/密钥。
- 主代理集成后，对T04私有副本只执行纯load/verify/preview，产出脱敏统计及完整来源校验结果；不执行真实A0模型run、SDK/网络或收费。若仍有真实不支持，明确报告而不声称就绪。

交付commit SHA、实际命令/断言、候选/归档旧职责接管、剩余语义/服务接受/计费边界。必要维护者选择由主代理统一提问并helper保存实际回答；实现细节不转嫁为产品选择。

### 2026-10-19T09:21:15+00:00 · draft → ready

承接维护者继续既有无模型实验及仅准备三天输入的授权，补齐核验所需实验导入兼容；worker仅合成fixture，主代理私有材料纯load/verify，不改生产或付费，不扩大材料范围。

### 2026-10-19T09:21:15+00:00 · claim

Owner: codex:root:a0-legacy-input-20261019


## 2026-10-19 · worker交付，进入主代理审查

独立worktree已提交`acd173c31542e7a72ea93ababe15734f46ec6ce6`，仅修改experiments/a0_pilot.py、a0_calibration.py及README；worker已停止写入、工作树干净。主代理已核对diff与关键出处，另由只读代理核验CR/notice/origin/preview路径，未发现限定范围内阻塞缺陷。

worker最终doctor85项通过、56次fake HTTP与结算尝试，哨兵均0；证据`/tmp/a0-t05-doctor.json`及`/tmp/bot-a0-pilot-7ixbt7xh/doctor.json`。覆盖三drive、原bytes及字段、CRLF头字符串ID、缺号/重号原记录的正式read_messages(origin)回查、过滤与候选号不碰撞。旧T01 57项、T02 54项/49次mock通过；check125文件/85模块、两源compile及README链接/diff检查通过。worktree文件数与主树有无未跟踪文档不同，主树仍将对照127/85及smoke83/85基线验证。

纯CLI preview合成探针为0 SDK/model/action，证据`/tmp/bot-a0-t05-preview-probe-b931au78`。此时尚未集成或对真实副本执行新preview，不能提前写完成。

### 2026-10-19T09:37:55+00:00 · update

保存worker提交acd173c及合成/回归证据，限定语义只读复核通过；进入主代理集成和私有纯preview验收，尚不完成。

### 2026-10-19T09:37:55+00:00 · active → review

worker停止写入、提交干净；diff和关键路径审查通过，主代理接管集成验证。

### 2026-10-19T09:37:55+00:00 · claim

Owner: codex:root:t05-integration-20261019

## 2026-10-19 · 主代理审查、集成与真实材料纯校验

worker提交经diff/关键出处审查和独立只读核验后，集成为`74e38a7141e752febd6c21ef03578f6793852dab`。仅experiments三文件，未改mods、容量政策、ledger或T02客户端。默认doctor仍只用合成输入；真实run的材料/付费/凭据/契约和5元/12尝试边界未扩大。具体职责接管为：严格格式拒绝改为有界旧格式导入；归档不再重写成普通消息，而是逐原片段追加并核对原bytes/解析字段；回声号从全选范围的消息/引用号以下分配，源记录不改号。

主树实际执行`uv run --frozen python experiments/a0_pilot.py doctor`：85项通过，56次假HTTP与56次结算尝试，socket/QQ/external_write/real_HTTP均0。脱敏入口证据`/tmp/agent-memory-t05-main-doctor.json`，完整合成证据`/tmp/bot-a0-pilot-vzsper1o/doctor.json`。旧T01/T02回归分别57项、54项/49次mock，已核验worker输出；证据`/tmp/a0-t05-old-doctor.json`、`/tmp/a0-t05-sdk-doctor.json`。主树check127文件/85模块，smoke83/85、仅mcf/minecraft可选失败，均与同一开工基线相符；输出`/tmp/agent-memory-t05-main-check.txt`、`/tmp/agent-memory-t05-main-smoke.txt`。

合成证据覆盖：原清单逐字节保存、旧prompt标签与实际A0 prompt分列；内部CR/CRLF/尾空白及CRLF头字符串ID不被规范化；负/重/缺号、撤回引用、poke、opaque与控制过滤；三drive与所有读取截止；候选回声后实际read_messages(origin)回查缺号与被range索引隐藏的同号同秒原记录；原生工具结果配对。旧归档recall/opaque连read_messages(origin)也仍被模型过滤，仅底层chatlog归档读取保留结构与原文，不冒充实时撤回重建或新raw模型通道。

主代理随后只对T04私有三天副本执行load/verify/preview，并与当前chatlog纯解析逐条比较8716条、逐日重组原bytes；全部相同，原副本/新副本的manifest与日档hash保持，完整source-map仅留私有目录。已授权预览未构造SDK/请求/动作；网络、模型、客户端、动作及生产文件哨兵尝试均0，费用0。公开脱敏报告`/tmp/agent-memory-t04-final-preview-summary.json`（SHA-256 `b40b7b9b959ab6a73bcd586a68b28fe081eb092fc1157413e094b4c23ac7ae86`）；受控仓库外脚本`/tmp/agent-memory-t04-final-preview.py`（SHA-256 `3119c7f1510ba46b02660d4ffd689208796064509124470be41e2840d8318525`）。没有执行真实run或把完整私有数据交给worker。

本票验收已满足，T04可继续关闭材料准备。仅证明材料兼容与无模型隔离，未证明服务接受、实际计费、四臂收益、实时事件/到达顺序或生产迁移；sidecar/私聊/其它日期、未知格式、部分恢复/fork仍不支持。D05完整预算、D10及真实收费许可均未因此完成。

### 2026-10-19T09:40:21+00:00 · update

保存集成74e38a7、主树85项/56fake、check/smoke同基线及真实私有副本8716条纯preview通过；原bytes/字段未改，调用费用0。

### 2026-10-19T09:40:21+00:00 · review → done

独立worktree交付已审查集成，受影响回归/check/smoke通过，主代理真实私有副本仅load/verify/preview完成；生产/模型/付费未触及。
