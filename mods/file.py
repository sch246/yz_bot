"""Host-file helpers and the privileged ``.file`` command."""

import json
import os
import re
import shutil
import traceback

from mods import connect, context, cq, history, message, msgs, op, text, thread
from mods.command import command


def ensure_file(path: str) -> str:
    """Create the parent directory and avoid returning an existing directory."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isdir(path):
        stem, suffix = os.path.splitext(path)
        for index in range(100):
            candidate = f"{stem}_{index}{suffix}"
            if not os.path.isdir(candidate):
                return candidate
        raise RuntimeError("循环超过上限")
    return path


def read(path: str, start_line=None, end_line=None):
    with open(path, encoding="utf-8") as stream:
        return "".join(stream.readlines()[start_line:end_line])


def add(path: str, value: str):
    ensure_file(path)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(value)


def write(path: str, value: str):
    ensure_file(path)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(value)


def overwrite(path: str, value: str, start_line=None, end_line=None):
    with open(path, encoding="utf-8") as stream:
        lines = stream.readlines()
    replacement = value.splitlines(keepends=True)
    if replacement and not replacement[-1].endswith(("\n", "\r")):
        replacement[-1] += "\n"
    lines[start_line:end_line] = replacement
    with open(path, "w", encoding="utf-8") as stream:
        stream.writelines(lines)


def getpath(path: str) -> str:
    return path


def json_read(path: str, **kwargs):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream, **kwargs)


def json_write(path: str, value, **kwargs):
    ensure_file(path)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=4, ensure_ascii=False, **kwargs)


def let_be_filename(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", title).strip()


def can_be_filename(name: str) -> bool:
    return not bool(set(name) & set('/\\:*?"<>|'))


def insert_linemark(value: str) -> str:
    return "\n".join(f"{index}│{line}" for index, line in enumerate(value.splitlines()))


def strip_linemark(value: str) -> str:
    return "\n".join(re.sub(r"^\d+[|│]", "", line, count=1) for line in value.splitlines())


def read_text(value: str, start=None, end=None) -> str:
    return "\n".join(insert_linemark(value).splitlines()[start:end])


def read_file(path: str, with_linemark=False, start=None, end=None) -> str:
    value = read(path)
    return read_text(value, start, end) if with_linemark else value


def listitems(path: str):
    directories, files = [], []
    for item in os.listdir(path):
        target = os.path.join(path, item)
        (directories if os.path.isdir(target) else files).append(item)
    return sorted(directories), sorted(files)


def listdir(path=".") -> str:
    directories, files = listitems(path)
    return "\n".join([*(f"> {name}" for name in directories), *files])


def _send_file(path: str) -> str:
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute):
        return f'打开失败，文件"{absolute}"不存在'
    return cq.dump({"type": "file", "data": {"file": f"file://{absolute}"}})


def _get(path: str):
    return _send_file(path)


def _read(path, with_linemark, start, end):
    if os.path.isdir(path):
        return listdir(path)
    try:
        value = read_file(path, with_linemark, start, end)
        return value if value else "文件为空"
    except Exception as exc:
        return exc


def _write(path, start, end, lines):
    try:
        overwrite(path, "\n".join(lines), start, end)
        return "已写入 " + path
    except Exception as exc:
        return exc


def _remove(path: str, recursive=False):
    try:
        if recursive:
            shutil.rmtree(path)
        else:
            os.remove(path)
        return f"已删除: {path}"
    except Exception as exc:
        return f"删除出错: {exc}"


def _del(path: str, recursive=False):
    if re.fullmatch(r"/[^/]*/?", path):
        return "危险操作，已禁用"
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return f'路径"{path}"不存在'
    if os.path.isfile(path):
        return _remove(path)
    if os.path.isdir(path) and recursive:
        return _remove(path, True)
    if os.path.isdir(path):
        reply = yield "目标是目录而不是文件，确定要删除吗(y/n)"
        if not _confirmed(reply):
            return "操作终止"
        return _remove(path, True)
    return "未知错误，遇到了不是文件也不是文件夹的东西"


@thread.to_thread(None)
def download(url: str, path: str, event=None):
    """Download a file on demand; import-time remains side-effect free."""
    try:
        import requests

        ensure_file(path)
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            with open(path, "wb") as stream:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        stream.write(chunk)
    except Exception:
        if event is not None:
            message.send("".join(traceback.format_exc()), **message.target(event))
        return None
    if event is not None:
        message.send(f"文件已保存到\n{path}", **message.target(event))
    return path


def recv_img(event, path):
    if not msgs.is_img(event):
        return "目标msg不是单个图片"
    item = cq.load(event["message"])
    download(item["data"]["url"], path, event)
    return f"正在将图片保存到\n{path}"


def _recv_file(event, path):
    if not msgs.is_file(event):
        return "发送的不是文件，操作终止"
    item = cq.load(cq.find_all(event["message"])[0])
    result = connect.call_api("get_file", file_id=item["data"]["file_id"])
    if result.get("retcode") != 0:
        return result.get("wording", "获取文件失败")
    ensure_file(path)
    shutil.move(result["data"]["file"], path)
    message.send(f"文件已保存到\n{path}", **message.target(event))


def _confirmed(event) -> bool:
    return msgs.is_msg(event) and event["message"] in (
        "是", "确定", "y", "Y", "yes", "Yes", "YES", "OK", "ok", "Ok"
    )


def _set(path, force=False):
    if force or not os.path.exists(path):
        reply = yield "请发送一个文件/图片"
        if msgs.is_file(reply):
            return _recv_file(reply, path)
        if msgs.is_img(reply):
            return recv_img(reply, path)
        return "发送的不是文件或图片，操作终止"
    if not os.path.isfile(path):
        return "目标已存在但不是文件"
    reply = yield "文件已存在，确定要覆盖文件吗(y/n)"
    if msgs.is_file(reply):
        return _recv_file(reply, path)
    if msgs.is_img(reply):
        return recv_img(reply, path)
    if not _confirmed(reply):
        return "操作终止"
    reply = yield "请发送一个文件/图片"
    if msgs.is_file(reply):
        return _recv_file(reply, path)
    if msgs.is_img(reply):
        return recv_img(reply, path)
    return "发送的不是文件或图片，操作终止"


def _to(path, force=False):
    recent = history.get_one(context.current(), msgs.is_file, 10)
    if not recent:
        return "10条消息内没有文件"
    if force or not os.path.exists(path):
        return _recv_file(recent, path)
    if not os.path.isfile(path):
        return "目标已存在但不是文件"
    reply = yield "文件已存在，确定要覆盖文件吗(y/n)"
    if not _confirmed(reply):
        return "操作终止"
    return _recv_file(recent, path)


@command
def run(body: str):
    """查看、收发和修改宿主机文件（管理员）。

    格式：.file read <路径> [-i] [起始行] [结束行]；write <路径> [起始行] [结束行]；get <路径>。
    另有 set <路径> [-y|-f]、to <路径> [-y|-f] 接收文件，以及 del <路径> [-r] 删除；目录收发委托 .dir。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    lines = cq.unescape(body).splitlines()
    first, rest = (lines + [""])[0], lines[1:]
    action, tail = text.read_params(first)
    if action == "read":
        path, first_extra, second_extra, third_extra, _ = text.read_params(tail, 4)
        if first_extra == "-i":
            marked, start, end = True, second_extra, third_extra
        else:
            marked, start, end = False, first_extra, second_extra
        return _read(path or ".", marked, text.getint(start), text.getint(end))
    if action == "write":
        path, start, end, _ = text.read_params(tail, 3)
        if not os.path.isfile(path):
            return "目标不是文件"
        return _write(path, text.getint(start), text.getint(end), rest)
    if action == "get":
        path, _ = text.read_params(tail)
        if os.path.isfile(path):
            return _get(path)
        if os.path.isdir(path):
            from mods import dir as directory

            return directory.get(path)
        return "目标不存在"
    if action in ("set", "del", "to"):
        path, extra, _ = text.read_params(tail, 2)
        force = extra in ("-y", "-f")
        if action == "set":
            return _set(path, force)
        if action == "del":
            return _del(path, extra == "-r")
        return _to(path, force)
    return run.__doc__
