"""On-demand geocoding and weather helpers for chat tools and live links."""

import os
import time
from typing import Optional


def geocode(address: str):
    import requests

    response = requests.get(
        "http://api.map.baidu.com/geocoder",
        params={"address": address, "output": "json"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["result"]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量: {name}")
    return value


def _load_private_key() -> str:
    path = os.getenv("QWEATHER_PRIVATE_KEY_FILE", "ed25519-private.pem")
    try:
        with open(path, encoding="utf-8") as key_file:
            return key_file.read()
    except OSError as error:
        raise RuntimeError(f"无法读取私钥文件: {error}") from error


def _generate_token(key_id: str, project_id: str) -> str:
    import jwt

    now = int(time.time())
    return jwt.encode(
        {"iat": now - 30, "exp": now + 900, "sub": project_id},
        _load_private_key(),
        algorithm="EdDSA",
        headers={"kid": key_id},
    )


def _api_request(endpoint: str, params: dict) -> Optional[dict]:
    import requests

    host = _require_env("QWEATHER_API_HOST")
    token = _generate_token(
        _require_env("QWEATHER_KEY_ID"),
        _require_env("QWEATHER_PROJECT_ID"),
    )
    try:
        response = requests.get(
            f"https://{host}{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    return response.json()


def search_city(location: str, adm: str = "", lang: str = "zh") -> Optional[list]:
    """检索地点，返回候选列表；其中的 id 字段是其余天气函数所需的 location ID。

    @param
    location: 地名或"经度,纬度"，例如 "杭州"、"西湖区"、"116.41,39.92"
    adm: 上级行政区，用来消歧同名地点，例如 location="朝阳" 时传 "北京"；不需要时传空字符串
    lang: 返回语言 enum: ["zh", "en"]
    """
    response = _api_request(
        "/geo/v2/city/lookup",
        {"location": location, "adm": adm, "lang": lang},
    )
    return response.get("location") if response and response.get("code") == "200" else None


def get_realtime_weather(location_id: str, unit: str = "m") -> Optional[dict]:
    """按 location ID 查询该地实时天气，返回含温度、体感、天气现象、风向风力、湿度等字段的 dict。

    @param
    location_id: search_city 返回的 id，不是地名
    unit: 单位制，m 公制、i 英制 enum: ["m", "i"]
    """
    response = _api_request(
        "/v7/weather/now", {"location": location_id, "unit": unit}
    )
    return response.get("now") if response and response.get("code") == "200" else None


def get_daily_forecast(location_id: str, days: int = 3, unit: str = "m") -> Optional[list]:
    """按 location ID 查询逐日预报，返回每天一项的列表，含日期、最高最低温、白天夜间天气。

    @param
    location_id: search_city 返回的 id，不是地名
    days: 预报天数，只支持 3、7、10、15、30；其它值会被静默按 3 处理
    unit: 单位制，m 公制、i 英制 enum: ["m", "i"]
    """
    endpoint_days = {3: "3d", 7: "7d", 10: "10d", 15: "15d", 30: "30d"}
    response = _api_request(
        f"/v7/weather/{endpoint_days.get(days, '3d')}",
        {"location": location_id, "unit": unit},
    )
    return response.get("daily") if response and response.get("code") == "200" else None


def get_hourly_forecast(location_id: str, hours: int = 24, unit: str = "m") -> Optional[list]:
    """按 location ID 查询逐小时预报，返回每小时一项的列表，含时间、温度、天气现象、降水概率。

    @param
    location_id: search_city 返回的 id，不是地名
    hours: 预报小时数，只支持 24、72、168；其它值会被静默按 24 处理
    unit: 单位制，m 公制、i 英制 enum: ["m", "i"]
    """
    endpoint_hours = {24: "24h", 72: "72h", 168: "168h"}
    response = _api_request(
        f"/v7/weather/{endpoint_hours.get(hours, '24h')}",
        {"location": location_id, "unit": unit},
    )
    return response.get("hourly") if response and response.get("code") == "200" else None
