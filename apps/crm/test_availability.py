from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.crm import test_inventory
from apps.crm.calendar_models import (
    CalendarDateOverride,
    HostCalendar,
    WeeklyCalendarBlock,
)
from apps.crm.calendars import CalendarError, available_slots, cipher
from apps.crm.inventory import token_for
from apps.crm.inventory_models import (
    InventoryBooking,
    InventoryChild,
    InventoryInvitation,
)
from apps.crm.models import Lead
from apps.crm.test_calendars import EMPTY, URL


class LocalAvailabilityTests(TestCase):
    def setUp(self):
        test_inventory.ConsultationAvailabilityTests.setUp(self)
        self.profile = HostCalendar.objects.create(host=self.bethany)
        self.settings_url = reverse("crm_calendar_settings")
        self.slot.starts_at = datetime(2026, 10, 5, 14, tzinfo=dt_timezone.utc)
        self.slot.ends_at = self.slot.starts_at + timedelta(minutes=30)
        self.slot.active = True
        self.slot.save()

    def weekly(self, weekday=0, start=time(9), end=time(11), mode="range"):
        return WeeklyCalendarBlock.objects.create(
            calendar=self.profile,
            weekday=weekday,
            mode=mode,
            starts_at=start if mode == "range" else None,
            ends_at=end if mode == "range" else None,
        )

    def override(self, day=date(2026, 10, 5), mode="none", start=None, end=None):
        return CalendarDateOverride.objects.create(
            calendar=self.profile,
            date=day,
            mode=mode,
            starts_at=start,
            ends_at=end,
        )

    def payload(self):
        data = {"action": "save_weekly", "blocking_timezone": "America/New_York"}
        for day in range(7):
            data.update(
                {
                    f"day-{day}-mode": "range",
                    f"day-{day}-starts_at": "09:00",
                    f"day-{day}-ends_at": "11:00",
                }
            )
        return data

    def test_weekly_blocks_without_external_calendar_and_preserves_other_hosts(self):
        self.weekly()
        self.assertEqual(available_slots([self.slot]), [])
        self.slot.host = self.other
        self.assertEqual(available_slots([self.slot]), [self.slot])

    def test_boundaries_are_open_and_weekdays_are_local(self):
        rule = self.weekly(start=time(9), end=time(10))
        self.assertEqual(available_slots([self.slot]), [self.slot])
        rule.starts_at, rule.ends_at = time(10, 30), time(11)
        rule.save()
        self.assertEqual(available_slots([self.slot]), [self.slot])
        self.profile.blocking_timezone = "Pacific/Honolulu"
        self.profile.save()
        self.weekly(weekday=6, start=time(23), end=time(23, 59))
        self.slot.starts_at = datetime(2026, 10, 5, 9, 10, tzinfo=dt_timezone.utc)
        self.slot.ends_at = self.slot.starts_at + timedelta(minutes=20)
        self.assertEqual(available_slots([self.slot]), [])

    def test_all_day_and_open_override_then_removal(self):
        self.weekly(mode="all")
        self.assertEqual(available_slots([self.slot]), [])
        exception = self.override()
        self.assertEqual(available_slots([self.slot]), [self.slot])
        exception.delete()
        self.assertEqual(available_slots([self.slot]), [])

    def test_custom_override_replaces_weekly_and_all_day_override_without_weekly(self):
        self.weekly()
        exception = self.override(mode="range", start=time(14), end=time(16))
        self.assertEqual(available_slots([self.slot]), [self.slot])
        exception.mode, exception.starts_at, exception.ends_at = "all", None, None
        exception.save()
        self.profile.weekly_blocks.all().delete()
        self.assertEqual(available_slots([self.slot]), [])

    def test_overnight_weekly_and_date_override_clip_at_date_boundary(self):
        self.weekly(weekday=6, start=time(22), end=time(11))
        self.assertEqual(available_slots([self.slot]), [])
        self.override()
        self.assertEqual(available_slots([self.slot]), [self.slot])

    def test_previous_date_override_replaces_overnight_carry(self):
        self.weekly(weekday=6, start=time(22), end=time(11))
        exception = self.override(day=date(2026, 10, 4))
        self.assertEqual(available_slots([self.slot]), [self.slot])
        exception.mode, exception.starts_at, exception.ends_at = (
            "range",
            time(23),
            time(11),
        )
        exception.save()
        self.assertEqual(available_slots([self.slot]), [])

    def test_dst_repeated_hour_and_spring_forward_are_blocked(self):
        rule = self.weekly(weekday=6, start=time(1), end=time(2))
        for hour in (5, 6):
            self.slot.starts_at = datetime(
                2026, 11, 1, hour, 15, tzinfo=dt_timezone.utc
            )
            self.slot.ends_at = self.slot.starts_at + timedelta(minutes=30)
            self.assertEqual(available_slots([self.slot]), [])
        rule.starts_at, rule.ends_at = time(2, 30), time(3, 15)
        rule.save()
        self.slot.starts_at = datetime(2027, 3, 14, 7, tzinfo=dt_timezone.utc)
        self.slot.ends_at = self.slot.starts_at + timedelta(minutes=10)
        self.assertEqual(available_slots([self.slot]), [])

    @patch("apps.crm.calendars.fetch_calendar", return_value=EMPTY)
    def test_override_does_not_bypass_provider_conflicts_or_errors(self, fetch):
        self.override()
        self.profile.encrypted_url = cipher().encrypt(URL.encode()).decode()
        self.profile.save()
        with patch(
            "apps.crm.calendars.check_calendar",
            return_value=[(self.slot.starts_at, self.slot.ends_at)],
        ):
            self.assertEqual(available_slots([self.slot]), [])
        fetch.side_effect = CalendarError("Unavailable")
        self.assertEqual(available_slots([self.slot]), [])

    def test_save_seven_days_customize_and_clear_preserves_connection(self):
        self.profile.encrypted_url = cipher().encrypt(URL.encode()).decode()
        self.profile.save()
        data = self.payload()
        data["day-2-mode"] = "none"
        data["day-6-mode"] = "all"
        self.assertEqual(self.client.post(self.settings_url, data).status_code, 302)
        self.assertEqual(self.profile.weekly_blocks.count(), 6)
        self.assertEqual(self.profile.weekly_blocks.get(weekday=6).mode, "all")
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.encrypted_url)
        for day in range(7):
            data[f"day-{day}-mode"] = "none"
        self.client.post(self.settings_url, data)
        self.assertFalse(self.profile.weekly_blocks.exists())

    def test_invalid_save_is_atomic_retains_values_and_errors(self):
        self.weekly()
        for field, value in [
            ("day-1-ends_at", "09:00"),
            ("day-2-starts_at", ""),
            ("blocking_timezone", "Bad/Zone"),
            ("day-3-mode", "bad"),
        ]:
            data = self.payload()
            data[field] = value
            response = self.client.post(self.settings_url, data)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "errorlist")
            self.assertEqual(self.profile.weekly_blocks.count(), 1)

    def test_missing_day_does_not_silently_clear_existing_rules(self):
        data = self.payload()
        del data["day-6-mode"]
        self.assertEqual(self.client.post(self.settings_url, data).status_code, 200)
        self.assertFalse(self.profile.weekly_blocks.exists())

    def test_update_date_override_and_delete_are_owner_scoped(self):
        data = {
            "action": "save_override",
            "override-date": "2026-10-05",
            "override-mode": "all",
            "host": self.other.pk,
        }
        self.assertEqual(self.client.post(self.settings_url, data).status_code, 302)
        data["override-mode"] = "none"
        self.client.post(self.settings_url, data)
        self.assertEqual(self.profile.date_overrides.count(), 1)
        exception = self.profile.date_overrides.get()
        self.assertEqual(exception.mode, "none")
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.post(
                self.settings_url,
                {"action": "delete_override", "override_id": exception.pk},
            ).status_code,
            404,
        )
        self.assertTrue(CalendarDateOverride.objects.filter(pk=exception.pk).exists())
        self.client.force_login(self.bethany)
        self.assertEqual(
            self.client.post(
                self.settings_url,
                {"action": "delete_override", "override_id": exception.pk},
            ).status_code,
            302,
        )
        self.assertFalse(self.profile.date_overrides.exists())

    def test_anonymous_cannot_save_and_other_host_cannot_target_profile(self):
        self.client.logout()
        self.assertEqual(
            self.client.post(self.settings_url, self.payload()).status_code, 302
        )
        self.assertFalse(self.profile.weekly_blocks.exists())
        self.client.force_login(self.other)
        data = self.payload() | {"host": self.bethany.pk}
        self.client.post(self.settings_url, data)
        self.assertFalse(self.profile.weekly_blocks.exists())
        self.assertEqual(
            WeeklyCalendarBlock.objects.filter(calendar__host=self.other).count(), 7
        )

    def test_settings_render_saved_times_and_disconnect_preserves_rules(self):
        self.weekly(start=time(22), end=time(7))
        self.override()
        response = self.client.get(self.settings_url)
        self.assertContains(response, 'value="22:00"')
        self.assertContains(response, "Weekly blocks")
        self.assertContains(response, "Date overrides")
        self.client.post(self.settings_url, {"action": "disconnect"})
        self.assertEqual(self.profile.weekly_blocks.count(), 1)
        self.assertEqual(self.profile.date_overrides.count(), 1)

    def test_booking_rechecks_new_block_and_existing_booking_is_preserved(self):
        parent = Lead.objects.create(
            contact_name="Parent", contact_email="parent@example.com"
        )
        child = InventoryChild.objects.create(
            parent=parent, name="Child", grade="grade_1"
        )
        invitation = InventoryInvitation.objects.create(
            child=child,
            recipient=parent.contact_email,
            completed_at=timezone.now(),
            result={"outcome": "support"},
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.slot.starts_at = timezone.now() + timedelta(days=2)
        self.slot.ends_at = self.slot.starts_at + timedelta(minutes=30)
        self.slot.save()
        url = reverse("inventory_booking", args=[token_for(invitation)])
        self.client.logout()
        self.assertEqual(len(self.client.get(url).context["slots"]), 1)
        rule = self.weekly(
            weekday=self.slot.starts_at.astimezone(
                ZoneInfo("America/New_York")
            ).weekday(),
            mode="all",
        )
        payload = {"slot": self.slot.pk, "phone": "4075550123", "timezone": "UTC"}
        self.assertEqual(len(self.client.get(url).context["slots"]), 0)
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertFalse(InventoryBooking.objects.exists())
        rule.delete()
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.client.force_login(self.bethany)
        self.client.post(self.settings_url, self.payload())
        self.assertEqual(InventoryBooking.objects.count(), 1)

    @patch("apps.crm.google_calendar.busy_periods")
    def test_google_sign_in_busy_times_and_local_rules_both_apply(self, busy):
        self.profile.encrypted_google_refresh_token = (
            cipher().encrypt(b"google-token").decode()
        )
        self.profile.google_email = "host@example.com"
        self.profile.save()
        self.override()
        busy.return_value = [(self.slot.starts_at, self.slot.ends_at)]
        self.assertEqual(available_slots([self.slot]), [])
        busy.return_value = []
        self.assertEqual(available_slots([self.slot]), [self.slot])
        self.profile.date_overrides.all().delete()
        self.weekly(mode="all")
        self.assertEqual(available_slots([self.slot]), [])
        self.client.post(self.settings_url, self.payload())
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.encrypted_google_refresh_token)
        busy.side_effect = CalendarError("Unavailable")
        self.assertEqual(available_slots([self.slot]), [])
