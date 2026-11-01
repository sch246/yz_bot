"""Prepare still images and GIF frames for vision input."""

import base64
import io
import math
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from mods import log


_stream = log.stream("image")


# WHY: 长图被切片时这段会追加到描述 prompt 末尾，结果同样进自动描述缓存。改这段文字或
# 下面三个切割参数，都要同时提升 image.AUTO_IMAGE_DESCRIPTION_VERSION。
AUTO_IMAGE_SPLIT_PROMPT = (
    "【自动图片切割】同一张原始长截图已按从上到下的顺序切割为连续片段，"
    "相邻片段存在重叠。请将这些片段作为一张完整图片理解，利用重叠衔接上下文，"
    "描述或转录时不要重复重叠内容。"
)
# WHY: 三个比例都是试出来的经验值，没有推导，目前跑着没问题。改动它们会改变模型实际
# 看到的内容，所以要连带提升 image.AUTO_IMAGE_DESCRIPTION_VERSION（见上）。
LONG_IMAGE_RATIO = 4
IMAGE_SLICE_HEIGHT_RATIO = 3
IMAGE_SLICE_OVERLAP_DIVISOR = 4


def _png_data_uri(image: Image.Image) -> str:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _gif_grid(source: Image.Image) -> tuple[list[str], str]:
    frame_count = source.n_frames
    pages: list[str] = []
    elapsed_ms = 0
    font = ImageFont.load_default(size=18)
    gap, label_height, max_side = 8, 30, 2560
    scale = min(1, 384 / max(source.size))
    thumb_width = max(1, round(source.width * scale))
    thumb_height = max(1, round(source.height * scale))
    cell_width = max(240, thumb_width)
    cell_height = thumb_height + label_height
    for start in range(0, frame_count, 25):
        count = min(25, frame_count - start)
        columns = min(
            range(1, count + 1),
            key=lambda cols: abs(math.log(
                (cols * (cell_width + gap) + gap)
                / (math.ceil(count / cols) * (cell_height + gap) + gap)
            )),
        )
        rows = math.ceil(count / columns)
        width = min(cell_width, (max_side - gap * (columns + 1)) // columns)
        height = min(cell_height, (max_side - gap * (rows + 1)) // rows)
        canvas = Image.new("RGB", (columns * (width + gap) + gap, rows * (height + gap) + gap), "white")
        draw = ImageDraw.Draw(canvas)
        for offset in range(count):
            index = start + offset
            # WHY: 必须顺序 seek，让 Pillow 先应用透明、增量帧与 disposal，再缩小当前完整帧。
            # 不能把所有原尺寸帧先 copy 到列表；长 GIF 的内存会随帧数增长。
            source.seek(index)
            frame = source.convert("RGBA")
            frame.thumbnail((width, height - label_height), Image.Resampling.LANCZOS)
            left = gap + (offset % columns) * (width + gap)
            top = gap + (offset // columns) * (height + gap)
            canvas.paste(frame, (left + (width - frame.width) // 2, top), frame)
            draw.text((left + 4, top + height - label_height + 5), f"#{index + 1}  t={elapsed_ms / 1000:.3f}s", font=font, fill="black")
            elapsed_ms += source.info.get("duration", 0)
        pages.append(_png_data_uri(canvas))
    return pages, (
        f"【GIF 全部帧组合图】共 {frame_count} 帧，分成 {len(pages)} 张网格图，未抽样、未遗漏帧。"
        "图片依次排列；每张按从左到右、再从上到下的顺序阅读，跨图继续。"
        "每格 # 是从 1 开始的全局帧号，t 是按 GIF 帧延时累计的帧起始时间（秒，未定义延时按 0 计）。"
        "这些格子是同一 GIF 在不同时刻的画面，请结合前后帧理解动作，不要当作一张长截图。"
    )


def prepare_image_data_uri(data_uri: str, *, gif_mode: str = "first") -> tuple[list[str], str]:
    """Prepare GIF first/native/grid input, or apply the existing long-image slicing."""
    if gif_mode not in {"first", "native", "grid"}:
        raise ValueError("gif_mode 必须是 first、native 或 grid")
    if isinstance(data_uri, str) and data_uri.startswith("data:image/"):
        header, separator, payload = data_uri.partition(",")
        if separator and ";base64" in header.lower():
            try:
                content = base64.b64decode(payload, validate=True)
                source = Image.open(io.BytesIO(content))
            except (ValueError, UnidentifiedImageError, OSError, SyntaxError):
                source = None
            if source is not None:
                with source:
                    if source.format == "GIF":
                        if gif_mode == "native":
                            return ["data:image/gif;base64," + payload], "【GIF 原图】这是 GIF 动图，保留原始 GIF 数据。"
                        try:
                            if gif_mode == "grid":
                                return _gif_grid(source)
                            return [_png_data_uri(source.convert("RGBA"))], (
                                "【GIF 首帧】这是 GIF 动图，目前仅展示第一帧，不能据此判断后续动作。"
                                "可以用 attach_image 打开此图的全部帧组合图，查看完整动画过程。"
                            )
                        except (EOFError, OSError, ValueError) as error:
                            raise ValueError("GIF 帧解码失败，无法准备图片") from error
    slices, split = split_long_image_data_uri(data_uri)
    return slices, AUTO_IMAGE_SPLIT_PROMPT if split else ""


def split_long_image_data_uri(data_uri: str) -> tuple[list[str], bool]:
    if not isinstance(data_uri, str) or not data_uri.startswith("data:image/"):
        return [data_uri], False
    header, separator, payload = data_uri.partition(",")
    if not separator or ";base64" not in header.lower():
        return [data_uri], False
    try:
        content = base64.b64decode(payload, validate=True)
        with Image.open(io.BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
    except (ValueError, UnidentifiedImageError, OSError, SyntaxError):
        return [data_uri], False
    width, height = image.size
    if width <= 0 or height <= LONG_IMAGE_RATIO * width:
        return [data_uri], False
    base_height = IMAGE_SLICE_HEIGHT_RATIO * width
    overlap = max(1, width // IMAGE_SLICE_OVERLAP_DIVISOR)
    slices: list[str] = []
    started_at = time.perf_counter()
    try:
        top = 0
        while top < height:
            base_bottom = min(top + base_height, height)
            bottom = height if height - base_bottom <= width else min(base_bottom + overlap, height)
            crop = image.crop((0, top, width, bottom))
            if crop.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                crop = crop.convert("RGBA" if "A" in crop.getbands() else "RGB")
            output = io.BytesIO()
            crop.save(output, format="PNG")
            slices.append("data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"))
            if bottom >= height:
                break
            top += base_height
    except (OSError, ValueError) as error:
        _stream.info(f"⚠️ 长图自动切割失败，改用原图：{error}")
        return [data_uri], False
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    _stream.info(
        f"✂️ 自动切割长图：{width}x{height} -> {len(slices)} 片，"
        f"基础片高={base_height}，重叠={overlap}，耗时={elapsed_ms}ms"
    )
    return slices, True
