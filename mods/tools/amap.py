"""查高德地图：搜周边好店、看评分与人均消费、查路线和经纬度。

## 什么时候翻高德

- 当你想知道"某地附近有什么好吃的、评分高不高、人均多少钱、几点开门"时，这里的工具最好使。它返回的 **`biz_ext` 里自带 `rating`（评分）、`cost`（人均）、`open_time`（营业时间）**——这些都是普通网页版地图列表里直接看不到的硬核信息（百度地图那个工具就没有评分）。
- **搜周边两步走**：如果想搜某个地标周边（`around`），先用 `geocode` 把地名（比如"广州岗顶地铁站"）算成经纬度坐标 `113.3406,23.1375`，再拿坐标去搜周边；或者直接用 `search` 按城市+关键词搜。

## 钥匙（Key）存在哪

高德的 Web 服务 Key 是属于你自己的公共配置，所有窗口共享。如果需要配置新 Key，去高德控制台（`https://console.amap.com/dev/key/app`）建个应用、添加「Web 服务」平台的 Key（32 位字符串），连同安全密钥（如果有的话）一块交给 `login` 就能存好。

## 平台规矩与限速

按高德开放平台的约定，查出来的数据只在聊天里给大家作参考，不存进数据库、也不拿去干别的。为了不触发高德的并发限流，工具在每次请求之间自动留了 1 秒的呼吸间隔。"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request

from mods import identity

KEY_STORAGE = "amap_key"
SECRET_STORAGE = "amap_secret"
BASE = "https://restapi.amap.com/v3"
MIN_INTERVAL = 1.0
TIMEOUT = 15.0

NEED_KEY = (
    "还没有高德 Key。请在 https://console.amap.com/dev/key/app 创建应用 → 添加 Key "
    "→ 服务平台选「Web 服务」，把拿到的 32 位 Key 交给 amap__login(key=...)。"
)

_last_call = 0.0


def _bucket() -> dict:
    return identity.getstorage(identity.bot_id())


def _creds() -> tuple[str, str]:
    bucket = _bucket()
    return str(bucket.get(KEY_STORAGE) or ""), str(bucket.get(SECRET_STORAGE) or "")


def _throttle() -> None:
    global _last_call
    gap = MIN_INTERVAL - (time.monotonic() - _last_call)
    if gap > 0:
        time.sleep(gap)
    _last_call = time.monotonic()


def _sign(params: dict, secret: str) -> str:
    items = sorted((k, str(v)) for k, v in params.items() if k != "sig" and str(v) != "")
    raw = "&".join(f"{k}={v}" for k, v in items) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _call(path: str, params: dict, key: str = "", secret: str = "") -> dict:
    """发一次 Web 服务请求，成功返回原始 JSON，失败返回 `{"_error": ...}`。"""
    if not key:
        key, secret = _creds() if not key else (key, secret)
    if not key:
        return {"_error": NEED_KEY}
    payload = {k: v for k, v in params.items() if str(v) != ""}
    payload["key"] = key
    payload["output"] = "json"
    if secret:
        payload["sig"] = _sign(payload, secret)
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(payload, doseq=True)
    _throttle()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as error:
        return {"_error": f"请求失败：{type(error).__name__}: {error}"}
    if str(data.get("status")) != "1":
        return {"_error": f"高德返回错误：{data.get('info')}（infocode {data.get('infocode')}）"}
    return data


def _field(value) -> str:
    """高德把空字段写成 `[]`，统一压成空串。"""
    if value is None or value == [] or value == "":
        return ""
    if isinstance(value, list):
        return ";".join(str(x) for x in value if x not in (None, "", []))
    return str(value)


def _poi_lines(pois: list, start: int = 1) -> list[str]:
    lines = []
    for index, poi in enumerate(pois, start):
        if not isinstance(poi, dict):
            continue
        ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
        bits = []
        rating = _field(ext.get("rating"))
        cost = _field(ext.get("cost"))
        if rating:
            bits.append(f"★{rating}")
        if cost:
            bits.append(f"人均¥{cost}")
        open_time = _field(ext.get("open_time")) or _field(ext.get("opentime2"))
        if open_time:
            bits.append(open_time)
        distance = _field(poi.get("distance"))
        if distance:
            bits.append(f"{distance}米")
        head = f"{index}. {_field(poi.get('name'))}"
        if bits:
            head += " | " + " | ".join(bits)
        lines.append(head)
        details = []
        kind = _field(poi.get("keytag")) or _field(poi.get("type"))
        if kind:
            details.append(f"类型：{kind}")
        address = _field(poi.get("address"))
        if address:
            details.append(f"地址：{address}")
        area = _field(poi.get("business_area"))
        if area:
            details.append(f"商圈：{area}")
        tel = _field(poi.get("tel"))
        if tel:
            details.append(f"电话：{tel}")
        tag = _field(poi.get("tag"))
        if tag:
            # WHY: 有的店 tag 能铺满一屏（太古汇那家 30 多条），摘要里只留前 6 条。
            items = [piece for piece in tag.replace(",", ";").split(";") if piece.strip()]
            short = ",".join(items[:6]) + ("…" if len(items) > 6 else "")
            details.append(f"招牌：{short}")
        location = _field(poi.get("location"))
        if location:
            details.append(f"坐标：{location}")
        ident = _field(poi.get("id"))
        if ident:
            details.append(f"id：{ident}")
        if details:
            lines.append("   " + " | ".join(details))
    return lines


def login(key: str, secret: str = "") -> str:
    """保存 Bot 自己的高德 Key（并先真查一次验证），供之后所有查询使用。

    @param
    key: 32 位高德 Key，服务端类型（控制台创建时服务平台选「Web 服务」）
    secret: 创建 Key 时若同时给了安全密钥就填这里，没有就留空
    """
    if not key.strip():
        return NEED_KEY
    probe = _call("place/text", {"keywords": "广州塔", "city": "广州", "offset": 1},
                  key=key.strip(), secret=secret.strip())
    if probe.get("_error"):
        return f"Key 验证失败：{probe['_error']}"
    bucket = _bucket()
    bucket[KEY_STORAGE] = key.strip()
    if secret.strip():
        bucket[SECRET_STORAGE] = secret.strip()
    else:
        bucket.pop(SECRET_STORAGE, None)
    found = len(probe.get("pois") or [])
    return f"成功：Key 有效（验证查询返回 {found} 条）。已保存为 Bot 的 Key。"


def search(keyword: str, city: str = "", limit: int = 10) -> str:
    """按关键字搜地点（城市内），返回带评分、人均、营业时间的清单。

    @param
    keyword: 搜索词，越像店名或品类越准，例如 糖水、猪杂汤粉、天河城
    city: 城市名或 adcode，例如 广州 或 440106（广州天河）；留空表示全国范围，容易不准
    limit: 最多返回多少条，1 到 20
    """
    size = max(1, min(int(limit), 20))
    data = _call("place/text", {
        "keywords": keyword, "city": city, "citylimit": "true" if city else "",
        "offset": size, "page": 1, "extensions": "all",
    })
    if data.get("_error"):
        return data["_error"]
    pois = data.get("pois") or []
    if not pois:
        return f"没有搜到「{keyword}」。换个更具体的词，或者用 around 按坐标搜周边。"
    lines = [f"「{keyword}」共 {data.get('count')} 条，列出前 {len(pois)} 条（★是评分，来自高德）："]
    lines.extend(_poi_lines(pois))
    return "\n".join(lines)


def around(keyword: str, location: str, radius: int = 1000, limit: int = 10) -> str:
    """按坐标搜周边，返回带距离、评分、人均、营业时间的清单。

    @param
    keyword: 搜索词，例如 糖水、砂锅粥、便利店；留空则按 types 默认查餐饮与生活服务
    location: 中心点坐标，格式 "经度,纬度"（高德是 GCJ-02），例如 113.3406,23.1375
    radius: 半径，单位米，1 到 50000
    limit: 最多返回多少条，1 到 20
    """
    size = max(1, min(int(limit), 20))
    span = max(1, min(int(radius), 50000))
    data = _call("place/around", {
        "keywords": keyword, "location": location, "radius": span,
        "offset": size, "page": 1, "extensions": "all", "sortrule": "weight",
    })
    if data.get("_error"):
        return data["_error"]
    pois = data.get("pois") or []
    if not pois:
        return f"这个点周边 {span} 米内没有搜到「{keyword}」，把 radius 调大再试。"
    lines = [f"坐标 {location} 周边 {span} 米内「{keyword}」共 {data.get('count')} 条，列出前 {len(pois)} 条："]
    lines.extend(_poi_lines(pois))
    return "\n".join(lines)


def detail(poi_id: str) -> str:
    """按 id 查一个地点的详情（搜索或周边结果里会给 id）。

    @param
    poi_id: 高德 POI 的 id，例如 B0K6LSBY95
    """
    data = _call("place/detail", {"id": poi_id, "extensions": "all"})
    if data.get("_error"):
        return data["_error"]
    pois = data.get("pois") or []
    if not pois:
        return f"没有查到这个 id：{poi_id}"
    return "\n".join(_poi_lines([pois[0]]))


def geocode(address: str, city: str = "") -> str:
    """把地址或地标名换成坐标（搜周边前先用它拿 location）。

    @param
    address: 结构化地址或地标名，例如 广州岗顶地铁站、天河路383号
    city: 可选的城市限定，如 广州
    """
    data = _call("geocode/geo", {"address": address, "city": city})
    if data.get("_error"):
        return data["_error"]
    geocodes = data.get("geocodes") or []
    if not geocodes:
        return f"定位不到「{address}」，把地址写得更完整一点再试。"
    lines = []
    for index, item in enumerate(geocodes[:5], 1):
        parts = [f"{index}. {_field(item.get('formatted_address'))}"]
        parts.append(f"坐标：{_field(item.get('location'))}")
        level = _field(item.get("level"))
        if level:
            parts.append(f"精度：{level}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def route(origin: str, destination: str, mode: str = "driving", city: str = "") -> str:
    """算两地之间的路线（距离和耗时），返回可读摘要。

    @param
    origin: 起点坐标，"经度,纬度"
    destination: 终点坐标，"经度,纬度"
    mode: driving 驾车、walking 步行、bicycling 骑行、transit 公交（公交需要给 city）
    city: 公交模式下必填的城市名，如 广州；其它模式留空
    """
    mode = (mode or "driving").strip().lower()
    if mode == "transit":
        path = "direction/transit/integrated"
        params = {"origin": origin, "destination": destination, "city": city, "extensions": "all"}
    elif mode in ("driving", "walking", "bicycling"):
        path = f"direction/{mode}"
        params = {"origin": origin, "destination": destination, "extensions": "all"}
    else:
        return "mode 只支持 driving / walking / bicycling / transit。"
    data = _call(path, params)
    if data.get("_error"):
        return data["_error"]
    route_obj = data.get("route")
    if not isinstance(route_obj, dict):
        return "没有算出路线，检查两个坐标格式是否是 经度,纬度。"
    paths = route_obj.get("paths") or route_obj.get("transits") or []
    if not paths:
        return "没有算出路线。"
    first = paths[0]
    lines = []
    distance = _field(first.get("distance"))
    duration = _field(first.get("duration"))
    if distance:
        lines.append(f"距离：{int(float(distance)) / 1000:.1f} 公里" if distance.replace(".", "", 1).isdigit() else f"距离：{distance}")
    if duration:
        minutes = int(float(duration)) // 60 if str(duration).replace(".", "", 1).isdigit() else duration
        lines.append(f"耗时：约 {minutes} 分钟")
    cost = _field(first.get("tolls"))
    if cost:
        lines.append(f"过路费：{cost} 元")
    taxi = _field(first.get("taxi_cost"))
    if taxi:
        lines.append(f"打车约：{taxi} 元")
    steps = first.get("steps") or []
    if steps:
        lines.append("路线：")
        for step in steps[:12]:
            instruction = _field(step.get("instruction"))
            if instruction:
                lines.append(f"- {instruction}")
    return "\n".join(lines) if lines else "拿到了路线数据但没解析出摘要。"


def status() -> str:
    """看看有没有保存高德 Key，以及有没有配安全密钥（只报存在与否，不显示内容）。"""
    key, secret = _creds()
    if not key:
        return NEED_KEY
    tail = key[-4:] if len(key) >= 4 else ""
    return f"已保存高德 Key（尾号 {tail}），安全密钥：{'已配置' if secret else '未配置'}。"


def forget() -> str:
    """删掉 Bot 保存的高德 Key。"""
    bucket = _bucket()
    had = bool(bucket.pop(KEY_STORAGE, None))
    bucket.pop(SECRET_STORAGE, None)
    return "已删除保存的高德 Key。" if had else "本来就没有保存过高德 Key。"


__all__ = ["around", "detail", "forget", "geocode", "login", "route", "search", "status"]
