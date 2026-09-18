"""保存 TOTP 种子并算出当前的动态验证码，供登录时做两步验证用。

种子只落在宿主机 data/yuzu_totp.json（权限 600），不回显明文、不写进聊天；对外只给名字
和一段指纹（blake2s 前 8 位），用来确认两次说的是同一个种子。

## 用法

1. `save(name, secret)` 存种子。`secret` 可以是 base32 明文（空格、连字符可有可无），
   也可以是 `otpauth://totp/...` 链接，或者一段原始 otpauth 文本。
2. `code(name)` 算当下的码，返回形如 `123456（还剩 17s / 30s）`。码是现算的，不要抄下来留用。
3. `list_all()` 看存了哪些（名字、指纹、位数、周期），`remove(name)` 删掉。

## 边界

这是纯本地计算，不联网；时间以宿主机 UTC 为准，宿主机时钟漂了就会算错，报错先核对时间。
明文种子属于凭据：别把 `secret` 的内容复述进聊天，也别在群里报完整的码。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
from urllib.parse import parse_qs, unquote, urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PATH = os.path.join(_ROOT, "data", "yuzu_totp.json")

_ALGOS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(items: dict) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(items, handle, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _PATH)


def _fingerprint(secret: str) -> str:
    return hashlib.blake2s(secret.encode("utf-8"), digest_size=4).hexdigest()


def _normalize(secret: str) -> str:
    return re.sub(r"[\s\-]", "", secret).upper()


def _parse(secret: str) -> dict:
    raw = secret.strip().strip("`").strip()
    if raw.lower().startswith("otpauth://"):
        url = urlparse(raw)
        query = parse_qs(url.query)
        label = unquote(url.path.lstrip("/"))
        return {
            "secret": _normalize(query.get("secret", [""])[0]),
            "label": label,
            "issuer": query.get("issuer", [""])[0],
            "digits": int(query.get("digits", ["6"])[0]),
            "period": int(query.get("period", ["30"])[0]),
            "algorithm": query.get("algorithm", ["SHA1"])[0].upper(),
        }
    return {"secret": _normalize(raw), "label": "", "issuer": "", "digits": 6, "period": 30,
            "algorithm": "SHA1"}


def _hotp(key: bytes, counter: int, digits: int, algorithm: str) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), _ALGOS[algorithm]).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def save(name: str, secret: str) -> str:
    """保存或覆盖一个 TOTP 种子，返回名字与指纹（不回显明文）。

    @param
    name: 便于识别的名字，例如 google
    secret: base32 明文或 otpauth:// 链接或原始 otpauth 文本
    """
    parsed = _parse(secret)
    if not parsed["secret"]:
        return "没读到种子：请给 base32 明文或 otpauth:// 链接"
    if parsed["algorithm"] not in _ALGOS:
        return f"不认识的算法 {parsed['algorithm']}，只支持 " + "、".join(_ALGOS)
    try:
        base64.b32decode(parsed["secret"] + "=" * (-len(parsed["secret"]) % 8))
    except Exception:
        return "种子不是合法的 base32，请核对后重给（字母 A-Z、数字 2-7）"
    items = _load()
    parsed["saved_at"] = int(time.time())
    items[name.strip()] = parsed
    _store(items)
    return (f"已存 {name.strip()}｜指纹 {_fingerprint(parsed['secret'])}"
            f"｜{parsed['digits']} 位 / {parsed['period']}s / {parsed['algorithm']}")


def code(name: str) -> str:
    """算出某个种子此刻的验证码，返回码与剩余秒数。

    @param
    name: 保存时用的名字
    """
    item = _load().get(name.strip())
    if not item:
        return f"没有叫 {name.strip()} 的种子，先 save 或看 list_all"
    key = base64.b32decode(item["secret"] + "=" * (-len(item["secret"]) % 8))
    now = time.time()
    period = int(item["period"])
    remaining = period - int(now % period)
    current = _hotp(key, int(now // period), int(item["digits"]), item["algorithm"])
    previous = _hotp(key, int(now // period) - 1, int(item["digits"]), item["algorithm"])
    return f"{current}（还剩 {remaining}s / {period}s；上一枚 {previous}）"


def list_all() -> str:
    """列出所有已保存的种子：名字、指纹、位数、周期（绝不含明文种子）。"""
    items = _load()
    if not items:
        return "还没有存任何种子"
    lines = []
    for name, item in sorted(items.items()):
        lines.append(f"{name}｜指纹 {_fingerprint(item['secret'])}"
                     f"｜{item['digits']} 位 / {item['period']}s / {item['algorithm']}"
                     f"｜{item.get('issuer') or item.get('label') or '-'}")
    return "\n".join(lines)


def remove(name: str) -> str:
    """删除一个种子。

    @param
    name: 保存时用的名字
    """
    items = _load()
    if name.strip() not in items:
        return f"没有叫 {name.strip()} 的种子"
    items.pop(name.strip())
    _store(items)
    return f"已删除 {name.strip()}"


__all__ = ["save", "code", "list_all", "remove"]
