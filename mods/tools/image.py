"""提供图片识别、文字生图和参考图生图能力。"""

from contextlib import ExitStack
import os
import re

import requests

from mods import context, cq, image as image_mod, llm, message, op


def recognize_image(image_uri: str, prompt: str = "") -> str:
    """按要求识别网络或本地图片。

    @param
    image_uri: http://、https:// 或 file:// 图片 URI
    prompt: 希望视觉模型完成的任务
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
    for value in images:
        message.sendmsg(cq.base64_to_cq(value))
    if images:
        # chat remains the sole owner of the usage entry and its cost lock.
        from mods import chat

        chat.inc_usage_cost(0.13 * len(images))
    return f"已生成并发送 {len(images)} 张图片" if images else "生图失败：API 未返回图片"


def create_image(prompt: str, size: str = "1024x1024", quality: str = "auto", n: int = 1, output_format: str = "png") -> str:
    """根据文字描述生成图片并发送。

    @param
    prompt: 图像描述
    size: 图片尺寸 enum: ["1024x1024"]
    quality: 质量 enum: ["auto", "low", "medium", "high"]
    n: 图片数量
    output_format: 格式 enum: ["png", "jpeg", "webp"]
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
    """使用参考图片生成新图并发送。

    @param
    prompt: 新图描述
    image_uris: 每行一个图片 URI
    size: 图片尺寸 enum: ["1024x1024"]
    quality: 质量 enum: ["auto", "low", "medium", "high"]
    n: 图片数量
    output_format: 格式 enum: ["png", "jpeg", "webp"]
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
