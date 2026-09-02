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

search_city = _weather.search_city
get_realtime_weather = _weather.get_realtime_weather
get_daily_forecast = _weather.get_daily_forecast
get_hourly_forecast = _weather.get_hourly_forecast


__all__ = [
    "search_city",
    "get_realtime_weather",
    "get_daily_forecast",
    "get_hourly_forecast",
]
