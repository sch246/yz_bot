"""CQ-code parsing, escaping, and image CQ construction."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
from pathlib import Path
import re
import shutil
import threading
import time

from mods import text


IMAGE_PATH = Path("data/images")
TEMP_PATH = Path("data/tmp_files")
REQUEST_TIMEOUT = 15

_inside = {"&": "&amp;", "[": "&#91;", "]": "&#93;", ",": "&#44;"}
_outside = {"&": "&amp;", "[": "&#91;", "]": "&#93;"}


def escape(value: str) -> str:
    return text.replace_by_dic(value, _inside)


def unescape(value: str) -> str:
    return text.replace_by_dic2(value, _inside)


def escape2(value: str) -> str:
    return text.replace_by_dic(value, _outside)


def unescape2(value: str) -> str:
    return text.replace_by_dic2(value, _outside)


_data_pattern = r"(?:,[^,=]+=[^,\]]*)*"
_cq = re.compile(rf"\[CQ:[^,\]]+{_data_pattern}\]")
re_CQ = re.compile(rf"\[CQ:(?P<type>[^,\]]+)(?P<data>{_data_pattern})\]")


def find_all(value: str) -> list[str]:
    return _cq.findall(value)


def load(value: str) -> dict:
    """Parse one complete CQ code into its ordinary dict representation."""
    value = re.sub(r"\s", "", value)
    match = re_CQ.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid CQ code: {value!r}")
    raw_data = match.group("data")
    data: dict[str, str] = {}
    if raw_data:
        for item in raw_data[1:].split(","):
            item = unescape(item)
            key, separator, item_value = item.partition("=")
            if not separator:
                raise ValueError(f"invalid CQ data item: {item!r}")
            data[key] = item_value
    return {"type": match.group("type"), "data": data}


def at2qq(value):
    """Return the QQ identifier carried by one at CQ code."""
    return load(value)["data"]["qq"]


def dump(value: dict) -> str:
    cq_type = value["type"]
    data = "".join(f",{escape(str(key))}={escape(str(item))}" for key, item in value.get("data", {}).items())
    return f"[CQ:{cq_type}{data}]"


def cq(cq_type: str, **data) -> str:
    return dump({"type": cq_type, "data": data})


_file_lock = threading.Lock()


def _image_extension(content: bytes, content_type: str = "") -> str:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            image_format = (image.format or "").lower()
    except Exception as error:
        raise ValueError("downloaded data is not a recognized image") from error
    extension = {
        "jpeg": ".jpg",
        "png": ".png",
        "gif": ".gif",
        "webp": ".webp",
    }.get(image_format)
    if extension is None:
        raise ValueError(f"unsupported image format: {image_format or content_type}")
    return extension


def download_img(url: str, name: str | None = None, temp: bool = True) -> str:
    """Resolve one image through the shared content-addressed image path."""
    from mods import get_available

    image = get_available("image")
    if image is None:
        raise RuntimeError("image resolver is unavailable")
    if not temp and name:
        for candidate in (
            Path(name).name,
            Path(name).stem + ".jpg",
            Path(name).stem + ".png",
            Path(name).stem + ".gif",
            Path(name).stem + ".webp",
        ):
            existing = IMAGE_PATH / candidate
            if existing.is_file():
                return str(existing)

    source, _mime, digest = image.resolve_image_with_digest(url)
    if temp:
        return source

    IMAGE_PATH.mkdir(parents=True, exist_ok=True)
    extension = Path(source).suffix
    if name:
        filename = Path(name).stem + extension
    else:
        filename = time.strftime("%Y-%m-%d-%H%M%S") + "-" + digest[:8] + extension
    path = IMAGE_PATH / filename
    with _file_lock:
        if not path.exists():
            shutil.copyfile(source, path)
    return str(path)


def url2cq(url: str, name: str | None = None, temp: bool = True) -> str:
    path = Path(download_img(url, name, temp)).absolute().as_posix()
    return dump({"type": "image", "data": {"file": f"file://{path}"}})


def base64_to_cq(image_base64: str) -> str:
    try:
        content = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, TypeError, ValueError) as error:
        raise ValueError("invalid Base64 image data") from error
    extension = _image_extension(content)
    TEMP_PATH.mkdir(parents=True, exist_ok=True)
    path = TEMP_PATH / (hashlib.sha256(content).hexdigest() + extension)
    with _file_lock:
        if not path.exists():
            path.write_bytes(content)
    return dump({"type": "image", "data": {"file": f"file://{path.absolute().as_posix()}"}})


def save_pic(value: str) -> str:
    def replace(match: re.Match) -> str:
        original = match.group(0)
        parsed = load(original)
        if parsed["type"] != "image" or not parsed["data"].get("url"):
            return original
        try:
            return url2cq(parsed["data"]["url"], temp=False)
        except Exception:
            return original

    return re_CQ.sub(replace, value)
