"""提供城市检索、实时天气以及逐日和逐小时预报能力。"""

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
