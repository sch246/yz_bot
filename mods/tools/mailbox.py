"""读写一个或多个邮箱账号的信件：存账号、看未读、列邮件、读正文、列文件夹。

凭据只落在宿主机 data/yuzu_mail.json（权限 600），不回显明文、不写进聊天。这里的"授权码"
是各家邮箱设置里生成的应用密码/授权码，不是登录密码；多数服务商在 IMAP 上会拒绝登录密码。
支持多账号：一个文件里放多个地址，用 account 参数按地址、标签或地址片段指定，留空则用默认账号。

## 用法

1. `save_account(address, authcode, label, host, port, make_default)` 存账号；host 留空按域名
   自动猜；同地址重复存即覆盖授权码，第一个账号自动成为默认。
2. `list_accounts()` 看存了哪些账号、谁是默认、各自授权码指纹。
3. `set_default(address)` / `remove_account(address)` 换默认、删账号。
4. `status(account)` 连一次服务器，确认能不能登、收件箱几封、几封未读。
5. `list_messages(limit, folder, unseen_only, keyword, account)` 列邮件，给出的是**序号**（1 开始，
   与文件夹内顺序一致），不是 uid；读正文时把这个序号传给 `read_message`。
6. `read_message(number, folder, max_chars, account)` 读某封的完整头部与正文纯文本。
7. `list_folders(account)` 看文件夹（INBOX、Sent Messages 等）。

常见 IMAP 主机：QQ/Foxmail 是 imap.qq.com，163 是 imap.163.com，126 是 imap.126.com，
Gmail 是 imap.gmail.com（须用应用专用密码），Outlook 是 outlook.office365.com。
只读不改：不删信、不发信、不改已读状态。
"""

from __future__ import annotations

import email
import hashlib
import html
import imaplib
import json
import os
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CRED = os.path.join(_ROOT, "data", "yuzu_mail.json")
_PORT = 993
_HOSTS = {
    "qq.com": "imap.qq.com",
    "vip.qq.com": "imap.qq.com",
    "foxmail.com": "imap.qq.com",
    "163.com": "imap.163.com",
    "126.com": "imap.126.com",
    "yeah.net": "imap.yeah.net",
    "sina.com": "imap.sina.com",
    "sina.cn": "imap.sina.com",
    "sohu.com": "imap.sohu.com",
    "aliyun.com": "imap.aliyun.com",
    "gmail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "139.com": "imap.139.com",
    "189.cn": "imap.189.cn",
    "21cn.com": "imap.21cn.com",
}


def _blank() -> dict:
    return {"default": "", "accounts": {}}


def _guess(address: str) -> str:
    domain = address.rsplit("@", 1)[-1].strip().lower()
    return _HOSTS.get(domain, "imap." + domain if domain else "imap.qq.com")


def _load() -> dict:
    try:
        with open(_CRED, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    if isinstance(raw.get("accounts"), dict):
        data = {"default": str(raw.get("default") or ""), "accounts": {}}
        for address, entry in raw["accounts"].items():
            if isinstance(entry, dict) and entry.get("authcode"):
                data["accounts"][address] = entry
        if not data["default"] and data["accounts"]:
            data["default"] = next(iter(data["accounts"]))
        return data
    if raw.get("address") and raw.get("authcode"):
        address = str(raw["address"])
        return {"default": address,
                "accounts": {address: {"authcode": raw["authcode"],
                                       "host": raw.get("host") or _guess(address),
                                       "label": ""}}}
    return _blank()


def _store(data: dict) -> None:
    os.makedirs(os.path.dirname(_CRED), exist_ok=True)
    tmp = _CRED + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _CRED)


def _fingerprint(code: str) -> str:
    return hashlib.blake2s(code.encode("utf-8"), digest_size=4).hexdigest()


def _pick(account: str = "") -> tuple:
    data = _load()
    book = data["accounts"]
    if not book:
        raise RuntimeError("还没存账号：先 save_account(address, authcode)")
    want = (account or "").strip().lower()
    if want in ("", "default", "默认"):
        want = str(data.get("default") or next(iter(book))).strip().lower()
    for address, entry in book.items():
        if address.lower() == want:
            return address, entry
    for address, entry in book.items():
        if str(entry.get("label") or "").strip().lower() == want:
            return address, entry
    for address, entry in book.items():
        if want and want in address.lower():
            return address, entry
    raise RuntimeError("没有叫「%s」的账号，现有：%s" % (account, "、".join(book)))


def _session(account: str = ""):
    address, entry = _pick(account)
    host = str(entry.get("host") or _guess(address))
    port = int(entry.get("port") or _PORT)
    box = imaplib.IMAP4_SSL(host, port)
    box.login(address, str(entry["authcode"]))
    return box, address, entry


