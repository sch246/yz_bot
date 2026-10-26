"""给自己或朋友定个延时闹钟：到点去指定的群或私聊发句话，或者跑一段代码。

定下的任务会稳稳记在对应群（`g<群号>`）或私聊（`u<QQ号>`）名下，就算中途你重启了也不会忘。

## 怎么写提醒内容

到点的时候，系统其实是在跑一小段 Python：先跑 `code`，再算出 `expr` 的值，算出来不是 `None` 就当成消息发出去。
所以**定普通文字提醒时，`expr` 一定要套上一层引号写成 Python 字符串**！比如写 `'该喝水啦'`；要是漏了引号直接写 `该喝水啦`，到点时 Python 会把它当成变量名去查，就会报错啦：

- **最简单的纯文字提醒**：`later_add("g群号", "10m", "", "'十分钟到了，记得喝水'")`（`code` 留空即可）
- **到点再现算的内容**：如果你有 op 权限，可以把计算逻辑写进 `code` 里，最后用 `expr` 拼出要发的那句话。

## 时间怎么填（`time` 参数）

- **倒计时（相对时间）**：`30s`、`10m`、`2h`、`1d`、`1M`、`1Y`，还能拼起来写像 `1h30m`。**注意大小写别混：`m` 是分钟，`M` 是月份。**
- **定具体钟点（绝对时间）**：`21:30`、`21:30:00`（要是今天的这个点已经过了，会自动顺延到明天）、`09-15`、`09-15 21:30`。如果只写日期没写几点，默认在当天凌晨 04:00 触发。

## 记好序号与周期重复

- `later_add` 定好后会回给你一句 `序号: 实际执行时间 表达式`。因为这里没有专门列任务清单的工具，**这个序号就是以后取消它的唯一凭据**，记得在回复里告诉对方，或者记在自己的笔记里（对方自己打 `.later` 命令也能看）。
- **想做成每天/定期的重复闹钟**：可以在 `code` 里写 `repeat('1d')`（或者 `later_repeat('1d')`），`expr` 写 `'每天提醒：交日报'`——这样它每次响完，都会自动给自己顺延排好下一次。"""


def _target_message(target: str) -> dict:
    from mods import chat

    window = chat.parse_target(target)
    return {"group_id": window[1]} if window[0] == "group" else {"target_id": window[1]}


def later_add(target: str, time: str, code: str, expr: str) -> str:
    """添加一个延时任务，到点在明确目标发送 expr 的求值结果；成功返回"序号: 执行时间 表达式"。

    @param
    target: g<群号> 或 u<私聊对端号>
    time: 执行时间；相对时间如 30s、10m、2h、1d、1M（M 是月、m 是分钟），绝对时间如 21:30、09-15 21:30、2026-09-15 21:30:00
    code: 到点时在 expr 之前执行的 Python 代码，不需要就传空字符串；非空代码只有管理员可用
    expr: 到点求值并发送的 Python 表达式；普通提醒必须是带引号的字符串，例如 '该喝水了'
    """
    from mods import later

    try:
        msg = _target_message(target)
    except ValueError as error:
        return str(error)
    return later.run(f" add {time} {code}\n{expr}", bot_action=True, msg=msg)


def later_del(target: str, seqs: str) -> str:
    """按序号删除明确目标聊天的延时任务，返回被删掉的任务；序号不存在时返回删除失败。

    @param
    target: g<群号> 或 u<私聊对端号>
    seqs: 一个序号如 3，逗号分隔的多个序号如 3,5,8，或 * 删除当前聊天的全部任务
    """
    from mods import later

    try:
        msg = _target_message(target)
    except ValueError as error:
        return str(error)
    return later.run(f" del {seqs}", bot_action=True, msg=msg)


__all__ = ["later_add", "later_del"]
