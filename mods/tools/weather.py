"""提供城市检索、实时天气以及逐日和逐小时预报能力。"""

from mods.weather import (
    get_daily_forecast,
    get_hourly_forecast,
    get_realtime_weather,
    search_city,
)


__all__ = [
    "search_city",
    "get_realtime_weather",
    "get_daily_forecast",
    "get_hourly_forecast",
]
