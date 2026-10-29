"""倾听与分析音频：听取语音条、网络音频或本地录音，获取内容转录、说话者情绪与背景声音。

## 什么时候用这个工具

- 聊天画面里看到 `[语音: 5秒, file:///...]`（#audio lazy 档位），你想知道对方到底说了什么、是用什么语气说的时。
- 对方发来网络音频链接（http:// 或 https://），或者本地有录音文件需要分析时。
- 你想深入了解说话人的口吻（撒娇、生气、开玩笑、疲惫）、环境杂音（风声、打字声、笑声）时。

## 参数怎么填

- `audio_uri`：完整的音频地址，支持 `file://`（本地绝对路径）、`http://`、`https://`。
- `prompt`：你想侧重了解的任务（例如"听听对方是不是在哭"、"转录背景音乐的歌词"）；留空则按默认格式完整转录台词并分析语气与背景声。
- `model`：一般留空走默认的多模态视觉/音频模型；需要换特定模型时传入 `供应商/模型`。
"""

from __future__ import annotations

import os
import re

from mods import llm, op
from mods.image.identity import file_uri_to_path


def listen(audio_uri: str, prompt: str = "", model: str = "") -> str:
    """听取一段网络或本地音频，返回语音转录、语气情绪与背景声音分析。

    @param
    audio_uri: 完整的音频 URI，支持 http://、https:// 或本地 file:// 绝对路径
    prompt: 希望重点关注的问题或分析任务；留空则进行完整转录与语气/背景音分析
    model: 指定调用的模型（格式：供应商/模型）；留空使用默认多模态模型
    """
    audio_uri = str(audio_uri or "").strip()
    if not audio_uri:
        return "音频识别失败：audio_uri 不能为空"

    if not re.match(r"^(?:https?|file)://", audio_uri, re.I) and not os.path.isfile(audio_uri):
        return "音频识别失败：仅支持 http://、https:// 或 file:// 音频 URI"

    if audio_uri.lower().startswith("file://") and not op.bot_is_op():
        try:
            path = file_uri_to_path(audio_uri)
            # 允许读取 data/tmp_files 下的合法缓存文件，其他本地路径需要管理员权限
            if not os.path.abspath(path).startswith(os.path.abspath("data/tmp_files")):
                return "音频识别失败：访问非临时缓存目录下的本地文件需要管理员权限"
        except Exception as err:
            return f"音频识别失败：路径非法 ({err})"

    try:
        client = llm.get_client()
        result = client.describe_audio(
            audio_uri,
            prompt=prompt.strip(),
            model=model.strip() or None,
        )
        return result or "音频识别失败：模型未返回识别结果"
    except Exception as error:
        return f"音频识别失败：{type(error).__name__}: {error}"


__all__ = ["listen"]
