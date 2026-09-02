"""识别图片、按文字描述生成图片、按参考图生成图片。

## 图片 URI 从哪来

`image_uri` / `image_uris` 只接受 `http://`、`https://` 或 `file://` 开头的完整 URI。用户在聊天里发的图片，进入模型时已经由 CQ 码转成了图片链接，直接把那个链接原样传进来，不要自己拼接、猜测或改写。`file://` 必须是宿主机上的绝对路径，且只有管理员可用。

## 识别

`recognize_image` 把一张图交给视觉模型并返回文字结果。`prompt` 决定它做什么：留空只得到笼统描述，需要具体信息就明确写出来，例如"把图里的文字全部转成文本"、"这张表第三列的数值分别是多少"、"图中有几个人"。一次只处理一张图，多张图分别调用。

## 生成

两个生成函数**不会把图片返回给你**，而是直接发送到当前聊天，返回值只是"已生成并发送 N 张图片"之类的回执。你看不到成品，所以不要在回复里描述生成结果长什么样，也不要为了"看看效果"重复生成。

`create_image_from_references` 用 `image_uris`（每行一个 URI）提供参考的人物、风格或待修改的原图，`prompt` 写想要的新画面；没有参考图时用 `create_image`。

生成是收费操作（每张约 0.13 元）并且要等几十秒：`n` 默认 1，用户没明确要多张就别改；`quality` 越高越慢越贵，一般保持 `auto`；`size` 目前只支持 `1024x1024`。

失败时返回以"生图失败："或"参考图生图失败："开头的原因，同样的参数重试一般还是失败，如实转告用户即可。
"""

from contextlib import ExitStack
import os
import re
import traceback

import requests

from mods import context, cq, image as image_mod, llm, message, op


def recognize_image(image_uri: str, prompt: str = "") -> str:
    """按要求识别一张网络或本地图片，返回视觉模型给出的文字结果。

    @param
    image_uri: 完整图片 URI；聊天里的图片直接用消息中给出的链接，file:// 需要管理员权限
    prompt: 希望视觉模型完成的任务，例如"提取图中所有文字"；留空则给出笼统描述
    """
    if not re.match(r"^(?:https?|file)://", image_uri, re.I):
        return "图片识别失败：仅支持 http://、https:// 或 file:// 图片 URI"
    if image_uri.lower().startswith("file://") and not op.require_op(context.current()):
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


def _send_generated_images(response: requests.Response) -> str:
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
    sent = 0
    for value in images:
        try:
            message.sendmsg(cq.base64_to_cq(value))
            sent += 1
        except Exception:
            traceback.print_exc()
    if sent != len(images):
        return f"已生成 {len(images)} 张图片，成功发送 {sent} 张"
    return f"已生成并发送 {sent} 张图片"


def create_image(prompt: str, size: str = "1024x1024", quality: str = "auto", n: int = 1, output_format: str = "png") -> str:
    """根据文字描述生成图片并直接发送到当前聊天，返回发送回执而不是图片本身。

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
    return _send_generated_images(response)


def create_image_from_references(prompt: str, image_uris: str, size: str = "1024x1024", quality: str = "auto", n: int = 1, output_format: str = "png") -> str:
    """以一张或多张参考图生成新图并直接发送到当前聊天，返回发送回执而不是图片本身。

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
    if any(uri.lower().startswith("file://") for uri in uris) and not op.require_op(context.current()):
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
    return _send_generated_images(response)


__all__ = ["recognize_image", "create_image", "create_image_from_references"]
