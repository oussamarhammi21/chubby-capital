from datetime import date, datetime

from chubby.cli import TORONTO, last_closed_day


def test_tick_waits_for_the_close_before_playing_today():
    assert last_closed_day(datetime(2026, 10, 27, 9, 45, tzinfo=TORONTO)) == date(2026, 10, 26)
    assert last_closed_day(datetime(2026, 10, 27, 16, 5, tzinfo=TORONTO)) == date(2026, 10, 26)
    assert last_closed_day(datetime(2026, 10, 27, 16, 20, tzinfo=TORONTO)) == date(2026, 10, 27)