def _hdr(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).replace("\n", " ").strip()
    except Exception:
        return str(value).strip()


def _when(value) -> str:
    if not value:
        return "?"
    try:
        return parsedate_to_datetime(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _folder(name: str) -> str:
    name = (name or "INBOX").strip()
    return '"%s"' % name if any(ch in name for ch in " 中文") else name


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def _plain(msg) -> str:
    chunks = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, "replace")
        except LookupError:
            text = payload.decode("utf-8", "replace")
        if ctype == "text/html":
            text = _strip_html(text)
        chunks.append(text.strip())
    body = "\n\n".join(chunk for chunk in chunks if chunk)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def save_account(address: str, authcode: str, label: str = "", host: str = "",
                 port: int = 0, make_default: bool = False) -> str:
    """保存或覆盖一个邮箱账号，返回地址与授权码指纹（不回显授权码）。

    @param
    address: 邮箱地址，例如 236288772@qq.com
    authcode: 邮箱设置里生成的授权码或应用专用密码，不是登录密码
    label: 好记的别名，例如"主号""工作"，留空则只能按地址找
    host: IMAP 主机，留空则按邮箱域名自动判断，例如 qq.com 用 imap.qq.com
    port: IMAP 端口，留 0 用 993
    make_default: 是否顺便设成默认账号；第一个账号会自动成为默认
    """
    address = address.strip()
    authcode = authcode.strip().strip("`").strip()
    if "@" not in address:
        return "地址不像邮箱，检查一下"
    if len(authcode) < 8:
        return "授权码太短，一般是 16 位小写字母那种"
    data = _load()
    entry = {"authcode": authcode, "host": host.strip() or _guess(address),
             "label": label.strip()}
    if port:
        entry["port"] = int(port)
    data["accounts"][address] = entry
    if make_default or not data.get("default"):
        data["default"] = address
    _store(data)
    tail = "" if data["default"] == address else "（默认还是 %s）" % data["default"]
    return "已存 %s｜主机 %s｜指纹 %s%s" % (address, entry["host"],
                                            _fingerprint(authcode), tail)


def list_accounts() -> str:
    """列出已存的邮箱账号：地址、标签、主机、授权码指纹，并标出默认账号。"""
    data = _load()
    book = data["accounts"]
    if not book:
        return "还没存账号：先 save_account(address, authcode)"
    lines = []
    for address, entry in book.items():
        mark = "★默认 " if address == data.get("default") else "　　"
        label = str(entry.get("label") or "").strip() or "-"
        host = str(entry.get("host") or _guess(address))
        lines.append("%s%s｜标签 %s｜%s｜指纹 %s"
                     % (mark, address, label, host, _fingerprint(str(entry.get("authcode", "")))))
    return "共 %d 个账号：\n%s" % (len(book), "\n".join(lines))


def set_default(address: str) -> str:
    """把某个已存账号设为默认账号，返回改后的默认账号地址。

    @param
    address: 邮箱地址、别名或地址片段
    """
    data = _load()
    try:
        picked, _entry = _pick(address)
    except Exception as error:
        return str(error)
    data["default"] = picked
    _store(data)
    return "默认账号已改成 %s" % picked


def remove_account(address: str) -> str:
    """删掉一个已存账号，返回结果；删的是默认账号时会自动换一个当默认。

    @param
    address: 邮箱地址、别名或地址片段
    """
    data = _load()
    try:
        picked, _entry = _pick(address)
    except Exception as error:
        return str(error)
    data["accounts"].pop(picked, None)
    if data.get("default") == picked:
        data["default"] = next(iter(data["accounts"]), "")
    _store(data)
    rest = "、".join(data["accounts"]) or "（一个都不剩了）"
    return "已删 %s｜还留着：%s｜默认：%s" % (picked, rest, data.get("default") or "无")


def status(account: str = "") -> str:
    """连一次服务器，返回登录结果、收件箱总数与未读数；用来确认授权码还灵不灵。

    @param
    account: 邮箱地址、别名或地址片段，留空用默认账号
    """
    try:
        box, address, _entry = _session(account)
    except Exception as error:
        return "登录失败：%s: %s" % (type(error).__name__, error)
    try:
        typ, data = box.select("INBOX", readonly=True)
        if typ != "OK":
            return "登录成功，但打不开收件箱：%s" % (data,)
        total = data[0].decode() if data and data[0] else "?"
        typ, unseen = box.search(None, "UNSEEN")
        count = len(unseen[0].split()) if typ == "OK" and unseen and unseen[0] else 0
        return "登录成功｜%s｜收件箱 %s 封，未读 %d 封" % (address, total, count)
    finally:
        try:
            box.logout()
        except Exception:
            pass


