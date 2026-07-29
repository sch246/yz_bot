"""Small lunar-calendar helpers for dynamic programs."""

import datetime

from lunardate import LunarDate


def lunar_time():
    """Return today's lunar-calendar date."""
    today = datetime.date.today()
    return LunarDate.fromSolarDate(today.year, today.month, today.day)


def 小六壬(offset=0):
    """Return the historical six-state result used by the Bot."""
    lunar = lunar_time()
    hour = (datetime.datetime.now().hour + 1) // 2
    if hour == 12:
        hour = 1
    return ("大安", "流连", "速喜", "赤口", "小吉", "空亡")[(lunar.month - 1 + lunar.day - 1 + hour) % 6]
