from datetime import datetime, time
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .domain import timestamp


def demo_id(university, namespace, key):
    return uuid5(NAMESPACE_URL, f"dean:{university}:{namespace}:{key}").hex


def demo_time(day, hour, zone="Europe/Moscow"):
    return timestamp(datetime.combine(day, time(hour), ZoneInfo(zone)))
