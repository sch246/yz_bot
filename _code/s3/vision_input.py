import base64
import io
import time

from PIL import Image, ImageOps, UnidentifiedImageError


AUTO_IMAGE_SPLIT_PROMPT = (
    "【自动图片切割】同一张原始长截图已按从上到下的顺序切割为连续片段，"
    "相邻片段存在重叠。请将这些片段作为一张完整图片理解，利用重叠衔接上下文，"
    "描述或转录时不要重复重叠内容。"
)

LONG_IMAGE_RATIO = 4
IMAGE_SLICE_HEIGHT_RATIO = 3
IMAGE_SLICE_OVERLAP_DIVISOR = 4


def split_long_image_data_uri(data_uri: str) -> tuple[list[str], bool]:
    """在发送给视觉模型前把长截图切成内存中的 PNG Data URI。"""
    if not isinstance(data_uri, str) or not data_uri.startswith('data:image/'):
        return [data_uri], False

    header, separator, payload = data_uri.partition(',')
    if not separator or ';base64' not in header.lower():
        return [data_uri], False

    try:
        image_bytes = base64.b64decode(payload, validate=True)
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
    except (ValueError, UnidentifiedImageError, OSError, SyntaxError):
        return [data_uri], False

    width, height = image.size
    if width <= 0 or height <= LONG_IMAGE_RATIO * width:
        return [data_uri], False

    started_at = time.perf_counter()
    base_slice_height = IMAGE_SLICE_HEIGHT_RATIO * width
    overlap = max(1, width // IMAGE_SLICE_OVERLAP_DIVISOR)
    slices = []

    try:
        top = 0
        while top < height:
            base_bottom = min(top + base_slice_height, height)
            remaining = height - base_bottom
            if remaining <= width:
                bottom = height
            else:
                bottom = min(base_bottom + overlap, height)
            crop = image.crop((0, top, width, bottom))
            if crop.mode not in {'1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I', 'I;16'}:
                crop = crop.convert('RGBA' if 'A' in crop.getbands() else 'RGB')

            output = io.BytesIO()
            crop.save(output, format='PNG')
            encoded = base64.b64encode(output.getvalue()).decode('ascii')
            slices.append(f'data:image/png;base64,{encoded}')

            if bottom >= height:
                break
            top += base_slice_height
    except (OSError, ValueError) as error:
        print(f"⚠️ 长图自动切割失败，改用原图: {error}")
        return [data_uri], False

    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    print(
        f"✂️ 自动切割长图: {width}x{height} -> {len(slices)}片, "
        f"基础片高={base_slice_height}, 重叠={overlap}, 耗时={elapsed_ms}ms"
    )
    return slices, True
