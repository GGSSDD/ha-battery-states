"""Battery States: detecting devices that are "not responding".

A battery's device is "not responding" only when the evidence shows the problem
is in that device (its battery, the device or its own connection):

1. Battery States knows what is normal for it: the user's setting, its own
   steady history, or the steady history of other devices of the same model.
2. Its silence is far beyond that (4x the normal longest gap, never less than
   the minimum setting; a user setting is used as it is).
3. Its network demonstrably works: another monitored device on the same
   integration and hub is healthy. Time the network, Home Assistant or the
   device's Last seen sensor was down doesn't count as silence.

Devices without a Last seen sensor are judged by their integration's own
verdict (unavailable) for at least the minimum time, with the same network
check. This module holds the pure logic; monitor.py feeds it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
import statistics
from typing import Any

from homeassistant.util import dt as dt_util

WINDOW = timedelta(days=14)  # rhythm history kept per device
INCIDENT_WINDOW = timedelta(days=7)  # repeated incidents looked at for a new rhythm
LEARN_MIN_SPAN = timedelta(days=3)
LEARN_MIN_REPORTS = 10
LEARN_MIN_DAY_SHARE = 0.7  # reported on at least this share of the days observed
RISE_DAYS = 3  # a new, slower rhythm must hold on this many days in a row
RISE_INCIDENTS = 3
TOLERANCE = 0.25  # "the same rhythm": within 25 % of the middle value
FACTOR = 4  # not responding after this many times the normal longest gap
TWINS_MIN = 2  # other devices of the same model needed to judge by the model
UNKNOWN_CADENCE = timedelta(hours=1)  # on schedule, for a device whose rhythm isn't known
MIN_CADENCE = timedelta(minutes=15)
REALERT_AFTER = timedelta(hours=24)  # a drop-out after less working time than this isn't re-alerted


def parse(value: Any) -> datetime | None:
    """A stored ISO time as an aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return dt_util.as_utc(parsed)


def iso(value: datetime) -> str:
    return dt_util.as_utc(value).isoformat()


# ---------------------------------------------------------------- intervals


