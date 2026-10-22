---
{
  "id": "T02",
  "kind": "implementation",
  "status": "done",
  "owner": null,
  "blocked_by": [
    "T01",
    "D09"
  ],
  "invalidated_by": [],
  "title": "实施A0真实SDK与假HTTP传输校准"
}
---
# 实施真实SDK与假HTTP传输的A0校准

## 范围与已有授权

维护者在T01交付后要求“继续”，本票承接同一无模型实验阶段，收窄到SDK真实请求边界与假HTTP传输；后续供应商契约已选DeepSeek官方直连。详见[SDK离线范围](../a0-sdk-offline-spec.md)。只用固定虚构key和封闭MockTransport，不接真实凭据/网络/材料或生产代码。供应商选择没有给予真实调用权限。

## 前置与验收

依赖T01隔离/ledger及D09接缝研究；D05完整预算不作为本合成前置。验收包括最终HTTP body预留、禁SDK重试、异常原始usage保留、工具前结算及实际A0消费、拒live路径。范围文档列出实际场景，不据fake通过宣称真实服务兼容。

## 交付与验证证据

待worker在独立worktree交付，主代理审查并回写原D05。

## 审查与集成证据

待实际集成。

### 2026-10-19T07:50:24+00:00 · draft → ready

维护者在T01交付后要求继续；承接无模型实验，按已选DeepSeek官方契约补真实SDK+封闭假HTTP校准，不读取或调用真实服务。范围见a0-sdk-offline-spec。

### 2026-10-19T07:50:24+00:00 · claim

Owner: codex:root:a0-sdk-implementation-20261019

## 2026-10-19 · 实际交付与主工作树验证

worker提交`20a517e95f4cdf87d772725af98439a49d4724e6`；主代理审查后以`c5a0443`集成，两个提交的experiments差异为空。只新增`experiments/a0_sdk_calibration.py`并补`experiments/README.md`；T01/ledger/mods未改。入口只有内建合成doctor，没有真实凭据、配置、模型、材料、live或恢复选项。

主工作树实际运行：

- `uv run --frozen python experiments/a0_sdk_calibration.py doctor`：54项通过，49次真实SDK→MockTransport尝试、49次结算；openai 2.49.0/httpx 0.28.1。证据`/tmp/bot-a0-sdk-zcuyeumh/doctor.json`，socket/QQ/external_write哨兵均0，真实HTTP/模型费用0。
- `uv run --frozen python experiments/a0_calibration.py doctor`：57项回归通过，证据`/tmp/bot-a0-calibration-c15zz18v/doctor.json`。
- `uv run --frozen python run.py --check`：127 Python files、85 public modules；`uv run --frozen python run.py --smoke`：83/85，可选失败仍为mcf/minecraft，与T01基线一致。验证摘要在`/tmp/agent-memory-t02-check-e89b3y7b`与`/tmp/agent-memory-t02-smoke-btt7wpr4`。
- worker另验证5个CLI拒绝探针（credential/live/config/model/resume均exit 2）、源码compile及README链接；无凭据或真实输入路径。

实际证据覆盖最终序列化body落盘预留后才进入mock；连接/429/500单次尝试；第13次在mock前拒绝；原始usage先于生产parser保存，length/断流/关闭时不丢已得用量；缓存和reasoning不重复计费；无usage全预留，矛盾/非法/超报/结算失败闭锁，工具只在结算成功后释放。实际A0的take/dry say、跨两次drive的原生reasoning/工具配对、活动turn取消及联合上下文拒绝均有断言。已有根在写manifest前拒绝，原manifest保持不变。

正常fixture manifest SHA-256：`2523640de6e3468c1567aaaaeeccf21760137198808c59f2c8b3bf1bd7ede4fc`；新源码SHA-256：`acc98dc3cd8de9e8ffaa067e37dc184eb5739a4025bd1708444bee3cf1834319`。完整manifest冻结实际SDK/httpx、代码、参数、价格数字快照与协议来源。

## 2026-10-19 · 审查范围与剩余限制

主代理读新增源码与README差异；只读复核代理定向检查SDK/HTTP/usage接缝。审查发现的两处证据缺口已补：第二drive核验旧[1,2,3]与新[4,5]的reasoning/结果；取消核验活动turn、cancel_turn命中、turn.cancelled、usage与无行动派发。复核代理确认新增断言成立；主代理随后实跑以上doctor/check/smoke。

本次用`.invalid`假地址，manifest另记官方契约目标；不证明真实服务接受、可用性或账户价格。`persist_reasoning=True`是本次官方thinking契约校准配置，T01的False未改，真实A0基线投影未冻结，也不改D04关于新循环跨唤醒丢弃思考的裁定。bytes+1024仍是假接口政策，不是真实完整输入token上界；18万/4096/12次与4.713216元仍为条件候选。部分恢复/fork继续不支持。D05/D10、真实材料读取与付费运行均未由本交付完成或授权。

### 2026-10-19T08:06:36+00:00 · update

追加实际交付、独立审查、主工作树54/57断言与check/smoke证据；保留原文和日志。

### 2026-10-19T08:06:36+00:00 · active → review

worker交付20a517e，已由主代理审查集成c5a0443，等待记录最终整体验收。

### 2026-10-19T08:06:36+00:00 · claim

Owner: codex:root:a0-sdk-review-20261019

### 2026-10-19T08:07:44+00:00 · review → done

主代理完成独立审查与c5a0443集成；主树SDK54项/A0 57项通过，check与smoke基线相符。23份Markdown/202个相对链接、历史前缀及无关票不变检查通过；范围内交付完成，真实费用/输入/四臂和D05/D10仍未完成。

### 2026-10-19T08:08:24+00:00 · update

清理已完成：原worker提交保留于refs/archive/codex/agent-memory-a0-sdk-calibration-20261019；确认干净且与集成差异为空后移除本票worktree及临时分支。主树既有无关改动保持不变。

## 2026-10-19 · 后续投影证据订正（交付状态不变）

D05后续[补证](../a0-projection-and-input-bound.md)发现：中心_record_output始终传model，oplog.output在model标签存在时即使persist_reasoning=False也保存reasoning。因此本票前段True与T01 False只是已记录的实参差异，不能直接推断为两种历史策略，也没有证据要求维护者为兼容另选一种保留模式。

主代理以当前keep/同model原生A0路径，在normal与plain-third两组各复验False/True的两drive完整wire body，每对六份逐项相等；24次MockTransport/24结算，socket0，证据`/tmp/bot-a0-projection-jzdk8lib/probe.json`。探针单独记录覆盖实参，未将原类默认True的manifest冒充正式清单；不外推drop/异模型/旧格式/无model路径或真实API接受性。本票已通过的SDK/usage/取消验收仍成立，无代码改动或恢复能力扩展。

有效README说明已修正，提交547f6ca；本目录scope文档和地图同步订正引用。跨唤醒不保留思考的来源准确写为无状态提案§6的D4，本目录D04是行为研究票；不改变维护者原裁定。正式A0实际运行清单仍需在未来范围内冻结。

### 2026-10-19T08:18:21+00:00 · update

保留全部历史，追加False/True真实wire等效范围、正式A0候选及输入预留条件补证；当前说明已订正，不宣称真实调用就绪。
