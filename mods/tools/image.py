"""看图与画图：请视觉模型认图，或者按描述/参考图画一张新图出来。

## 图片链接怎么传

`image_uri` / `image_uris` 只认 `http://`、`https://` 或 `file://` 开头的完整地址。聊天窗口里别人发的图，进到你眼前时已经从 CQ 码转成了现成的图片链接，直接把那串链接原样复制过来就行，千万别自己去改写或硬凑。如果是 `file://` 本机图片，得写宿主机上的绝对路径（需要你有 op 权限）。

## 认图（recognize_image）

把单张图交给视觉模型看（多张图的话建议去用 `vision__ask` 拼网格）。`prompt` 写得越具体越好：留空只会给你一段笼统的印象；你想知道什么就直接写明白，比如"把图里的字一字不落地抄下来"、"表格第三列的数字分别是多少"。

## 画图（create_image / create_image_from_references）

- **怎么选**：脑子里有画面直接凭空画，用 `create_image`；要是想照着某张图的画风、人物长相画，或者在原图基础上改图，就把原图地址（一行一个）传给 `create_image_from_references` 的 `image_uris`。
- **怎么发出去**：画好的图会存在临时缓存里（闲置 15 天可能会被清理），返回给你一个 `file://` 绝对路径，**不会自动发进聊天**。你要发给别人的话，先调 `type_word(target="g群号或u私聊号", text="[CQ:image,file=file:///绝对路径]")` 放进输入框，下一步再 `send`。因为图没直接递进你的眼睛里，发出去之前别凭想象去跟人吹嘘画出来的细节长啥样。
- **省着点画**：画一张图大概花 0.13 元，还得等上几十秒。所以对方没明确说要好几张时，`n` 保持默认的 1 就好；`quality` 保持 `auto`；尺寸目前固定是 `1024x1024`。万一画图报错了，拿一模一样的参数重试一般还是会卡住，直接告诉对方没画出来就好。"""

from contextlib import ExitStack
import base64
import binascii
import os
from pathlib import Path
import re

import requests

from mods import image as image_mod, llm, op


def recognize_image(image_uri: str, prompt: str = "") -> str:
    """按要求识别一张网络或本地图片，返回视觉模型给出的文字结果。

    @param
    image_uri: 完整图片 URI；聊天里的图片直接用消息中给出的链接，file:// 需要管理员权限
    prompt: 希望视觉模型完成的任务，例如"提取图中所有文字"；留空则给出笼统描述
    """
    if not re.match(r"^(?:https?|file)://", image_uri, re.I):
        return "图片识别失败：仅支持 http://、https:// 或 file:// 图片 URI"
    if image_uri.lower().startswith("file://") and not op.bot_is_op():
        return "图片识别失败：本地文件需要管理员权限"
    try:
        result = llm.get_client().describe_image(image_uri, prompt)
    except (OSError, ValueError) as error:
        return f"图片识别失败：{error}"
    return result or "图片识别失败：视觉模型未返回描述"


def _validate_generation(prompt: str, size: str, quality: str, n: int, output_format: str, prefix: str) -> str | None:
    if not isinstance(prompt, str) or not prompt.strip():
        return f"{prefix}失败：prompt 不能为空"
    if size != "1024x1024" or quality not in {"auto", "low", "medium", "high"} or output_format not in {"png", "jpeg", "webp"}:
        return f"{prefix}失败：参数不受支持"
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 10:
        return f"{prefix}失败：n 必须是 1 ~ 10 的整数"
    return None


def _store_generated_images(response: requests.Response) -> str:
    if not response.ok:
        return f"生图失败：API 返回 HTTP {response.status_code}"
    try:
        images = [item["b64_json"] for item in response.json().get("data", []) if isinstance(item, dict) and item.get("b64_json")]
    except (AttributeError, TypeError, ValueError):
        return "生图失败：API 返回了无法解析的结果"
    if not images:
        return "生图失败：API 未返回图片"

    # chat remains the sole owner of the usage entry and its cost lock.
    from mods import chat

    chat.inc_usage_cost(0.13 * len(images))
    saved = []
    for value in images:
        try:
            content = base64.b64decode(value, validate=True)
            path, _mime, _digest = image_mod.cache_image_bytes(content)
            saved.append(Path(path).resolve().as_uri())
        except (binascii.Error, OSError, ValueError) as error:
            return f"已生成 {len(images)} 张图片，已保存 {len(saved)} 张：{saved}；保存失败：{error}"
    return "已生成图片，本地路径：\n" + "\n".join(saved)


