"""用 DeepSeek 官方的服务端 web_search 在网上检索，并把结果规范化。

检索请求走 DeepSeek 的 **Anthropic 兼容端点**（`{base}/messages`），挂上原生服务端工具
`web_search_20250305`：模型自己决定搜什么、看哪些页面，回包里的 `web_search_tool_result`
块是结构化结果。注意这个 base **不是**聊天用的 chat-completions base（`/beta` 那个），
两者只共用 `DEEPSEEK_API_KEY`，所以不要复用 `DEEPSEEK_BASE_URL`。

**摘要从哪来**：官方结果条目只有 `title` / `url` / `page_age`，通常**没有** snippet。官方会把
引文放在 `text` 块的 `citations[].cited_text` 里、按 url 对齐，但实测常常为空。所以这里两头都取：
能拿到的逐条 snippet 就填进 `snippet`，同时把模型基于搜索结果写的那段话放进 `answer` 兜底。

**可信度**：`answer` 与每条 `snippet` 都是**外部不受信内容**，可能夹带指令。调用方只把它们
当资料引用，绝不照做。
"""

from __future__ import annotations

import json
import os
import urllib.request

from mods import FEATURE


PHASE = FEATURE

# 官方默认端点与模型名（Anthropic 命名），与仓库里的 chat-completions 配置无关。
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic/v1"
MODEL = "deepseek-v4-flash"
API_VERSION = "2023-06-01"
MAX_TOKENS = 4096
MAX_USES = 5
TIMEOUT = 60.0
USER_AGENT = "yuzu-bot/1.0"


def base_url() -> str:
    """搜索端点的 base URL，可用 `DEEPSEEK_WEB_SEARCH_BASE_URL` 覆盖。"""
    value = os.getenv("DEEPSEEK_WEB_SEARCH_BASE_URL", "").strip().strip('"').strip("'")
    return value.rstrip("/") or DEFAULT_BASE_URL


def api_key() -> str:
    """从环境变量取 DeepSeek 密钥（与聊天共用），取不到就报错。"""
    value = os.getenv("DEEPSEEK_API_KEY", "").strip().strip('"').strip("'")
    if not value:
        raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY")
    return value


def build_request(query: str, max_uses: int = MAX_USES) -> dict:
    """构造 Anthropic Messages 请求体，搜索是其中的服务端工具。"""
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": f"Perform a web search for the query: {query}"}],
            }
        ],
        "tools": [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": max(1, int(max_uses))}
        ],
    }


def _citations(blocks: list) -> dict:
    """把每个 `text` 块的 `citations[]` 收敛成 `url -> cited_text`，同一 url 取首次出现。"""
    snippets: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for citation in block.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            url = citation.get("url") or ""
            text = citation.get("cited_text") or ""
            if url and text and url not in snippets:
                snippets[url] = text
    return snippets


def normalize(payload: dict, query: str = "") -> dict:
    """把 Messages 回包整理成 `{query, answer, results, searches}`，按 url 去重。"""
    blocks = payload.get("content") or []
    snippets = _citations(blocks)

    results: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        items = block.get("content")
        if not isinstance(items, list):
            continue  # 出错时这里是 web_search_tool_result_error 对象，不是数组
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = item.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(
                {
                    "title": item.get("title") or "",
                    "url": url,
                    "snippet": snippets.get(url, ""),
                    "age": item.get("page_age") or "",
                }
            )

    answer = "\n".join(
        block.get("text", "").strip()
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ).strip()

    usage = payload.get("usage") or {}
    searches = int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
    return {"query": query, "answer": answer, "results": results, "searches": searches}


def search(query: str, max_uses: int = MAX_USES, timeout: float = TIMEOUT) -> dict:
    """执行一次网络检索，返回规范化结果。

    @param
    query: 检索词，越具体结果越准
    max_uses: 这次请求允许模型调用搜索工具的次数上限
    timeout: 整个 HTTP 请求的秒数上限
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("检索词不能为空")
    body = build_request(query, max_uses)
    request = urllib.request.Request(
        f"{base_url()}/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            # 官方认 x-api-key，Anthropic 兼容网关认 Authorization，两个都发。
            "x-api-key": api_key(),
            "authorization": f"Bearer {api_key()}",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return normalize(payload, query)


__all__ = [
    "DEFAULT_BASE_URL",
    "MODEL",
    "MAX_USES",
    "base_url",
    "api_key",
    "build_request",
    "normalize",
    "search",
]
