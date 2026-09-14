"""Local recurring blocks, clipped to each date before date overrides are applied."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from apps.crm.calendar_models import BlockingRule, HostCalendar


def local_busy_periods(
    profile: HostCalendar, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    zone = ZoneInfo(profile.blocking_timezone)
    first = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    weekly = {rule.weekday: rule for rule in profile.weekly_blocks.all()}
    overrides = {rule.date: rule for rule in profile.date_overrides.all()}
    periods: list[tuple[datetime, datetime]] = []
    day = first
    while day <= last:
        midnight = datetime.combine(day, time.min, zone)
        tomorrow = midnight + timedelta(days=1)
        override = overrides.get(day)
        rules: list[tuple[BlockingRule, date]] = []
        if override is not None:
            rules.append((override, day))
        else:
            if day.weekday() in weekly:
                rules.append((weekly[day.weekday()], day))
            previous = day - timedelta(days=1)
            # An exception on yesterday also replaces yesterday's overnight rule.
            prior = overrides.get(previous, weekly.get(previous.weekday()))
            if (
                prior
                and prior.mode == "range"
                and prior.ends_at is not None
                and prior.starts_at is not None
                and prior.ends_at < prior.starts_at
            ):
                rules.append((prior, previous))
        for rule, anchor in rules:
            if rule.mode == "none":
                continue
            if rule.mode == "all":
                begins, finishes = midnight, tomorrow
            else:
                assert rule.starts_at is not None and rule.ends_at is not None
                begins = datetime.combine(anchor, rule.starts_at, zone)
                finishes = datetime.combine(anchor, rule.ends_at, zone)
                if rule.ends_at < rule.starts_at:
                    finishes += timedelta(days=1)
                begins, finishes = max(begins, midnight), min(finishes, tomorrow)
            # Cover both occurrences of ambiguous clocks during the autumn change.
            begins = min(
                begins.replace(fold=fold).astimezone(timezone.utc) for fold in (0, 1)
            )
            finishes = max(
                finishes.replace(fold=fold).astimezone(timezone.utc) for fold in (0, 1)
            )
            if begins < finishes and begins < end and finishes > start:
                periods.append((begins, finishes))
        day += timedelta(days=1)
    return periods
