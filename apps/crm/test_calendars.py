from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.crm import test_inventory
from apps.crm.calendar_models import HostCalendar
from apps.crm.calendars import (
    CalendarError,
    available_slots,
    busy_periods,
    cipher,
    normalize_url,
)

URL = "https://calendar.google.com/calendar/ical/owner/private-secret/basic.ics"
EMPTY = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def feed(body):
    return (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + body.encode() + b"\r\nEND:VCALENDAR\r\n"
    )


class CalendarParsingTests(SimpleTestCase):
    def test_reject_arbitrary_hosts_credentials_ports_and_redirect_targets(self):
        for url in [
            "https://localhost/private",
            "http://calendar.google.com/calendar/ical/x",
            "https://calendar.google.com.evil.test/calendar/ical/x",
            "https://user@calendar.google.com/calendar/ical/x",
            "https://calendar.google.com:8080/calendar/ical/x",
            "https://calendar.google.com/not-a-calendar",
        ]:
            with self.subTest(url=url), self.assertRaises(CalendarError):
                normalize_url(url)
        self.assertEqual(
            normalize_url("webcal://p123-calendars.icloud.com/published/2/abc"),
            "https://p123-calendars.icloud.com/published/2/abc",
        )

    def test_recurring_exclusion_all_day_and_transparent_events(self):
        data = feed("""BEGIN:VEVENT
UID:recurring
DTSTART;TZID=America/New_York:20260914T100000
DTEND;TZID=America/New_York:20260914T110000
RRULE:FREQ=DAILY;COUNT=3
EXDATE;TZID=America/New_York:20260915T100000
END:VEVENT
BEGIN:VEVENT
UID:allday
DTSTART;VALUE=DATE:20260915
DTEND;VALUE=DATE:20260916
END:VEVENT
BEGIN:VEVENT
UID:transparent
DTSTART:20260914T090000Z
DTEND:20260914T100000Z
TRANSP:TRANSPARENT
END:VEVENT""")
        start = datetime(2026, 9, 14, tzinfo=dt_timezone.utc)
        periods = busy_periods(
            data, start, start + timedelta(days=4), "America/New_York"
        )
        self.assertEqual(len(periods), 3)
        self.assertIn(
            (
                datetime(2026, 9, 15, 4, tzinfo=dt_timezone.utc),
                datetime(2026, 9, 16, 4, tzinfo=dt_timezone.utc),
            ),
            periods,
        )

    def test_all_day_event_blocks_late_local_evening_after_utc_midnight(self):
        start = datetime(2026, 9, 16, 3, tzinfo=dt_timezone.utc)
        data = feed(
            "BEGIN:VEVENT\nUID:day\nDTSTART;VALUE=DATE:20260915\nDTEND;VALUE=DATE:20260916\nEND:VEVENT"
        )
        self.assertEqual(
            len(
                busy_periods(
                    data, start, start + timedelta(minutes=30), "America/New_York"
                )
            ),
            1,
        )

    @patch("apps.crm.calendars.requests.get")
    def test_fetch_does_not_follow_redirects_or_accept_large_responses(self, get):
        from apps.crm.calendars import MAX_BYTES, fetch_calendar

        response = get.return_value.__enter__.return_value
        response.status_code = 302
        with self.assertRaises(CalendarError):
            fetch_calendar(URL)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        response.status_code = 200
        response.iter_content.return_value = [b"x" * (MAX_BYTES + 1)]
        with self.assertRaises(CalendarError):
            fetch_calendar(URL)

    def test_malformed_data_fails_closed(self):
        for data in [
            b"<html>login</html>",
            feed("BEGIN:VEVENT\nUID:broken\nEND:VEVENT"),
        ]:
            with self.assertRaises(CalendarError):
                busy_periods(
                    data, timezone.now(), timezone.now() + timedelta(days=1), "UTC"
                )

    def test_encryption_does_not_store_url_in_plaintext(self):
        encrypted = cipher().encrypt(URL.encode())
        self.assertNotIn(b"private-secret", encrypted)
        self.assertEqual(cipher().decrypt(encrypted).decode(), URL)


