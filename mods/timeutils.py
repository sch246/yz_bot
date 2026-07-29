"""Time calculations used by persistent reactions."""

import datetime
import math
import time


def _sun_theta(timestamp):
    value = time.gmtime(timestamp)
    origin = 79.6764 + 0.2422 * (value.tm_year - 1985) - int((value.tm_year - 1985) / 4)
    return 2 * math.pi * (value.tm_yday - origin) / 365.2422


def _sun_delta(timestamp):
    theta = _sun_theta(timestamp)
    return (
        0.0028
        - 1.9857 * math.sin(theta)
        + 9.9059 * math.sin(2 * theta)
        - 7.0924 * math.cos(theta)
        - 0.6882 * math.cos(2 * theta)
    )


def 真太阳时(timestamp, longitude):
    return time.gmtime(timestamp + _sun_delta(timestamp) * 60 + longitude * 4 * 60)


def time_between(start, end) -> bool:
    now = datetime.datetime.now().time()
    return datetime.time(start, 0) <= now < datetime.time(end, 0)
