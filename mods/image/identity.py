"""Stable identities for local, HTTP, and QQ-hosted image content."""

import base64
import hashlib
import os
import re
from urllib.parse import parse_qs, unquote, urlparse, urlunparse


def file_uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme.lower() != "file":
        raise ValueError("不是 file:// 图片 URI")
    if parsed.netloc not in ("", "localhost"):
        raise ValueError("file:// URI 不支持远程主机")
    path = unquote(parsed.path)
    if not os.path.isabs(path):
        raise ValueError("file:// URI 必须使用绝对路径")
    return path


def canonical_http_uri(uri: str) -> str:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("图片 URL 缺少有效协议或主机名")
    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    host = f"[{hostname}]" if ":" in hostname else hostname
    host_port = host if port is None or default_port else f"{host}:{port}"
    userinfo = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
    netloc = f"{userinfo}@{host_port}" if userinfo else host_port
    return urlunparse((scheme, netloc, parsed.path or "/", "", parsed.query, ""))


def image_uri_alias_key(uri: str) -> str:
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("图片 URI 不能为空")
    uri = uri.strip()
    parsed = urlparse(uri)
    if parsed.scheme.lower() == "file":
        path = os.path.abspath(os.path.expanduser(file_uri_to_path(uri)))
        try:
            stat = os.stat(path)
        except OSError as error:
            raise ValueError("图片文件不存在或无法读取") from error
        identity = f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}"
        return "file:" + hashlib.sha256(identity.encode()).hexdigest()
    canonical = canonical_http_uri(uri)
    parsed = urlparse(canonical)
    if parsed.hostname == "multimedia.nt.qq.com.cn" and parsed.path == "/download":
        params = parse_qs(parsed.query)
        appid, fileid = params.get("appid", [""])[0], params.get("fileid", [""])[0]
        if appid and fileid:
            return "qq:" + hashlib.sha256(f"{appid}\0{fileid}".encode()).hexdigest()
    return "url:" + hashlib.sha256(canonical.encode()).hexdigest()


def valid_image_digest(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_description_identity(value) -> bool:
    return valid_image_digest(value) or (
        isinstance(value, str)
        and re.fullmatch(r"(?:sha1:[0-9a-f]{40}|md5:[0-9a-f]{32})", value) is not None
    )


def _read_varint(data: bytes, position: int) -> tuple[int, int]:
    value = shift = 0
    while position < len(data):
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, position
        shift += 7
    raise ValueError("截断的 protobuf varint")


def _multimedia_fileid_sha1(fileid: str) -> str | None:
    try:
        data = base64.urlsafe_b64decode(fileid + "=" * (-len(fileid) % 4))
        position = 0
        while position < len(data):
            key, position = _read_varint(data, position)
            field, wire_type = key >> 3, key & 7
            if wire_type == 0:
                _, position = _read_varint(data, position)
            elif wire_type == 1:
                position += 8
            elif wire_type == 2:
                length, position = _read_varint(data, position)
                if position + length > len(data):
                    return None
                value = data[position:position + length]
                position += length
                if field == 2 and length == 20:
                    return value.hex()
            elif wire_type == 5:
                position += 4
            else:
                return None
    except (IndexError, TypeError, ValueError):
        return None
    return None


def intrinsic_image_description_identity(uri: str) -> str | None:
    if not isinstance(uri, str):
        return None
    parsed = urlparse(uri)
    hostname = (parsed.hostname or "").lower()
    if hostname == "multimedia.nt.qq.com.cn":
        sha1 = _multimedia_fileid_sha1(parse_qs(parsed.query).get("fileid", [""])[0])
        return f"sha1:{sha1}" if sha1 else None
    if hostname == "gchat.qpic.cn":
        match = re.search(r"/0-0-([0-9A-Fa-f]{32})/", parsed.path)
        return f"md5:{match.group(1).lower()}" if match else None
    return None


def hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
