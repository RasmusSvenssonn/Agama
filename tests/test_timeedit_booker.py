from datetime import date

from timeedit_booker import target_date


def test_target_date_is_two_days_later() -> None:
    assert target_date(date(2026, 9, 30)) == date(2026, 10, 2)


def test_target_date_crosses_year_boundary() -> None:
    assert target_date(date(2026, 12, 31)) == date(2027, 1, 2)