def list_folders(account: str = "") -> str:
    """列出邮箱里的文件夹名，找不到时给出提示。

    @param
    account: 邮箱地址、别名或地址片段，留空用默认账号
    """
    try:
        box, address, _entry = _session(account)
    except Exception as error:
        return "登录失败：%s: %s" % (type(error).__name__, error)
    try:
        typ, rows = box.list()
        if typ != "OK":
            return "列文件夹失败：%s" % (rows,)
        names = []
        for row in rows or []:
            text = row.decode("utf-8", "replace") if isinstance(row, bytes) else str(row)
            match = re.search(r'"([^"]*)"$', text.strip())
            names.append(match.group(1) if match else text.strip())
        return "%s 的文件夹：%s" % (address, "、".join(names))
    finally:
        try:
            box.logout()
        except Exception:
            pass


def list_messages(limit: int = 10, folder: str = "INBOX", unseen_only: bool = False,
                  keyword: str = "", account: str = "") -> str:
    """列出最近的邮件，每行给出序号、时间、发件人、主题。

    @param
    limit: 最多返回几封，默认 10，最新的在前
    folder: 文件夹名，默认 INBOX
    unseen_only: 只看未读
    keyword: 只保留主题或发件人里含这个词的邮件，空字符串表示不筛
    account: 邮箱地址、别名或地址片段，留空用默认账号
    """
    try:
        box, address, _entry = _session(account)
    except Exception as error:
        return "登录失败：%s: %s" % (type(error).__name__, error)
    try:
        typ, data = box.select(_folder(folder), readonly=True)
        if typ != "OK":
            return "打不开 %s：%s" % (folder, data)
        typ, found = box.search(None, "UNSEEN" if unseen_only else "ALL")
        seqs = found[0].split() if typ == "OK" and found and found[0] else []
        if not seqs:
            return "%s 的 %s 里没有符合条件的邮件" % (address, folder)
        rows = []
        for seq in reversed(seqs):
            typ, payload = box.fetch(seq, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            raw = b""
            for part in payload or []:
                if isinstance(part, tuple):
                    raw += part[1]
            msg = email.message_from_bytes(raw)
            subject = _hdr(msg.get("Subject")) or "(无主题)"
            sender = _hdr(msg.get("From")) or "?"
            if keyword and keyword not in subject and keyword not in sender:
                continue
            rows.append("[%s] %s｜%s｜%s" % (seq.decode(), _when(msg.get("Date")), sender, subject))
            if len(rows) >= max(1, int(limit)):
                break
        if not rows:
            return "%s 的 %s 里没有含「%s」的邮件" % (address, folder, keyword)
        head = "%s｜%s 最近 %d 封（序号可直接传给 read_message）：" % (address, folder, len(rows))
        return head + "\n" + "\n".join(rows)
    finally:
        try:
            box.logout()
        except Exception:
            pass


def read_message(number: int, folder: str = "INBOX", max_chars: int = 2000,
                 account: str = "") -> str:
    """读某一封邮件的头部和正文纯文本，长正文按 max_chars 截断。

    @param
    number: 邮件序号，就是 list_messages 里方括号中的数字
    folder: 序号所属的文件夹，默认 INBOX
    max_chars: 正文最多返回多少个字符，默认 2000
    account: 邮箱地址、别名或地址片段，留空用默认账号
    """
    try:
        box, address, _entry = _session(account)
    except Exception as error:
        return "登录失败：%s: %s" % (type(error).__name__, error)
    try:
        typ, data = box.select(_folder(folder), readonly=True)
        if typ != "OK":
            return "打不开 %s：%s" % (folder, data)
        typ, payload = box.fetch(str(int(number)), "(BODY.PEEK[])")
        if typ != "OK" or not payload:
            return "读不到第 %s 封：%s" % (number, payload)
        raw = b""
        for part in payload:
            if isinstance(part, tuple):
                raw += part[1]
        if not raw:
            return "第 %s 封是空的或已被删除" % number
        msg = email.message_from_bytes(raw)
        body = _plain(msg)
        limit = max(200, int(max_chars))
        if len(body) > limit:
            body = body[:limit] + "\n…（正文共 %d 字，已截断）" % len(body)
        lines = [
            "账号：%s" % address,
            "时间：%s" % _when(msg.get("Date")),
            "发件人：%s" % _hdr(msg.get("From")),
            "收件人：%s" % _hdr(msg.get("To")),
            "主题：%s" % (_hdr(msg.get("Subject")) or "(无主题)"),
            "-" * 20,
            body or "(没有正文，可能是纯附件或 HTML 空壳)",
        ]
        return "\n".join(lines)
    finally:
        try:
            box.logout()
        except Exception:
            pass


__all__ = ["save_account", "list_accounts", "set_default", "remove_account", "status",
           "list_folders", "list_messages", "read_message"]
