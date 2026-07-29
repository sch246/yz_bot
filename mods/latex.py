"""Render trusted LaTeX snippets to a local PNG CQ image."""

import os
from io import BytesIO


def latex2img(
    text,
    size=32,
    color=(0, 0, 0),
    bg_color=(255, 255, 255),
    out="demo.png",
    **options,
):
    # These imports can scan fonts or load image plugins, so they belong to the
    # actual device action rather than module discovery.
    from PIL import Image
    import matplotlib.font_manager as font_manager
    from matplotlib import mathtext

    if os.path.splitext(out)[1].lower() != ".png":
        raise ValueError("仅支持后缀名为.png的文件名")
    unknown = set(options) - {"dpi", "family", "weight"}
    if unknown:
        raise KeyError(f"不支持的关键字参数：{sorted(unknown)[0]}")

    properties = font_manager.FontProperties(
        family=options.get("family"),
        size=size,
        weight=options.get("weight", "normal"),
    )
    buffer = BytesIO()
    mathtext.math_to_image(
        text,
        buffer,
        prop=properties,
        dpi=options.get("dpi", 72),
        color=color,
    )
    formula = Image.open(buffer).convert("RGBA")
    background = Image.new("RGBA", formula.size, tuple(bg_color) + (255,))
    Image.alpha_composite(background, formula).convert("RGB").save(out, "PNG")
    return f"[CQ:image,file=file://{os.path.abspath(out)}]"
