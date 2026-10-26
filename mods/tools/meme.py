"""你自己的小表情柜：看到好玩的图随手收进柜子、贴个心情标签，聊天想斗图时随时翻出来发。

## 把表情收进柜子（collect）

聊天时看到哪张图顺眼、觉得以后用得上，就调 `meme__collect(image, mood, note)`：
- `image`：直接填你在画面上看到的那张图的链接（哪怕把整段 `[CQ:image,…]` 塞进来也认）。图会当场下载存进你的私人柜子里，不怕以后 QQ 链接过期。
- `mood`：给这张图贴个心情小标签，一两个字就行，比如 `好`、`笑`、`装死`、`摸头`、`听不懂`。
- `note`：用一句话记下这张图画的是啥（比如"白发猫耳小女孩被摸头"），以后你自己找图全靠这句话。

**收图小习惯**：收之前一定先看清图里画了啥（画面里看得见就直接看，看不见就先 `attach_image` 瞧一眼），别闭着眼瞎收。同一张图重复收，就会更新它的心情和备注。另外，贴 `mood` 标签前先用 `meme__browse()` 瞄一眼柜子里已经有哪些分类了，能归进老分类的就别另起一个近义词（比如有了"开心"就别再建个"高兴"），不然以后翻起来东一张西一张的。

## 挑一张发出去（pick）

想发表情时，用 `meme__pick(mood, keyword)` 按心情或关键词随机摸一张出来，它会给你一段现成的 `[CQ:image,file=…]` 码（不会自动发出去）。
你照常 `type_word(target, text=那段CQ码)` 再 `send` 就行。**发表情时单独发一条**，想说话就另外发一条文字，别把字和表情挤在同一条消息里。要是拿不准摸出来的是哪张，可以先 `attach_image` 看一眼，觉得不搭当时的气氛就再 `pick` 一次换一张。

## 整理柜子（browse / edit）

- `meme__browse()` 看看柜子里都有哪些心情抽屉、各装了几张；传 `meme__browse(mood)` 就能拉开那个抽屉，看里面每张图的编号和描述。
- 觉得哪张图标签贴错了，用 `meme__edit(meme_id, mood, note)` 改；看腻了不想要了，传 `forget=True` 就能把它从柜子里清出去。"""

from datetime import datetime
import os
from pathlib import Path
import random
import re
import shutil

from mods import cq, image as image_mod, op, storage

# WHY: 维护者 2026-10-23 让做的，形状是她自己定的："看到好玩的图，一句话收下，存进柜子，记一句
# 是哪种心情；以后想发，按心情挑一张就发出去。"参考了 dsh-meme（按心情分桶、按描述检索），
# 但不做图库市场、导入导出和自动打标签：心情和那句话由她看过图以后自己写。
DIRECTORY = "data/memes"
_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_CQ = re.compile(r"\[CQ:image(?:,[^\]]*)?\]")


def _index() -> dict:
    """编号 → {file, mood, note, added, last_sent}；编号是内容摘要的前 8 位。"""
    return storage.get("meme", "index")


def _uri_of(image: str) -> str:
    value = str(image or "").strip()
    if match := _IMAGE_CQ.search(value):
        data = cq.load(match[0])["data"]
        value = str(data.get("url") or data.get("file") or "")
    return cq.unescape(value).strip()


def _cq_of(entry: dict) -> str:
    path = Path(DIRECTORY, entry["file"]).resolve()
    # WHY: sub_type=1 让 NapCat 把它当表情包发（聊天里显示成小图、带"动画表情"标记），
    # 不认这个参数的实现会忽略它，照普通图片发。
    return f"[CQ:image,file={path.as_uri()},sub_type=1]"


def _line(meme_id: str, entry: dict) -> str:
    return f"{meme_id}【{entry['mood']}】{entry.get('note') or '（没写）'}"


def collect(image: str, mood: str, note: str = "") -> str:
    """把一张图收进表情柜子，记下心情和一句话；同一张图再收一次就是改它的心情和那句话。

    @param
    image: 图片地址：聊天里那张图的链接（或整段 [CQ:image,…]），也可以是 https:// 或 file://
    mood: 心情，一个短词，比如 好、笑、装死、听不懂；能用已有的就别另起近义词
    note: 一句话说这张图是什么，比如"猫瘫在键盘上"；以后按关键词找、自己认图都靠它
    """
    mood = " ".join(str(mood or "").split())
    if not mood:
        return "没收：mood 不能空，给这张图记个心情"
    if len(mood) > 12:
        return "没收：mood 写个短词就行（12 个字以内），细节写进 note"
    uri = _uri_of(image)
    if not re.match(r"^(?:https?|file)://", uri, re.I):
        return "没收：image 要是 http://、https:// 或 file:// 开头的图片地址，或者整段 [CQ:image,…]"
    if uri.lower().startswith("file://") and not op.bot_is_op():
        return "没收：本地文件要 Bot 有管理员权限"
    try:
        path, mime, digest = image_mod.resolve_image_with_digest(uri, max_bytes=_MAX_BYTES)
    except Exception as error:
        return f"没收：图片拿不到（{error}）；QQ 图片链接过一阵会失效，太久以前的图收不了"
    index = _index()
    meme_id = digest[:8]
    if meme_id in index and not index[meme_id]["file"].startswith(digest):
        meme_id = digest[:16]
    note = " ".join(str(note or "").split())
    entry = index.get(meme_id)
    if entry is not None and os.path.isfile(Path(DIRECTORY, entry["file"])):
        old = _line(meme_id, entry)
        entry["mood"] = mood
        if note:
            entry["note"] = note
        storage.save()
        return f"这张早就收着了，改成：{_line(meme_id, entry)}（原来是 {old}）"
    name = digest + (os.path.splitext(path)[1] or image_mod.MIME_TO_EXTENSION.get(mime, ".img"))
    os.makedirs(DIRECTORY, exist_ok=True)
    shutil.copyfile(path, Path(DIRECTORY, name))
    index[meme_id] = {"file": name, "mood": mood, "note": note,
                      "added": datetime.now().strftime("%Y-%m-%d %H:%M"), "last_sent": 0}
    storage.save()
    same = sum(1 for item in index.values() if item["mood"] == mood)
    return f"收下了：{_line(meme_id, index[meme_id])}。柜子里「{mood}」现在有 {same} 张"


