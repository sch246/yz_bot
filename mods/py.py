"""The shared, privileged dynamic Python environment."""

import base64
import builtins
import datetime
import json
import math
import os
import random
import re
import time
import traceback

from mods import LATE, command, context, cq, file, log, message, op, thread


PHASE = LATE

# This is the installed, name-to-object public surface.  It is populated once
# all modules have imported, so optional import failures cannot be mistaken for
# a valid dynamic interface.
DYNAMIC_EXPORTS: dict[str, object] = {}
loc: dict[str, object] = {}
_stream = log.stream("py")


_EXPORT_SPECS = {
    # Message position and IO.
    "send": ("message", "send"),
    "sendmsg": ("message", "sendmsg"),
    "recvmsg": ("message", "recvmsg"),
    "msg_id": ("context", "interaction_key"),
    # Event predicates used by live reactions.
    "is_msg": ("msgs", "is_msg"),
    "is_file": ("msgs", "is_file"),
    "is_img": ("msgs", "is_img"),
    "is_notice": ("msgs", "is_notice"),
    "is_group_msg": ("msgs", "is_group_msg"),
    "is_group_recall": ("msgs", "is_group_recall"),
    "is_friend_recall": ("msgs", "is_friend_recall"),
    # Identity and ordinary state.
    "getname": ("identity", "getname"),
    "setname": ("identity", "setname"),
    "getstorage": ("identity", "getstorage"),
    "getgroupstorage": ("identity", "getgroupstorage"),
    "getgroupname": ("identity", "getgroupname"),
    # Self-authored tools may edit named prompt objects through the same
    # trusted Python surface instead of requiring a dedicated prompt editor.
    "prompts": ("chat", "prompts"),
    "headshot": ("identity", "headshot"),
    "headshot_url": ("identity", "headshot_url"),
    "memberlist": ("identity", "memberlist"),
    # Dynamic reaction and template vocabulary.
    "prints": ("pages", "prints"),
    "run_action": ("link", "run_action"),
    "do_action": ("link", "run_action"),
    "Int": ("link", "Int"),
    "Name": ("link", "Name"),
    "Param": ("link", "Param"),
    "All": ("link", "All"),
    "CQ": ("link", "CQ"),
    "CQ_at": ("link", "CQ_at"),
    # Device and live-link leaf capabilities with confirmed consumers.
    "getran": ("randoms", "getran"),
    "rd": ("randoms", "rd"),
    "connect_mc": ("minecraft", "connect_mc"),
    "checkmc": ("minecraft", "checkmc"),
    "start_mc": ("minecraft", "start_mc"),
    "stop_mc": ("minecraft", "stop_mc"),
    "restart_mc": ("minecraft", "restart_mc"),
    "mc_console": ("minecraft", "console"),
    # Stable vocabulary migrated out of the device pyload file.
    "at2qq": ("cq", "at2qq"),
    "bbxm": ("bbxm", "random_phrase"),
    "bottles_get": ("bottles", "bottles_get"),
    "bottles_answer_get": ("bottles", "bottles_answer_get"),
    "bottles_set": ("bottles", "bottles_set"),
    "bottles_answer_set": ("bottles", "bottles_answer_set"),
    "bottles_init": ("bottles", "bottles_init"),
    "bottles_guess": ("bottles", "bottles_guess"),
    "bottles_check": ("bottles", "bottles_check"),
    "decode_unicode": ("codec", "decode_unicode"),
    "encode_unicode": ("codec", "encode_unicode"),
    "decode_base85": ("codec", "decode_base85"),
    "encode_base85": ("codec", "encode_base85"),
    "decode_dict": ("codec", "decode_dict"),
    "encode_dict": ("codec", "encode_dict"),
    "get_reply": ("message", "get_reply"),
    "iex": ("screen", "iex"),
    "is_valid_ssh_pubkey": ("portfunc", "is_valid_ssh_pubkey"),
    "isfloat": ("text", "isfloat"),
    "setu": ("setu", "link_image"),
    "time_between": ("timeutils", "time_between"),
    "vcs": ("screen", "vcs"),
    "真太阳时": ("timeutils", "真太阳时"),
    "lunar_time": ("lunar", "lunar_time"),
    "小六壬": ("lunar", "小六壬"),
    "get_petpet_keys": ("petpet", "get_petpet_keys"),
    "petpet": ("petpet", "petpet"),
    "petpet_dic": ("petpet", "petpet_dic"),
    "petpet_trans": ("petpet", "petpet_trans"),
    "move_mat": ("game2048", "move_mat"),
    "show_mat": ("game2048", "show_mat"),
    "d2048": ("game2048", "d2048"),
    "step_2048": ("game2048", "step_2048"),
    "准6": ("change", "准6"),
    "deal_img": ("image_tools", "deal_img"),
    "geocode": ("weather", "geocode"),
    "道德经": ("dao", "道德经"),
    # Persistent tasks replace this with a task-bound closure while firing.
    "later_repeat": ("later", "repeat"),
    "repeat": ("later", "repeat"),
}


