"""把表情柜整堆拼成一张联络表（contact sheet），一眼挑图。

`data/memes` 里的图文件名全是 sha256，想发图只能靠猜或一张张写出来看，太贵。
`memesheet__sheet()` 把整个柜子按页拼成大图（带页内序号、已记的心情和备注），
保存成本地 jpg 并返回 `file://` 地址，拿它 `attach_image` 看一眼就能挑。

用法：
- `memesheet__sheet()` 第一页，默认一页 24 张（6 列 4 行）、单格 200 像素。
- `memesheet__sheet(page=2)` 翻下一页；`per_page`、`cols`、`thumb` 可调，但整柜 80 张
  用默认值分 4 页刚好，格子里那个序号是「这一页里的第几张」，跟别页不连续。
- 返回的路径可以直接 `attach_image(uri)`；图里每格底部写着柜子里已记的 `心情` 和
  备注前 12 个字，还没记过标签的只显示文件名前 8 位。

这套只读不写：不改柜子的索引、不删图，生成物落在 `data/memes/_sheet/`。
"""

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DIRECTORY = Path("data/memes")
OUTPUT = DIRECTORY / "_sheet"
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _files() -> list[Path]:
    if not DIRECTORY.is_dir():
        return []
    return sorted(
        p for p in DIRECTORY.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXT
    )


def _labels() -> dict[str, dict]:
    """文件名 → 柜子索引里的 {id, mood, note}；读不到索引就当全没标签。"""
    try:
        from mods import storage

        index = storage.get("meme", "index") or {}
    except Exception:
        return {}
    out = {}
    for meme_id, entry in index.items():
        name = str((entry or {}).get("file") or "")
        if name:
            out[name] = {"id": meme_id, "mood": entry.get("mood") or "", "note": entry.get("note") or ""}
    return out


def _font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _thumb(path: Path, box: int) -> Image.Image:
    img = Image.open(path)
    if getattr(img, "is_animated", False):
        img.seek(0)
    img = img.convert("RGBA")
    img.thumbnail((box, box), Image.LANCZOS)
    canvas = Image.new("RGBA", (box, box), (255, 255, 255, 255))
    canvas.alpha_composite(img, ((box - img.width) // 2, (box - img.height) // 2))
    return canvas.convert("RGB")


def sheet(page: int = 1, per_page: int = 24, cols: int = 6, thumb: int = 200) -> str:
    """把表情柜拼成一张联络表存成 jpg，返回 file:// 地址，供 attach_image 看一眼挑图。

    @param
    page: 第几页，从 1 开始；整柜 80 张用默认值分 4 页
    per_page: 一页放几张，默认 24
    cols: 一行几列，默认 6；行数由 per_page 和 cols 算出来
    thumb: 单格图片边长像素，默认 200；看不清就调到 280 以上
    """
    files = _files()
    if not files:
        return f"表情柜是空的：{DIRECTORY} 里没有图片"
    per_page = max(1, int(per_page))
    cols = max(1, int(cols))
    total_pages = max(1, (len(files) + per_page - 1) // per_page)
    page = min(max(1, int(page)), total_pages)
    chunk = files[(page - 1) * per_page : page * per_page]
    labels = _labels()

    pad, label_h = 8, 46
    cell_w, cell_h = thumb + pad * 2, thumb + pad * 2 + label_h
    rows = (len(chunk) + cols - 1) // cols
    width, height = cols * cell_w, rows * cell_h + 34
    board = Image.new("RGB", (width, height), (248, 248, 246))
    draw = ImageDraw.Draw(board)
    f_head, f_label, f_note = _font(18), _font(15), _font(13)

    draw.text((pad, 8), f"memes · 第 {page}/{total_pages} 页 · 共 {len(files)} 张", fill=(40, 40, 40), font=f_head)
    for i, path in enumerate(chunk):
        r, c = divmod(i, cols)
        x, y = c * cell_w + pad, r * cell_h + 34 + pad
        try:
            pic = _thumb(path, thumb)
        except Exception:
            pic = Image.new("RGB", (thumb, thumb), (230, 230, 230))
        board.paste(pic, (x, y))
        draw.rectangle([x - 1, y - 1, x + thumb, y + thumb], outline=(200, 200, 200))
        num = i + 1
        draw.rectangle([x, y, x + 34, y + 26], fill=(40, 40, 40))
        draw.text((x + 7, y + 3), str(num), fill=(255, 255, 255), font=f_label)
        info = labels.get(path.name)
        if info:
            tag = info["mood"] or "—"
            note = (info["note"] or "")[:12]
            draw.text((x, y + thumb + 6), f"{info['id'][:6]}·{tag}", fill=(30, 90, 30), font=f_label)
            if note:
                draw.text((x, y + thumb + 25), note, fill=(90, 90, 90), font=f_note)
        else:
            draw.text((x, y + thumb + 6), path.stem[:8], fill=(120, 120, 120), font=f_label)
            draw.text((x, y + thumb + 25), path.suffix.lower(), fill=(150, 150, 150), font=f_note)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    out = OUTPUT / f"sheet_p{page}_{per_page}.jpg"
    board.save(out, "JPEG", quality=82)
    return out.resolve().as_uri()


__all__ = ["sheet"]
