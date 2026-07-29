"""Image resizing helpers retained for live link actions."""

import math
import os

from mods import cq


LOAD_AFTER = ("image",)


def on_load(_ctx) -> None:
    from mods import is_available

    if not is_available("image"):
        raise RuntimeError("image_tools 依赖的 image 不可用")


def _target_size(width: int, height: int, size):
    area = width * height
    if isinstance(size, int):
        scale = math.sqrt(size / area)
    elif isinstance(size, float):
        scale = size
    else:
        raise TypeError("size 必须是整数或浮点数")
    return max(1, int(width * scale)), max(1, int(height * scale))


def deal_img(pic: dict, size, is_fit: bool):
    """Download and resize one CQ image, returning an absolute output path."""
    from PIL import Image, ImageSequence

    source = cq.download_img(pic["data"]["url"])
    with Image.open(source) as image:
        width, height = image.size
        if is_fit and isinstance(size, int) and width * height <= size:
            if not source.lower().endswith(".gif"):
                return None
        target_size = _target_size(width, height, size)
        base, extension = os.path.splitext(source)

        if extension.lower() == ".gif":
            output = f"{base}_resized_{size}.gif"
            frames = []
            durations = []
            disposals = []
            for frame in ImageSequence.Iterator(image):
                durations.append(frame.info.get("duration", image.info.get("duration", 100)))
                disposals.append(getattr(frame, "disposal_method", 2))
                resized = frame.convert("RGBA").resize(target_size, Image.Resampling.LANCZOS)
                frames.append(resized.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
            save_options = {
                "save_all": True,
                "append_images": frames[1:],
                "duration": durations,
                "loop": image.info.get("loop", 0),
                "optimize": False,
                "disposal": disposals,
            }
            if image.info.get("transparency") is not None:
                save_options["transparency"] = image.info["transparency"]
            frames[0].save(output, **save_options)
        else:
            output = f"{base}_resized_{size}.png"
            if image.mode not in {"RGBA", "RGB"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.resize(target_size, Image.Resampling.LANCZOS).save(output, "PNG")
    return os.path.abspath(output)
