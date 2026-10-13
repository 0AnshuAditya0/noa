"""tzutil: Monday week boundaries in Asia/Kolkata, month/year ranges, DST-free."""
from datetime import datetime, timedelta

import tzutil

IST = tzutil.TZ


def dt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=IST)


def test_timezone_is_kolkata_by_default():
    assert tzutil.TZ_NAME == "Asia/Kolkata"


def test_monday_midnight_maps_to_itself():
    assert tzutil.monday_00(dt(2026, 10, 12, 0, 0, 0)) == dt(2026, 10, 12)


def test_just_before_monday_belongs_to_previous_week():
    # Sunday 23:59:59 IST is still last week.
    assert tzutil.monday_00(dt(2026, 10, 11, 23, 59, 59)) == dt(2026, 10, 5)


def test_just_after_monday_midnight_belongs_to_new_week():
    assert tzutil.monday_00(dt(2026, 10, 12, 0, 0, 1)) == dt(2026, 10, 12)


def test_midweek_maps_back_to_monday():
    assert tzutil.monday_00(dt(2026, 10, 14, 15, 30)) == dt(2026, 10, 12)
    assert tzutil.monday_00(dt(2026, 10, 18, 23, 59)) == dt(2026, 10, 12)


def test_week_range_is_seven_days_starting_monday():
    start, end = tzutil.this_week_range()
    assert start.weekday() == 0
    assert (end - start) == timedelta(days=7)
    assert (start.hour, start.minute) == (0, 0)


def test_last_week_adjoins_this_week():
    this_start, _ = tzutil.this_week_range()
    last_start, last_end = tzutil.last_week_range()
    assert last_end == this_start
    assert (this_start - last_start) == timedelta(days=7)


def test_month_range_starts_on_first():
    start, end = tzutil.this_month_range()
    assert (start.day, start.hour, start.minute) == (1, 0, 0)
    assert end >= start


def test_year_range_starts_on_jan_first():
    start, end = tzutil.this_year_range()
    assert (start.month, start.day) == (1, 1)
    assert end >= start


def test_dst_free_all_year():
    # Asia/Kolkata has no DST: offset must be constant across seasons.
    offsets = {
        datetime(2026, 1, 15, 12, tzinfo=IST).utcoffset(),
        datetime(2026, 4, 15, 12, tzinfo=IST).utcoffset(),
        datetime(2026, 7, 15, 12, tzinfo=IST).utcoffset(),
        datetime(2026, 10, 15, 12, tzinfo=IST).utcoffset(),
    }
    assert offsets == {timedelta(hours=5, minutes=30)}


def test_midnight_crossover_arithmetic():
    # 23:30 Sunday + 1h lands on Monday, same absolute instant math as UTC.
    local = dt(2026, 10, 11, 23, 30) + timedelta(hours=1)
    assert local.date().isoformat() == "2026-10-12"
    assert local.weekday() == 0


def test_week_label_format():
    assert tzutil.week_label(dt(2026, 10, 14)) == "2026-W42"