def merge(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Sorted, non-overlapping intervals."""
    out: list[tuple[datetime, datetime]] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def covered(intervals: Iterable[tuple[datetime, datetime]], start: datetime, end: datetime) -> timedelta:
    """How much of [start, end] the (possibly overlapping) intervals cover."""
    total = timedelta()
    for s, e in merge(intervals):
        lo, hi = max(s, start), min(e, end)
        if hi > lo:
            total += hi - lo
    return total


def load_intervals(raw: Any) -> list[tuple[datetime, datetime]]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, list) and len(item) == 2:
            s, e = parse(item[0]), parse(item[1])
            if s and e and e > s:
                out.append((s, e))
    return merge(out)


def dump_intervals(intervals: Iterable[tuple[datetime, datetime]], now: datetime) -> list[list[str]]:
    """Kept for the rhythm window only."""
    oldest = now - WINDOW
    return [[iso(s), iso(e)] for s, e in merge(intervals) if e > oldest]


# ------------------------------------------------------------- rhythm stats


def new_stats() -> dict[str, Any]:
    return {"days": {}, "first": None, "last": None, "base": None, "incidents": []}


def clean_stats(raw: Any) -> dict[str, Any]:
    """Stored stats, tolerating missing or broken values."""
    stats = new_stats()
    if not isinstance(raw, dict):
        return stats
    days = raw.get("days")
    if isinstance(days, dict):
        for key, value in days.items():
            if (
                isinstance(key, str)
                and isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], int)
                and (value[1] is None or isinstance(value[1], (int, float)))
            ):
                stats["days"][key] = [value[0], value[1]]
    for key in ("first", "last"):
        if parse(raw.get(key)):
            stats[key] = raw[key]
    base = raw.get("base")
    if isinstance(base, (int, float)) and not isinstance(base, bool) and base > 0:
        stats["base"] = float(base)
    incidents = raw.get("incidents")
    if isinstance(incidents, list):
        stats["incidents"] = [
            [i[0], float(i[1])]
            for i in incidents
            if isinstance(i, list) and len(i) == 2 and parse(i[0]) and isinstance(i[1], (int, float))
        ]
    if raw.get("seeded") is True:
        stats["seeded"] = True
    return stats


def _day(value: datetime) -> str:
    return dt_util.as_utc(value).date().isoformat()


def prune(stats: dict[str, Any], now: datetime) -> None:
    oldest = (now - WINDOW).date().isoformat()
    stats["days"] = {k: v for k, v in stats["days"].items() if k >= oldest}
    cutoff = now - INCIDENT_WINDOW
    stats["incidents"] = [i for i in stats["incidents"] if (parse(i[0]) or now) > cutoff]
    first = parse(stats["first"])
    if first and first < now - WINDOW:
        # The observed period starts with the oldest day still kept.
        stats["first"] = iso(now - WINDOW) if stats["days"] else None


def record_report(
    stats: dict[str, Any], at: datetime, excluded: timedelta, incident: bool, now: datetime
) -> timedelta | None:
    """A report from the device at `at`. Returns the gap since the previous one
    (minus time that doesn't count), or None for the first or an old report."""
    last = parse(stats["last"])
    if last is not None and at <= last:
        return None
    gap = None
    if last is not None:
        gap = max(timedelta(), at - last - excluded)
    if incident and gap is not None:
        stats["incidents"].append([iso(at), gap.total_seconds()])
    else:
        day = stats["days"].setdefault(_day(at), [0, None])
        day[0] += 1
        if gap is not None:
            day[1] = max(day[1] or 0.0, gap.total_seconds())
    stats["last"] = iso(at)
    if stats["first"] is None:
        stats["first"] = iso(at)
    prune(stats, now)
    return gap


def _nth_largest(values: list[float], n: int) -> float | None:
    ordered = sorted(values, reverse=True)
    return ordered[n - 1] if len(ordered) >= n else None


def _same_rhythm(values: list[float]) -> bool:
    if not values:
        return False
    middle = statistics.median(values)
    return middle > 0 and all(abs(v - middle) <= TOLERANCE * middle for v in values)


def steady(stats: dict[str, Any], now: datetime) -> bool:
    """Whether the device shows a regular rhythm over the window."""
    first = parse(stats["first"])
    if first is None:
        return False
    span = now - first
    if span < LEARN_MIN_SPAN:
        return False
    reports = sum(v[0] for v in stats["days"].values())
    if reports < LEARN_MIN_REPORTS:
        return False
    days_in_span = max(1, (now.date() - first.date()).days + 1)
    days_with_reports = sum(1 for v in stats["days"].values() if v[0] > 0)
    return days_with_reports >= LEARN_MIN_DAY_SHARE * days_in_span


def update_base(stats: dict[str, Any], now: datetime) -> float | None:
    """Learn or adjust the device's normal longest gap (seconds).

    - First: once steady, the longest gap seen on at least 3 different days.
    - Rise: only to a new rhythm that holds on 3 recent days in a row (within
      25 %), or 3 recent incidents of the same length (a device set up to
      report less often); erratic silences never raise it.
    - Fall: when a full week shows only shorter gaps.
    """
    prune(stats, now)
    maxima = sorted((d, v[1]) for d, v in stats["days"].items() if v[1])
    values = [m for _, m in maxima]
    base = stats.get("base")
    if base is None:
        if steady(stats, now):
            third = _nth_largest(values, 3)
            first = parse(stats["first"])
            if third and first and third <= (now - first).total_seconds() / 3:
                stats["base"] = third
        return stats.get("base")
    # Rise to a steady new rhythm.
    recent = maxima[-RISE_DAYS:]
    if len(recent) == RISE_DAYS:
        newest_days = [datetime.fromisoformat(d).date() for d, _ in recent]
        consecutive = (newest_days[-1] - newest_days[0]).days == RISE_DAYS - 1
        fresh = (now.date() - newest_days[-1]).days <= 1
        rhythm = [m for _, m in recent]
        if consecutive and fresh and _same_rhythm(rhythm) and statistics.median(rhythm) > base:
            base = max(rhythm)
    incidents = [i[1] for i in stats["incidents"]]
    if len(incidents) >= RISE_INCIDENTS and _same_rhythm(incidents[-RISE_INCIDENTS:]):
        base = max(base, max(incidents[-RISE_INCIDENTS:]))
    # Fall when the last full week shows only shorter gaps (the device got chattier).
    week = [m for d, m in maxima if datetime.fromisoformat(d).date() > (now - timedelta(days=7)).date()]
    if len(week) >= 7 and steady(stats, now) and max(week) < base:
        base = _nth_largest(week, 3) or base
    stats["base"] = base
    return base


# ------------------------------------------------------------------ verdicts


def threshold(
    normal: timedelta | None, source: str | None, minimum: timedelta, setting: timedelta | None
) -> timedelta | None:
    """How long a silence may last before the device is not responding."""
    if setting is not None:
        return setting
    if normal is None or source is None:
        return None
    return max(minimum, FACTOR * normal)


def realert(recovered_at: datetime | None, now: datetime) -> bool:
    """Whether a new "not responding" gets an alert (not a quick drop-out again)."""
    return recovered_at is None or now - recovered_at >= REALERT_AFTER


def duration_text(value: timedelta) -> str:
    """'45 minutes', '2 hours', '3 days' for messages and the settings page."""
    seconds = value.total_seconds()
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    if seconds < 48 * 3600:
        hours = round(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = round(seconds / 86400)
    return f"{days} days"
