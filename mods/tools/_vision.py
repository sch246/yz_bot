'''把图片交给**当前模型自己**看（工具结果里带图优先，退回时排进下一个子请求）。

## 两条路

1. **工具结果里带图**（首选）。工具结果的正文里写一行 markdown 图片引用
   （感叹号 + `[说明](地址)` 那种写法），`llm._convert_images` 在发给 provider 之前
   把它展开成 image part。对端认不认，看模型能力位 `tool_images`——
   OpenAI 与 DeepSeek 的文档都写着“图片仅支持出现在 user 消息中”，
   而 DeepSeek 的 chat/completions 实收 tool 消息里的 image_url（2026-09-18 实测
   deepseek-flash 读对了工具结果那张图里的随机码）。所以它是按模型登记的能力，不是协议
   保证的。

   这条路的收益是图**长在工具调用的位置上**：经历日志（mods/chat/journal）原样存下它，重建回
   上下文时还带着，"哪次调用看到了哪张图"不必靠记忆。本次运行内一直带图；被以后的运行
   借去时只保留地址与已有描述，要重新看可以再次 attach。

   引用一律写**地址**，不内联 base64：工具结果会原样落进操作记录，一段几百 KB 的 base64
   会跟着每一次重建一起膨胀。所以 `data:` 图先落盘再引用。

2. **排进下一个子请求**（退回）。对端不支持 tool 结果带图时，把图作为一条 user 消息挂在
   本次运行的 images 队列上，由统一 run 在重建请求后取走。
   这条消息不写 chatlog、不进经历日志和视图，只活一次子请求。

## 结果约定

`attach()` 成功一律以 `✅` 开头，失败以 `⚠️` 开头并说明原因。调用方按前缀判断能不能走
"自己看图"这条路，`⚠️` 时退回原来的"让视觉模型转述"。

## 谁可以调用

交给模型看要有 binding（`mods.tools.current_binding()`，由工具绑定函数
在每次工具调用期间设好）。拿不到就说明这次调用不在绑定会话里，返回 `⚠️`，不抛异常。
'''

from __future__ import annotations

import base64
import hashlib
import os
import re

from mods import log


_stream = log.stream("tools")
_DATA_URI = re.compile(r"^data:image/([A-Za-z0-9.+-]+);base64,(.*)$", re.S)
_EXTENSIONS = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "bmp": ".bmp"}


def _brief(selection: str) -> str:
    """把地址折成一句人话，别让 data URI 的整段 base64 落进日志或工具结果里。

    WHY: 截图压缩后仍是几百 KB 的 base64，写进日志会刷屏，写进返回值会白占上下文。
    """
    if selection.startswith("data:"):
        head, _, payload = selection.partition(",")
        mime = head[5:].split(";")[0] or "image"
        return f"{mime} 内联图（约 {len(payload) * 3 // 4 // 1024} KB）"
    return selection


def _file_uri(selection: str) -> str:
    """把图片来源变成一条能写进工具结果的**地址引用**：`data:` 落盘，其余原样返回。

    WHY: 只有 data URI 必须落盘。落盘名用内容摘要，同一张图重复附加不会堆出第二份；
    文件名形态与图片内容缓存一致（64 位十六进制 + 扩展名）。
    """
    matched = _DATA_URI.match(selection)
    if matched is None:
        if os.path.isfile(selection):
            from pathlib import Path
            return Path(selection).resolve().as_uri()
        return selection
    try:
        from mods import image as image_module

        directory = image_module.TEMP_PATH
    except Exception:
        directory = "data/tmp_files"
    content = base64.b64decode(matched.group(2), validate=False)
    suffix = _EXTENSIONS.get(matched.group(1).lower(), ".img")
    path = os.path.join(directory, hashlib.sha256(content).hexdigest() + suffix)
    try:
        os.makedirs(directory, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "wb") as handle:
                handle.write(content)
    except OSError as error:
        _stream.info("data URI 落盘失败，改用内联图：%s", error)
        return selection
    return "file://" + os.path.abspath(path)


