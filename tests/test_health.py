"""1.1.0 "not responding" detection: the pure rules in health.py."""
from datetime import datetime, timedelta, timezone

from custom_components.battery_states import health

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)


def feed(stats, times, now=None, incident=False):
    for t in times:
        health.record_report(stats, t, timedelta(), incident, now or t)


def every(start, step, count):
    return [start + i * step for i in range(count)]


# ------------------------------------------------------------- learning
def test_steady_device_learns_its_longest_normal_gap():
    stats = health.new_stats()
    times = every(T0, 50 * timedelta(minutes=1), 4 * 24 * 60 // 50)  # every 50 min for 4 days
    feed(stats, times)
    now = times[-1]
    assert health.steady(stats, now)
    assert health.update_base(stats, now) == 50 * 60


def test_not_steady_before_three_days_or_ten_reports():
    stats = health.new_stats()
    feed(stats, every(T0, H, 40))  # 40 h only
    assert not health.steady(stats, T0 + 40 * H)
    assert health.update_base(stats, T0 + 40 * H) is None
    stats = health.new_stats()
    feed(stats, every(T0, 12 * H, 9))  # 4 days, 9 reports
    assert not health.steady(stats, T0 + 96 * H)


def test_bursty_device_like_the_bedroom_contact_is_not_steady():
    """7 reports in 9 days, silences of 4-6 days: its own history can't say what's normal."""
    stats = health.new_stats()
    times = [T0, T0 + timedelta(hours=153), T0 + timedelta(hours=260), T0 + timedelta(hours=261),
             T0 + timedelta(hours=263), T0 + timedelta(hours=273), T0 + timedelta(hours=289)]
    times += [times[-1] + timedelta(minutes=m) for m in (1, 2, 3, 4)]  # a burst: enough reports
    feed(stats, times)
    now = T0 + timedelta(days=13)
    assert not health.steady(stats, now)
    assert health.update_base(stats, now) is None


def test_a_single_long_gap_does_not_become_normal():
    stats = health.new_stats()
    times = every(T0, H, 5 * 24)
    times.insert(60, times[59] + timedelta(minutes=10))  # keep counts simple
    times = [t for t in times if not (T0 + 48 * H < t < T0 + 58 * H)]  # one 10 h gap on day 3
    feed(stats, sorted(times))
    now = T0 + 5 * 24 * H
    assert health.update_base(stats, now) == 3600  # the longest gap seen on 3+ days


def test_excluded_time_is_not_counted_in_a_gap():
    stats = health.new_stats()
    health.record_report(stats, T0, timedelta(), False, T0)
    gap = health.record_report(stats, T0 + 10 * H, 9 * H, False, T0 + 10 * H)
    assert gap == H


def test_old_reports_are_ignored():
    stats = health.new_stats()
    feed(stats, [T0 + H])
    assert health.record_report(stats, T0, timedelta(), False, T0 + H) is None
    assert stats["last"] == health.iso(T0 + H)


# ------------------------------------------------------------- adjusting
def _learned(base_gap=H, days=5):
    stats = health.new_stats()
    times = every(T0, base_gap, int(days * 24 * H / base_gap))
    feed(stats, times)
    health.update_base(stats, times[-1])
    return stats, times[-1]


def test_normal_rises_only_for_a_steady_new_rhythm():
    stats, last = _learned()
    assert stats["base"] == 3600
    times = every(last + 3 * H, 3 * H, 3 * 8)  # every 3 h for 3 days
    feed(stats, times)
    assert health.update_base(stats, times[-1]) == 3 * 3600


def test_slow_decline_does_not_raise_the_normal():
    """Gaps growing day by day (2 h, 3.5 h, 6 h): not one rhythm, the old normal stays."""
    stats, last = _learned()
    t = last
    for gap in (2, 3.5, 6):
        day_times = every(t + timedelta(hours=gap), timedelta(hours=gap), int(24 // gap))
        feed(stats, day_times)
        t = day_times[-1]
    assert health.update_base(stats, t) == 3600


def test_repeated_incidents_of_one_length_are_a_new_rhythm():
    """A device set up to report every 24 h: its first silences are incidents;
    3 of the same length in a week become the new normal."""
    stats, last = _learned()
    t = last
    for _ in range(3):
        t += 24 * H
        health.record_report(stats, t, timedelta(), True, t)
        assert stats["base"] == 3600 or _ == 2
        health.update_base(stats, t)
    assert stats["base"] == 24 * 3600


def test_erratic_incidents_never_become_normal():
    stats, last = _learned()
    t = last
    for gap in (153, 107, 40):
        t += timedelta(hours=gap)
        health.record_report(stats, t, timedelta(), True, t)
        health.update_base(stats, t)
    assert stats["base"] == 3600


def test_normal_falls_after_a_chattier_week():
    stats, last = _learned(base_gap=3 * H, days=4)
    assert stats["base"] == 3 * 3600
    times = every(last + H, H, 9 * 24)
    feed(stats, times)
    assert health.update_base(stats, times[-1]) == 3600


def test_history_older_than_two_weeks_is_dropped():
    stats, last = _learned()
    health.prune(stats, last + timedelta(days=20))
    assert stats["days"] == {}


# ------------------------------------------------------------- intervals and verdicts
def test_intervals_merge_and_cover():
    a = (T0, T0 + 2 * H)
    b = (T0 + H, T0 + 3 * H)
    c = (T0 + 5 * H, T0 + 6 * H)
    assert health.merge([b, a, c]) == [(T0, T0 + 3 * H), c]
    assert health.covered([a, b, c], T0 + 2 * H, T0 + 10 * H) == 2 * H
    assert health.load_intervals(health.dump_intervals([a, b], T0 + 4 * H)) == [(T0, T0 + 3 * H)]
    assert health.load_intervals([["x", "y"], [health.iso(T0 + H), health.iso(T0)]]) == []


def test_threshold():
    minimum = 12 * H
    assert health.threshold(H, "model", minimum, None) == 12 * H
    assert health.threshold(6 * H, "own", minimum, None) == 24 * H
    assert health.threshold(H, "own", minimum, 3 * H) == 3 * H  # the user's setting as it is
    assert health.threshold(None, None, minimum, None) is None


def test_realert_and_durations():
    assert health.realert(None, T0)
    assert not health.realert(T0, T0 + 23 * H)
    assert health.realert(T0, T0 + 24 * H)
    assert health.duration_text(timedelta(minutes=50)) == "50 minutes"
    assert health.duration_text(H) == "1 hour"
    assert health.duration_text(2.7 * H) == "3 hours"
    assert health.duration_text(timedelta(days=3)) == "3 days"


def test_broken_stored_stats_are_tolerated():
    raw = {"days": {"2026-09-01": [3, 120.0], "bad": "x", "2026-09-02": [1]}, "first": "nope",
           "last": health.iso(T0), "base": -5, "incidents": [["x", 1], [health.iso(T0), 60]]}
    stats = health.clean_stats(raw)
    assert stats["days"] == {"2026-09-01": [3, 120.0]}
    assert (stats["first"], stats["last"], stats["base"]) == (None, health.iso(T0), None)
    assert stats["incidents"] == [[health.iso(T0), 60.0]]
    assert health.clean_stats("junk") == health.new_stats()