class CalendarConnectionTests(TestCase):
    def setUp(self):
        test_inventory.ConsultationAvailabilityTests.setUp(self)
        self.slot.starts_at = self.slot.starts_at.replace(microsecond=0)
        self.slot.ends_at = self.slot.ends_at.replace(microsecond=0)
        self.slot.save()
        self.profile = HostCalendar.objects.create(
            host=self.bethany, encrypted_url=cipher().encrypt(URL.encode()).decode()
        )
        self.calendar_url = reverse("crm_calendar_settings")

    @patch("apps.crm.calendar_views.fetch_calendar", return_value=EMPTY)
    def test_connect_uses_logged_in_profile_and_hides_secrets(self, fetch):
        self.client.force_login(self.other)
        response = self.client.post(
            self.calendar_url,
            {
                "calendar_url": URL,
                "source_timezone": "America/New_York",
                "host": self.bethany.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(HostCalendar.objects.filter(host=self.other).exists())
        self.assertNotContains(self.client.get(self.calendar_url), "private-secret")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_checked_at, None)

    @patch(
        "apps.crm.calendars.fetch_calendar", side_effect=CalendarError("Unavailable")
    )
    def test_provider_failure_blocks_connected_host_only(self, fetch):
        self.assertEqual(available_slots([self.slot]), [])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_error, "Unavailable")
        self.profile.encrypted_url = ""
        self.profile.save()
        self.assertEqual(available_slots([self.slot]), [self.slot])

    @patch("apps.crm.calendars.fetch_calendar")
    def test_overlapping_busy_time_blocks_slot_but_boundary_does_not(self, fetch):
        stamp = lambda value: value.strftime("%Y%m%dT%H%M%SZ")
        fetch.return_value = feed(
            f"BEGIN:VEVENT\nUID:busy\nDTSTART:{stamp(self.slot.starts_at)}\nDTEND:{stamp(self.slot.ends_at)}\nEND:VEVENT"
        )
        self.assertEqual(available_slots([self.slot]), [])
        fetch.return_value = feed(
            f"BEGIN:VEVENT\nUID:busy\nDTSTART:{stamp(self.slot.ends_at)}\nDTEND:{stamp(self.slot.ends_at + timedelta(hours=1))}\nEND:VEVENT"
        )
        self.assertEqual(available_slots([self.slot]), [self.slot])

    def test_subscription_rotation_revokes_previous_link(self):
        old = reverse("crm_calendar_feed", args=[self.profile.subscription_token])
        self.assertEqual(self.client.get(old).status_code, 200)
        self.client.post(self.calendar_url, {"action": "rotate"})
        self.assertEqual(self.client.get(old).status_code, 404)

    def test_other_host_cannot_disconnect_calendar(self):
        self.client.force_login(self.other)
        self.client.post(
            self.calendar_url, {"action": "disconnect", "host": self.bethany.pk}
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.encrypted_url)

    def test_anonymous_cannot_change_settings_and_inactive_feed_is_revoked(self):
        self.client.logout()
        self.assertEqual(
            self.client.post(self.calendar_url, {"action": "disconnect"}).status_code,
            302,
        )
        self.bethany.is_active = False
        self.bethany.save()
        url = reverse("crm_calendar_feed", args=[self.profile.subscription_token])
        self.assertEqual(self.client.get(url).status_code, 404)

    @patch("apps.crm.calendars.fetch_calendar", return_value=EMPTY)
    def test_booking_rechecks_calendar_and_feed_contains_only_own_times(self, fetch):
        from apps.crm.inventory import token_for
        from apps.crm.inventory_models import (
            InventoryBooking,
            InventoryChild,
            InventoryInvitation,
        )
        from apps.crm.models import Lead

        parent = Lead.objects.create(
            contact_name="Private Parent", contact_email="private@example.com"
        )
        child = InventoryChild.objects.create(
            parent=parent, name="Private Child", grade="grade_1"
        )
        invitation = InventoryInvitation.objects.create(
            child=child,
            recipient=parent.contact_email,
            completed_at=timezone.now(),
            result={"outcome": "support"},
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.slot.active = True
        self.slot.save()
        url = reverse("inventory_booking", args=[token_for(invitation)])
        self.client.logout()
        self.assertEqual(len(self.client.get(url).context["slots"]), 1)
        fetch.side_effect = CalendarError("Calendar unavailable")
        payload = {"slot": self.slot.pk, "phone": "4075550123", "timezone": "UTC"}
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertFalse(InventoryBooking.objects.exists())
        fetch.side_effect = None
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(InventoryBooking.objects.count(), 1)
        response = self.client.get(
            reverse("crm_calendar_feed", args=[self.profile.subscription_token])
        )
        self.assertContains(response, "BEGIN:VEVENT")
        for secret in [
            "Private Parent",
            "Private Child",
            "private@example.com",
            "4075550123",
        ]:
            self.assertNotContains(response, secret)
        other_profile = HostCalendar.objects.create(host=self.other)
        self.assertNotContains(
            self.client.get(
                reverse("crm_calendar_feed", args=[other_profile.subscription_token])
            ),
            "BEGIN:VEVENT",
        )