def attach(uri: str, note: str = "", model: str | None = None) -> str:
    """把一张图交给当前模型自己看，或者在当前模型无视觉/指定了视觉模型时转述，返回一句以 ✅ 或 ⚠️ 开头的结果说明。

    @param
    uri: 图片地址，`file://`、`http(s)://` 或已经是 `data:` 都行
    note: 附在图前面的一句话，例如"这是浏览器当前页面的截图"
    model: 可选。指定调用外部视觉模型转述（如 `azure/gpt-5.6-luna`、`cheap` 等）；若指定则由该模型转述并返回文字结果
    """
    selection = str(uri or "").strip()
    if not selection:
        return "⚠️ 没有给出图片地址"

    explicit_model = str(model or "").strip()
    if explicit_model:
        from mods import llm
        from mods.tools import vision as vision_tool

        target = vision_tool.ALIASES.get(explicit_model.lower(), explicit_model)
        prompt = str(note or "").strip() or "如实描述图中内容；有文字就逐字抄出，有表格就按行列说清。"
        try:
            client = llm.get_client()
            desc = client.describe_image(selection, prompt=prompt, model=target)
            if desc:
                return f"✅ 已由指定视觉模型（{target}）转述：\n{desc}"
            return f"⚠️ 指定视觉模型（{target}）未返回有效内容"
        except Exception as error:
            return f"⚠️ 调用指定视觉模型（{target}）失败（{type(error).__name__}: {error}）"

    try:
        from mods.tools import current_binding

        binding = current_binding()
        session = binding["session"]
        state = binding["run"]
    except Exception as error:
        return f"⚠️ 这次调用拿不到会话（{type(error).__name__}: {error}），附加不了图片"
    current_model = str(getattr(session, "model", "") or "")
    try:
        from mods import llm

        client = llm.get_client()
        capabilities = client.get_model_capabilities(current_model)
    except Exception as error:
        return f"⚠️ 取不到模型能力（{type(error).__name__}: {error}），附加不了图片"
    if not capabilities.vision:
        fallback_model = client.get_vision_model() or "bytecat/gpt-5.4-mini"
        prompt = str(note or "").strip() or "如实描述图中内容；有文字就逐字抄出，有表格就按行列说清。"
        try:
            desc = client.describe_image(selection, prompt=prompt, model=fallback_model)
            if desc:
                return f"✅ 当前模型不支持视觉，已自动由默认视觉模型（{fallback_model}）转述：\n{desc}"
            return f"⚠️ 当前模型不支持视觉，自动调用视觉模型（{fallback_model}）未返回有效内容"
        except Exception as error:
            return f"⚠️ 当前模型不支持视觉，自动调用视觉模型失败（{type(error).__name__}: {error}）"
    text = str(note or "").strip()
    try:
        from mods import image

        reference = _file_uri(selection)
        value = selection if selection.startswith("data:") else image.image_uri_to_data_uri(reference)
        pictures, explanation = image.prepare_image_data_uri(value, gif_mode="grid")
        if "GIF" in explanation:
            references = [_file_uri(picture) for picture in pictures]
            text = "\n".join(part for part in (text, explanation) if part)
        else:
            references = [reference]
    except Exception as error:
        return f"⚠️ 图片读取失败（{type(error).__name__}: {error}）"
    if capabilities.tool_images:
        _stream.info("图片已写进工具结果：%s", _brief(reference))
        head = f"✅ 图片已随这条工具结果一起送到（{_brief(reference)}），就在下面这一行："
        return "\n".join(part for part in (head, text, *(f"![图片]({uri})" for uri in references)) if part)
    # WHY: 对端不收 tool 图时用显式标记放行 user 附图，#image off 也能看（那一档只管自动看
    # 聊天图）。消息里存地址，由 llm 发送前再编码。
    parts = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend({"type": "image_url", "image_url": {"url": uri}} for uri in references)
    # WHY: 图片只属于这次运行，不能被另一个并发请求取走；统一 run 在重建请求后
    # 消费一次，不写进经历日志或持久视图。要再看就再 attach，结果里留着地址。
    state.setdefault("images", []).append({"role": "user", "content": parts, "_tool_image": True})
    _stream.info("图片已排入下一次请求：%s", _brief(selection))
    return f"✅ 图片已附加到下一次请求（{_brief(reference)}），你马上就能看到它"


__all__ = ["attach"]
