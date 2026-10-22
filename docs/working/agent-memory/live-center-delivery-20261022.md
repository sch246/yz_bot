# 正式启动接线与凌晨自检 · 2026-10-22

## 交接发布更新

维护者随后授权提交推送供Claude接手。源码基础已提交为`ded4e7b`，分支`codex/context-selection-handoff-20261022`；本次复核check134／85、临时fake smoke83／85通过，仅原有mcf／minecraft可选失败。[T28](tickets/T28-让前置选材决定实际上下文并支持过程重选.md)是尚未实施的下一切片。以下“先不提交”与旧基线保留初次交付事实；本次未重启真实进程，提交不代表部署或长期效果已证。

## 初次交付与验证

维护者授权直接应用主代码、先不提交，由其重启；自检选北京时间凌晨4点。[T27](tickets/T27-接通正式启动与凌晨自检并开放运行轨迹.md)记录授权和交付。基线为 `6896bf7`，本轮修改保留在工作树，未启动或重启真实Bot；未读取生产聊天、配置或凭据，未调用真实模型或发送QQ消息。

正常启动现在通过 `chat.on_load → live.start`装配Center，boot后接入既有scheduler和新journal的pending。旧pending恢复／NapCat启动补回线程退出正式路径；旧 `event_stream`、chatlog与配置保留，不自动导入、删除或重放。模型选择、客户端及费用继续复用正式设置；不接实验临时key和100M账本。

每醒前置选材、主体主动笔记、轮后图书管理员都已接线。运行记录增加UTC时间、按cutoff查询的有界 `inspect_runtime`目录与明确单条全文；目录不授予引用资格。可查历史note并以当前parent提交旧正文形成恢复版本，历史不覆盖。图片描述和委派请求有独立role／owner，实际请求、结果和usage进入同journal，不替主体确认激活或继承其来源信用。

夜间任务默认 **04:00:00 Asia/Shanghai**，使用同一个主体的环境激活；没有活动窗口不请求，同日去重，停机不补跑。quiet默认不执行窗口结束hint，仍允许主体明确选择say。自检提示鼓励核对请求、原件、行动结果与笔记，必要时使用现有代码工具在隔离目录实验；允许不改，不强制日更笔记或报告。普通mods变更仍需完整重启，工具模块沿last-good显式reload/load。

主循环及委派保留原有无默认轮数上限的WHY约束；选材／整理每角色默认4请求，显式实验仍可指定有限总量或每醒上限。普通已知供应商失败记失败结束，不永久关闭journal；落盘不确定、未知派发仍停止后续行动，不自动重试或重放。

## 验证证据

- 静态检查：134个Python文件、85个公开模块通过。
- 临时fake smoke改前后均83/85；仅原有 `mcf`、`minecraft`可选失败。
- 实际生命周期探针使用临时cwd、合成config与假OneBot，真OpenAI SDK经MockTransport响应。主／选材／整理10请求，实际SDK payload与journal逐字一致；2次笔记提交，派发／结果配对及journal重建通过。普通hint只到假服务；夜间quiet无外发，同日重复和重建后重复不写入。
- 实际Cron对象为04:00上海时区；手动投递通过，未等待真实凌晨。去掉smoke参数后，真实after_boot线程正常安装cron，请求仍10次、journal不变，无启动补跑。正常退出卸载Center、清理cron、还原client并关闭scheduler／listener。
- 辅助角色探针22次假HTTP、17次有usage响应，模型最大在途1；嵌套图片、背景图片、委派结果进入主体下一请求、来源隔离、过期安装拒绝、未知派发恢复闭锁均通过。delegate在role_requests=2时可连续5请求，仅显式总量5才停止，未新增默认轮数门槛。
- 补复现：256×256合成PNG的Base64曾被文本分词估成约189k token而拒绝；现在仅估算文本，图片payload仍完整发送并记录，超限长文本仍被拒绝。不把该估算当作供应商视觉token／费用上限。
- 外部证据：`/root/work/agent-memory-live-20261022/verify-live.py`、`verify-live-result.json`、`verify-live-output.txt`、`auxiliary-verification.log`、`final-smoke.log`。探针审计生产runtime文件访问为0，非假服务网络尝试为0；没有新增永久测试套件。

## 尚未证明与回退

假接口只证明接线、记录和生命周期，不证明真实长期记忆正确。T26已接受笔记仍有语义归错的反例继续保留。近期候选与查询有界，journal仍全量恢复入内存；多窗口分配、旧存量迁移及长期资源增长仍有缺口。

重启后首次使用新journal，无自动搬运旧笔记／pending。旧聊天可明确查档，旧事件仍可经现有oplog函数定向反查，但不是新C编号。若需回退代码，先停进程并保留两份日志；旧循环看不到新Center的激活和笔记，不能当作无损双向转换。本轮交付完成指主代码可供重启，不代表真实运行进程已经切换。

## 官方文档核对补记

2026-10-22按维护者提供的[官方价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)核对：当前Flash为V4.1-Flash-1017，文档上下文1M；凌晨04:00属于空闲半价时段。源码默认Flash三段高峰价（输入缓存命中0.04／未命中2／输出8元每百万token）与文档一致；已有设备配置不会被源码默认值覆盖，本轮没有读取或修改它，也没有把本地40000文本估算上限自动放大到供应商容量。

[官方用量说明](https://api-docs.deepseek.com/zh-cn/quick_start/token_usage/)和[图像理解](https://api-docs.deepseek.com/zh-cn/guides/vision/)确认图片按尺寸换算token，单张上限1024，实际以usage为准；Base64计入请求体大小，不应拿编码字符当文本token。48MiB请求体限制与模型上下文是两个边界，本轮没有增加Files API或宣称实现全部供应商准入限制。

保留一处证据差异：当前Chat Completions图像文档只保证user消息带图；仓库 `tool_images=True` 的WHY来自2026-09-18工具结果读图实测。未对1017版本做付费复验，也不因文档与旧实测不同而擅自移除该能力；此项不在本轮假接口通过的真实供应商结论内。
