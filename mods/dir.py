"""Zip directory transfer for the privileged ``.dir`` command."""

import os
import shutil
import tempfile

from mods import connect, context, cq, file, message, msgs, op, text
from mods.command import command


_temporary_archives: set[str] = set()


def get(path: str):
    absolute = os.path.abspath(path)
    if not os.path.exists(absolute):
        return f'路径"{absolute}"不存在'
    if not os.path.isdir(absolute):
        return f'路径"{absolute}"不是文件夹'
    try:
        base = tempfile.mktemp(prefix="yuzu-dir-")
        archive = shutil.make_archive(base, "zip", absolute)
        _temporary_archives.add(archive)
        future = message.sendmsg(file._send_file(archive))

        def cleanup(_future):
            try:
                os.remove(archive)
            except FileNotFoundError:
                pass
            finally:
                _temporary_archives.discard(archive)

        future.add_done_callback(cleanup)
        return future
    except Exception as exc:
        return f"压缩出错: {exc}"


def _receive(event, destination):
    item = cq.load(cq.find_all(event["message"])[0])
    result = connect.call_api("get_file", file_id=item["data"]["file_id"])
    if result.get("retcode") != 0:
        return result.get("wording", "获取文件失败")

    handle, archive = tempfile.mkstemp(prefix="yuzu-dir-", suffix=".zip")
    os.close(handle)
    try:
        shutil.move(result["data"]["file"], archive)
        if os.path.exists(destination):
            shutil.rmtree(destination)
        os.makedirs(destination, exist_ok=True)
        shutil.unpack_archive(archive, destination)
    finally:
        if os.path.exists(archive):
            os.remove(archive)
    message.send(
        f"文件夹内容已成功解压到\n{destination}",
        **message.target(event),
    )


def set(path, force=False):
    if os.path.exists(path) and not force:
        if not os.path.isdir(path):
            return "该路径已存在但不是文件夹，操作终止"
        reply = yield "目标文件夹已存在，确定要覆盖吗(y/n)"
        if not file._confirmed(reply):
            return "操作终止"
    reply = yield "请发送一个zip压缩文件（包含要解压的文件夹内容）"
    if not msgs.is_file(reply):
        return "发送的不是文件，操作终止"
    return _receive(reply, path)


@command
def run(body: str):
    """压缩发送或接收解压宿主机目录（管理员）。

    格式：.dir get <路径> | .dir set <路径> [-y|-f] dir
    get 会把目录打包发送；set 的末尾必须写 dir，强制参数表示允许覆盖。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    first = cq.unescape(body).splitlines()[0] if body.strip() else ""
    action, tail = text.read_params(first)
    if action == "get":
        path, _ = text.read_params(tail)
        if os.path.isfile(path):
            return file._get(path)
        if os.path.isdir(path):
            return get(path)
        return "目标不存在"
    if action == "set":
        path, extra, remaining = text.read_params(tail, 2)
        if extra not in ("-y", "-f"):
            remaining, extra = extra, ""
        if remaining.strip() == "dir":
            return set(path, extra in ("-y", "-f"))
        return file._set(path, extra in ("-y", "-f"))
    return run.__doc__
