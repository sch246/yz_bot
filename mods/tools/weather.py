"""查询天气：城市检索、实时天气、逐日预报和逐小时预报。

天气查询必须两步走，不能跳过第一步：

1. 用 `weather__search_city("杭州")` 把地名换成和风天气的 location ID。返回一组候选地点，每条含 `id`、`name`、`adm2`（市）、`adm1`（省）、`country`、`lat`、`lon`、`tz`。同名地点很多，先按 `adm1`/`adm2` 判断是不是用户要的那个，再取它的 `id`。
2. 把这个 `id` 传给 `weather__get_realtime_weather`、`weather__get_daily_forecast` 或 `weather__get_hourly_forecast`。这三个函数只接受 location ID，直接传中文地名查不到。

返回值是和风天气的原始结构（dict 或 list[dict]），字段值都是字符串：实时天气有 `temp`、`feelsLike`、`text`、`windDir`、`windScale`、`humidity`、`precip`、`vis` 等；逐日预报每天有 `fxDate`、`tempMax`、`tempMin`、`textDay`、`textNight`、`sunrise`、`sunset`；逐小时预报每小时有 `fxTime`、`temp`、`text`、`pop`（降水概率）。数值单位由 `unit` 决定：`m` 是公制（摄氏度、公里/小时、毫米），`i` 是英制（华氏度、英里/小时、英寸）。

查询失败、缺少密钥、地点不存在时一律返回 `None`（工具结果显示为字符串 "None"），不区分原因。这时如实说查不到，不要根据常识编造天气数据。

预报档位是固定的：逐日只有 3、7、10、15、30 天，逐小时只有 24、72、168 小时。传别的数字不会报错，而是被静默降级成最小档（3 天 / 24 小时），所以只用列出的取值，需要"明天"就取 3 天预报的第二项。

问"今天/现在"之前先用 `common__get_time` 确认当前日期，再和 `fxDate`、`fxTime` 对齐。
"""

from mods import get_available


_weather = get_available("weather")
if _weather is None:
    raise RuntimeError("weather module is unavailable")


def search_city(location: str, adm: str = "", lang: str = "zh") -> list | None:
    """检索地点，返回候选列表；其中的 id 字段是其余天气函数所需的 location ID。

    @param
    location: 地名或"经度,纬度"，例如 "杭州"、"西湖区"、"116.41,39.92"
    adm: 上级行政区，用来消歧同名地点，例如 location="朝阳" 时传 "北京"；不需要时传空字符串
    lang: 返回语言 enum: ["zh", "en"]
    """
    return _weather.search_city(location, adm, lang)


def get_realtime_weather(location_id: str, unit: str = "m") -> dict | None:
    """按 location ID 查询该地实时天气，返回含温度、体感、天气现象、风向风力、湿度等字段的 dict。

    @param
    location_id: search_city 返回的 id，不是地名
    unit: 单位制，m 公制、i 英制 enum: ["m", "i"]
    """
    return _weather.get_realtime_weather(location_id, unit)


def get_daily_forecast(location_id: str, days: int = 3, unit: str = "m") -> list | None:
    """按 location ID 查询逐日预报，返回每天一项的列表，含日期、最高最低温、白天夜间天气。

    @param
    location_id: search_city 返回的 id，不是地名
    days: 预报天数，只支持 3、7、10、15、30；其它值会被静默按 3 处理
    unit: 单位制，m 公制、i 英制 enum: ["m", "i"]
    """
    return _weather.get_daily_forecast(location_id, days, unit)


def get_hourly_forecast(location_id: str, hours: int = 24, unit: str = "m") -> list | None:
    """按 location ID 查询逐小时预报，返回每小时一项的列表，含时间、温度、天气现象、降水概率。

    @param
    location_id: search_city 返回的 id，不是地名
    hours: 预报小时数，只支持 24、72、168；其它值会被静默按 24 处理
    unit: 单位制，m 公制、i 英制 enum: ["m", "i"]
    """
    return _weather.get_hourly_forecast(location_id, hours, unit)


__all__ = [
    "search_city",
    "get_realtime_weather",
    "get_daily_forecast",
    "get_hourly_forecast",
]
