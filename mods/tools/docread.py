"""读文档和网页：把图片／PDF／Word／PPT 里的字抠出来，或者把一个链接抓成 Markdown 正文。

两个工具都用 DeepSeek 的平台工具接口（和模型费分开按次／按页计费，很便宜）：

- `docread__ocr`：`POST /v1/tools/ocr`。适合读群里的截图、验证码、扫描件、办公文档。
  一页一厘半（1.5 元/千页）。**别再拿视觉模型转述图片里的字**——那个一张一笔钱，还只能听转述。
- `docread__read_url`：`POST /v1/tools/fetch`。直接把网页抓成 Markdown 正文，比拉起浏览器快得多。
  一次五厘（5 元/千次）。**它不执行 JavaScript，遇到反爬、验证码、要登录的页面会失败**——
  那种时候换 `browser__open_page` 那套去打开，别在这儿死磕。

用得着的地方：别人甩来一个链接要我看内容；群里一张截图要我读上面的字；一份 PDF／Word 要我提取文字。
用不着的地方：手上已经有正文的、你自己记得的常识、纯图片没有字（那归识图，不归 OCR）。
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import requests


_BASE = "https://api.deepseek.com"
_OCR_URL = _BASE + "/v1/tools/ocr"
_FETCH_URL = _BASE + "/v1/tools/fetch"
_TIMEOUT = 120
_MAX_PAGE_CHARS = 4000
_MAX_CONTENT = 6000


def _api_key() -> str:
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("没有 DEEPSEEK_API_KEY")
    return key


def _error_text(error: Exception) -> str:
    """把 HTTP 错误里的 JSON 说明抠出来，别只丢一个状态码。"""
    if isinstance(error, urllib.error.HTTPError):
        try:
            body = error.read().decode("utf-8", "ignore")
            detail = json.loads(body)
            message = detail.get("error", {}).get("message") or detail.get("message") or body
        except Exception:
            message = str(error)
        return f"HTTP {error.code}：{message}"
    return f"{type(error).__name__}: {error}"


def _guess_name(url_or_path: str, data: bytes) -> str:
    """取个带扩展名的文件名——OCR 按扩展名认格式，名字丢了会判错类型。"""
    raw = url_or_path.split("?")[0].split("#")[0].rstrip("/")
    name = raw.split("/")[-1] or "file"
    if "." in name:
        return name
    head = data[:8]
    if head.startswith(b"%PDF"):
        return name + ".pdf"
    if head.startswith(b"\x89PNG"):
        return name + ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return name + ".jpg"
    if head.startswith(b"PK"):
        return name + ".docx"
    return name


def _load_bytes(target: str) -> tuple[str, bytes]:
    """本地路径 / file:// / http(s) 统一读成 (文件名, 字节)。"""
    text = str(target or "").strip()
    if not text:
        raise ValueError("路径为空")
    if text.startswith(("http://", "https://")):
        request = urllib.request.Request(text, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = response.read()
        return _guess_name(text, data), data
    if text.startswith("file://"):
        text = urllib.request.url2pathname(text[7:])
    path = Path(text)
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{text}")
    data = path.read_bytes()
    return _guess_name(path.name, data), data


def ocr(file: str, purpose: str = "text_recognition") -> str:
    """识别图片／PDF／Word／PPT 里的文字，按页返回（一页一厘半）。

    @param
    file: 本地路径、file:// 或 http(s) 链接；支持 png/jpg/pdf/doc/docx/ppt/pptx
    purpose: text_recognition（默认：Word/PPT 直接提取文字，其他逐页 OCR）或 rich_layout_recognition（所有文件都逐页 OCR）
    """
    try:
        name, data = _load_bytes(file)
        response = requests.post(
            _OCR_URL,
            headers={"Authorization": f"Bearer {_api_key()}"},
            files={"file": (name, data)},
            data={"purpose": purpose or "text_recognition"},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return f"OCR 失败：HTTP {response.status_code}：{response.text[:500]}"
        result = response.json()
    except Exception as error:
        return f"OCR 失败：{_error_text(error)}"

    pages = result.get("pages") or []
    count = result.get("page_count") or len(pages)
    lines = [f"识别完成：共 {count} 页（{result.get('purpose') or purpose}，计费约 {count} 页 = {count * 0.0015:.3f} 元）"]
    if not pages:
        lines.append("（没有识别出内容）")
        return "\n".join(lines)
    for page in pages:
        index = int(page.get("index", 0)) + 1
        text = (page.get("text") or "").strip()
        note = ""
        if page.get("finish_reason") == "length":
            note = "（这一页输出到上限，文本不完整）"
        if len(text) > _MAX_PAGE_CHARS:
            text = text[:_MAX_PAGE_CHARS] + f"…（本页还有 {len(text) - _MAX_PAGE_CHARS} 字没展开）"
        lines.append(f"【第 {index} 页】{note}")
        lines.append(text or "（本页没有文字）")
    return "\n".join(lines)


def read_url(url: str, start: int = 0) -> str:
    """把一个网页抓成 Markdown 正文（一次五厘；不跑 JavaScript，反爬或要登录请改用 browser__open_page）。

    @param
    url: http(s) 链接
    start: 从第几个字开始读（正文很长时分段读，返回里会告诉你下次从哪接）
    """
    text = str(url or "").strip()
    if not text:
        return "读取失败：url 为空"
    payload = json.dumps({"url": text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _FETCH_URL,
        data=payload,
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8", "ignore"))
    except Exception as error:
        return (
            f"读取失败：{_error_text(error)}\n"
            "→ 如果这个站要登录或有反爬（验证码、人机校验），换 `browser__open_page` 试试。"
        )

    title = (result.get("title") or "").strip()
    content = (result.get("content") or "").strip()
    if not content:
        return f"读取到了，但正文是空的：{title or text}"
    offset = max(0, int(start or 0))
    chunk = content[offset:offset + _MAX_CONTENT]
    head = f"【{title or '(无标题)'}】"
    tail = ""
    if offset + _MAX_CONTENT < len(content):
        tail = f"\n…（共 {len(content)} 字，已到第 {offset + _MAX_CONTENT} 字；接着读用 start={offset + _MAX_CONTENT}）"
    return f"{head}\n{chunk}{tail}"


__all__ = ["ocr", "read_url"]