def _build_exports(loaded):
    from mods import is_available

    exports = {}
    missing = []
    # These definitions intentionally point forward within LATE: link needs
    # py.loc, while later injects its task-bound repeat closure through py.
    forward_definitions = {"later", "link"}
    for public_name, (module_name, attribute) in _EXPORT_SPECS.items():
        module = loaded.get(module_name)
        if (
            module is None
            or not hasattr(module, attribute)
            or (
                module_name not in forward_definitions
                and not is_available(module_name)
            )
        ):
            missing.append(f"{public_name}={module_name}.{attribute}")
            continue
        exports[public_name] = getattr(module, attribute)
    if missing:
        raise RuntimeError("动态导出缺失: " + ", ".join(missing))
    storage_module = loaded.get("storage")
    if storage_module is None or not is_available("storage"):
        raise RuntimeError("动态环境缺少 storage 模块")
    exports.update(
        {
            # Ordinary modules import directly.  Dynamic code uses this only
            # when the module name itself is computed at runtime.
            "getmod": loaded.get,
            # The old root data.json namespace is retired; storage is now the
            # only runtime and persistence owner of the familiar data object.
            "data": storage_module.get("", "py_data"),
            "input": chat_input,
            "print": chat_print,
            "find": find,
            "ls": ls,
            "match": match,
            "getlog": getlog,
            "same_times": same_times,
            "any_same": any_same,
            "get_one": get_one,
            "base64": base64,
            "datetime": datetime,
            "json": json,
            "math": math,
            "os": os,
            "random": random,
            "re": re,
            "time": time,
            "traceback": traceback,
            "true": True,
            "false": False,
            "none": None,
            "nil": None,
            "null": None,
        }
    )
    return exports


def on_load(loaded):
    """Atomically install the base environment plus a successful pyload input."""
    exports = _build_exports(loaded)
    DYNAMIC_EXPORTS.clear()
    DYNAMIC_EXPORTS.update(exports)
    base = {"__builtins__": builtins.__dict__, "ctx": loaded, **loaded, **exports}
    candidate = dict(base)
    path = "data/pyload.py"
    if os.path.isfile(path):
        try:
            source = file.read(path)
            exec(compile(source, path, "exec"), candidate)
            if candidate.get("ctx") is not loaded:
                raise ValueError("pyload.py 不得覆盖保留名 ctx")
            # An explicit version-controlled flat export may intentionally use
            # the same spelling as a module (currently ``petpet``).  All other
            # module names remain reserved against device-script replacement.
            overwritten_modules = [
                name
                for name, module in loaded.items()
                if name not in exports and candidate.get(name) is not module
            ]
            if overwritten_modules:
                raise ValueError(
                    "pyload.py 不得覆盖模块名: " + ", ".join(overwritten_modules)
                )
            overridden = sorted(
                name
                for name, value in exports.items()
                if name in candidate and candidate[name] is not value
            )
            if overridden:
                _stream.info("pyload.py 覆盖动态导出: " + ", ".join(overridden))
        except Exception:
            traceback.print_exc()
            _stream.info("pyload.py 执行失败，本次仅安装版本控制内的动态环境")
            candidate = base
    loc.clear()
    loc.update(candidate)


def chat_input(prompt: str = "", recv_all=False):
    event = context.current()
    key = context.interaction_key(event)
    waiter = context.MessageWaiter()
    context.register_waiter(key, waiter)
    if prompt:
        message.sendmsg(prompt)
    try:
        reply = waiter.wait()
    finally:
        context.pop_waiter(key)
    if recv_all:
        return reply
    return reply.get("message") if isinstance(reply, dict) else None


def chat_print(*values, sep=" ", end="\n", file=None, flush=False):
    if file is not None:
        builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
        return
    if not values or (len(values) == 1 and values[0] is None):
        return
    return message.sendmsg(sep.join(map(str, values)))


def find(iterable, predicate):
    for index, value in enumerate(iterable):
        if predicate(value):
            return index
    return None


def ls(value):
    return "\n".join(sorted(map(str, value)))


def match(pattern):
    event = context.current()
    if isinstance(event, dict) and "message" in event:
        return re.match(pattern, event["message"])
    return None


def getlog(index=None):
    from mods import history

    events = history.getlog(context.current())
    return events if index is None else events[index]


def same_times(predicate, count=None):
    from mods import history

    return history.same_times(context.current(), predicate, count)


def any_same(predicate, count=None):
    from mods import history

    return history.any_same(context.current(), predicate, count)


def get_one(predicate, count=None):
    from mods import history

    return history.get_one(context.current(), predicate, count)


def _execute(body: str, msg=None, skip_op=False, insert=None):
    event = msg if msg is not None else context.current()
    context.set_current(event)
    if not skip_op and not op.require_op(event):
        return None
    loc["_py_expr"] = body
    loc["_py_msg"] = event
    loc["msg"] = event
    if insert:
        loc.update(insert)
    body = cq.unescape(body.strip())
    if not body:
        return run.__doc__
    lines = body.splitlines(keepends=True)
    try:
        exec("".join(lines[:-1]), loc)
        last = lines[-1].strip()
        if last.startswith("###"):
            file.add("data/pyload.py", "\n" + body)
            message.sendmsg("添加成功")
        elif not last.startswith("#"):
            result = eval(last, loc)
            if result is not None:
                message.sendmsg(result)
    except context.InteractionCancelled:
        return None
    except Exception:
        message.sendmsg("#" + "".join(traceback.format_exc().splitlines(True)[3:]).strip())
    return None


@command.command
@thread.to_thread
def run(body: str, msg=None, skip_op=False, insert=None):
    """在共享动态环境中运行 Python（管理员）。

    格式：.py <代码>
    多行代码先 exec 前面各行，再 eval 最后一行并发送非 None 结果；末行以 # 静默，以 ### 将整段追加到 data/pyload.py。
    """
    return _execute(body, msg=msg, skip_op=skip_op, insert=insert)