def create_image(prompt: str, size: str = "1024x1024", quality: str = "auto", n: int = 1, output_format: str = "png") -> str:
    """根据文字描述生成图片并返回本地 file:// 路径，不自动发送。

    @param
    prompt: 图像描述，写清主体、风格、构图、色调，越具体越好
    size: 图片尺寸 enum: ["1024x1024"]
    quality: 生成质量，越高越慢越贵 enum: ["auto", "low", "medium", "high"]
    n: 生成张数，1 到 10；每张单独计费，没有特别要求就用 1
    output_format: 图片格式 enum: ["png", "jpeg", "webp"]
    """
    if error := _validate_generation(prompt, size, quality, n, output_format, "生图"):
        return error
    base_url, api_key = os.getenv("BYTECAT_BASE_URL", "").rstrip("/"), os.getenv("BYTECAT_IMAGE_API_KEY")
    if not base_url or not api_key:
        return "生图失败：未配置图片 API"
    try:
        response = requests.post(f"{base_url}/images/generations", headers={"Authorization": f"Bearer {api_key}"}, json={"model": "gpt-image-2", "prompt": prompt.strip(), "size": size, "quality": quality, "n": n, "output_format": output_format}, timeout=(10, 300))
    except requests.RequestException as error:
        return f"生图失败：请求异常（{type(error).__name__}）"
    return _store_generated_images(response)


def create_image_from_references(prompt: str, image_uris: str, size: str = "1024x1024", quality: str = "auto", n: int = 1, output_format: str = "png") -> str:
    """以一张或多张参考图生成新图并返回本地 file:// 路径，不自动发送。

    @param
    prompt: 新图描述，说明要在参考图基础上得到什么
    image_uris: 参考图 URI，每行一个；聊天里的图片直接用消息中给出的链接，file:// 需要管理员权限
    size: 图片尺寸 enum: ["1024x1024"]
    quality: 生成质量，越高越慢越贵 enum: ["auto", "low", "medium", "high"]
    n: 生成张数，1 到 10；每张单独计费，没有特别要求就用 1
    output_format: 图片格式 enum: ["png", "jpeg", "webp"]
    """
    if error := _validate_generation(prompt, size, quality, n, output_format, "参考图生图"):
        return error
    uris = list(dict.fromkeys(uri.strip() for uri in image_uris.splitlines() if uri.strip()))
    if not uris:
        return "参考图生图失败：至少需要一个图片 URI"
    if any(uri.lower().startswith("file://") for uri in uris) and not op.bot_is_op():
        return "参考图生图失败：本地文件需要管理员权限"
    base_url, api_key = os.getenv("BYTECAT_BASE_URL", "").rstrip("/"), os.getenv("BYTECAT_IMAGE_API_KEY")
    if not base_url or not api_key:
        return "参考图生图失败：未配置图片 API"
    try:
        with ExitStack() as stack:
            files = []
            for index, uri in enumerate(uris, 1):
                path, mime = image_mod.resolve_image_uri(uri)
                stream = stack.enter_context(open(path, "rb"))
                files.append(("image[]", (os.path.basename(path) or f"reference-{index}", stream, mime)))
            response = requests.post(f"{base_url}/images/edits", headers={"Authorization": f"Bearer {api_key}"}, data={"model": "gpt-image-2", "prompt": prompt.strip(), "size": size, "quality": quality, "n": n, "output_format": output_format}, files=files, timeout=(10, 300))
    except (requests.RequestException, OSError, ValueError) as error:
        return f"参考图生图失败：{error}"
    return _store_generated_images(response)


__all__ = ["recognize_image", "create_image", "create_image_from_references"]