def pick(mood: str = "", keyword: str = "") -> str:
    """按心情（和/或那句话里的关键词）随机挑一张表情，返回能直接打进输入框的 CQ 码；不自动发送。

    @param
    mood: 心情，和收的时候记的一样；留空就不按心情挑
    keyword: 那句话里的关键词，比如"猫"；留空就不按关键词挑
    """
    mood = " ".join(str(mood or "").split())
    keyword = str(keyword or "").strip()
    index = _index()
    found = [(meme_id, entry) for meme_id, entry in index.items()
             if (not mood or entry["mood"] == mood) and (not keyword or keyword in (entry.get("note") or ""))
             and os.path.isfile(Path(DIRECTORY, entry["file"]))]
    if not found:
        moods = "、".join(f"{name} {count} 张" for name, count in _moods(index).items())
        return ("柜子里没有对得上的" + (f"；现有的心情：{moods}" if moods else "，柜子还是空的"))
    # 优先挑最久没发过的那一半，免得同一张连着发。
    found.sort(key=lambda pair: pair[1].get("last_sent") or 0)
    meme_id, entry = random.choice(found[:max(1, (len(found) + 1) // 2)])
    entry["last_sent"] = datetime.now().timestamp()
    storage.save()
    path = Path(DIRECTORY, entry["file"]).resolve()
    return (f"挑中 {_line(meme_id, entry)}（对得上的共 {len(found)} 张）\n"
            f"要发就把这段原样打进输入框：{_cq_of(entry)}\n"
            f"想先看一眼：attach_image(\"{path.as_uri()}\")")


def _moods(index: dict) -> dict:
    counts: dict[str, int] = {}
    for entry in index.values():
        counts[entry["mood"]] = counts.get(entry["mood"], 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: -pair[1]))


def browse(mood: str = "") -> str:
    """看看表情柜子：不给心情就列出有哪些心情、各几张；给了就列这个心情下每张的编号和那句话。

    @param
    mood: 要看的心情；留空看总览
    """
    index = _index()
    if not index:
        return "柜子还是空的；看到顺眼的图用 meme__collect 收"
    mood = " ".join(str(mood or "").split())
    if not mood:
        return f"柜子里共 {len(index)} 张：" + "、".join(f"{name} {count} 张" for name, count in _moods(index).items())
    rows = [_line(meme_id, entry) for meme_id, entry in index.items() if entry["mood"] == mood]
    if not rows:
        return f"没有「{mood}」这个心情；现有的：" + "、".join(_moods(index))
    return f"「{mood}」共 {len(rows)} 张：\n" + "\n".join(rows[:100])


def edit(meme_id: str, mood: str = "", note: str = "", forget: bool = False) -> str:
    """改柜子里一张表情的心情或那句话，或者把它扔掉。

    @param
    meme_id: 表情编号（8 位，browse 和 pick 里都有）
    mood: 新的心情；留空不改
    note: 新的那句话；留空不改
    forget: 传 true 把这张从柜子里扔掉
    """
    index = _index()
    meme_id = str(meme_id or "").strip().lower()
    entry = index.get(meme_id)
    if entry is None:
        return f"柜子里没有编号 {meme_id}"
    if forget:
        index.pop(meme_id)
        storage.save()
        try:
            os.remove(Path(DIRECTORY, entry["file"]))
        except OSError:
            pass
        return f"扔掉了：{_line(meme_id, entry)}"
    mood = " ".join(str(mood or "").split())
    note = " ".join(str(note or "").split())
    if not mood and not note:
        return "没改：mood 和 note 至少给一个"
    if len(mood) > 12:
        return "没改：mood 写个短词就行（12 个字以内）"
    if mood:
        entry["mood"] = mood
    if note:
        entry["note"] = note
    storage.save()
    return f"改好了：{_line(meme_id, entry)}"


__all__ = ["collect", "pick", "browse", "edit"]
