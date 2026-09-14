from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.crm.calendar_models import CalendarAuthorization, HostCalendar
from apps.crm.calendars import CalendarError, available_slots, cipher
from apps.crm.google_calendar import SCOPE, freebusy, provider_request
from apps.crm.test_inventory import ConsultationAvailabilityTests

CONFIG = {
    "CRM_CALENDAR_GOOGLE_CLIENT_ID": "calendar-client",
    "CRM_CALENDAR_GOOGLE_CLIENT_SECRET": "calendar-secret",
    "CRM_CALENDAR_REDIRECT_URI": "https://example.com/crm/email/callback/",
    "ENABLE_DEMO_ACCESS": False,
    "SECRET_KEY": "calendar-test-secret-long-enough-for-production-check",
}


@override_settings(**CONFIG)
class GoogleCalendarTests(TestCase):
    def setUp(self):
        ConsultationAvailabilityTests.setUp(self)
        self.client.force_login(self.bethany)
        self.start_url = reverse("crm_google_calendar_connect")
        self.callback_url = reverse("crm_email_callback")

    def start(self):
        response = self.client.post(self.start_url)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.url).query)
        return query["state"][0], query

    def test_consent_is_calendar_only_and_requires_post_and_crm_access(self):
        self.assertEqual(self.client.get(self.start_url).status_code, 405)
        state, query = self.start()
        self.assertTrue(state.startswith("calendar."))
        self.assertEqual(set(query["scope"][0].split()), {"openid", "email", SCOPE})
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["access_type"], ["offline"])
        authorization = CalendarAuthorization.objects.get()
        self.assertNotEqual(authorization.state_hash, state)
        self.assertNotIn("gmail", query["scope"][0])
        self.client.logout()
        self.assertEqual(self.client.post(self.start_url).status_code, 302)
        self.assertEqual(CalendarAuthorization.objects.count(), 1)

    @patch("apps.crm.google_calendar.freebusy", return_value=[])
    @patch("apps.crm.google_calendar.id_token.verify_oauth2_token")
    @patch("apps.crm.google_calendar.provider_request")
    def test_success_encrypts_token_and_connects_only_current_host(
        self, provider, verify, busy
    ):
        state, query = self.start()
        provider.return_value = {
            "id_token": "identity",
            "access_token": "access",
            "refresh_token": "private-refresh",
            "scope": SCOPE,
        }
        verify.return_value = {
            "nonce": query["nonce"][0],
            "email_verified": True,
            "email": "personal@gmail.com",
            "sub": "google-subject",
        }
        response = self.client.get(
            self.callback_url,
            {"state": state, "code": "auth-code", "host": self.other.pk},
        )
        self.assertRedirects(response, reverse("crm_calendar_settings"))
        profile = HostCalendar.objects.get(host=self.bethany)
        self.assertEqual(
            cipher().decrypt(profile.encrypted_google_refresh_token.encode()),
            b"private-refresh",
        )
        self.assertEqual(profile.google_email, "personal@gmail.com")
        self.assertFalse(HostCalendar.objects.filter(host=self.other).exists())
        self.assertTrue(CalendarAuthorization.objects.get().consumed)
        self.assertNotContains(
            self.client.get(reverse("crm_calendar_settings")), "private-refresh"
        )
        provider.reset_mock()
        self.client.get(self.callback_url, {"state": state, "code": "auth-code"})
        provider.assert_not_called()
        self.client.post(reverse("crm_calendar_settings"), {"action": "disconnect"})
        profile.refresh_from_db()
        self.assertFalse(profile.encrypted_google_refresh_token)
        self.assertFalse(profile.google_email)

    @patch("apps.crm.google_calendar.provider_request")
    def test_wrong_session_expired_and_denied_requests_never_exchange_code(
        self, provider
    ):
        state, _ = self.start()
        other_client = Client()
        other_client.force_login(self.bethany)
        other_client.get(self.callback_url, {"state": state, "code": "code"})
        provider.assert_not_called()
        CalendarAuthorization.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.client.get(self.callback_url, {"state": state, "code": "code"})
        provider.assert_not_called()
        state, _ = self.start()
        self.client.get(self.callback_url, {"state": state, "error": "access_denied"})
        provider.assert_not_called()
        self.assertFalse(HostCalendar.objects.filter(host=self.bethany).exists())

    @patch("apps.crm.google_calendar.id_token.verify_oauth2_token")
    @patch("apps.crm.google_calendar.provider_request")
    def test_partial_consent_bad_identity_and_api_failure_preserve_previous_connection(
        self, provider, verify
    ):
        previous = HostCalendar.objects.create(
            host=self.bethany, encrypted_url="previous-encrypted-url"
        )
        for failure in ["scope", "nonce", "api"]:
            with self.subTest(failure=failure):
                state, query = self.start()
                token = {
                    "id_token": "identity",
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "scope": SCOPE if failure != "scope" else "openid",
                }
                verify.return_value = {
                    "nonce": query["nonce"][0] if failure != "nonce" else "wrong",
                    "email_verified": True,
                    "email": "personal@gmail.com",
                    "sub": "subject",
                }
                provider.side_effect = [token, CalendarError("Unavailable")]
                self.client.get(self.callback_url, {"state": state, "code": "code"})
                previous.refresh_from_db()
                self.assertEqual(previous.encrypted_url, "previous-encrypted-url")
                self.assertFalse(previous.encrypted_google_refresh_token)

    @patch("apps.crm.google_calendar.provider_request")
    def test_google_busy_times_and_provider_failure_block_offered_slots(self, provider):
        profile = HostCalendar.objects.create(
            host=self.bethany,
            encrypted_google_refresh_token=cipher().encrypt(b"refresh").decode(),
        )
        provider.side_effect = [
            {"access_token": "access"},
            {
                "calendars": {
                    "primary": {
                        "busy": [
                            {
                                "start": self.slot.starts_at.isoformat(),
                                "end": self.slot.ends_at.isoformat(),
                            }
                        ]
                    }
                }
            },
        ]
        self.assertEqual(available_slots([self.slot]), [])
        profile.refresh_from_db()
        self.assertIsNotNone(profile.last_checked_at)
        provider.side_effect = CalendarError("Reconnect Google Calendar")
        self.assertEqual(available_slots([self.slot]), [])
        profile.refresh_from_db()
        self.assertEqual(profile.last_error, "Reconnect Google Calendar")
        provider.side_effect = [
            {"access_token": "access"},
            {"calendars": {"primary": {"busy": []}}},
        ]
        self.assertEqual(available_slots([self.slot]), [self.slot])

    def test_settings_shows_google_button_and_secondary_link_option(self):
        response = self.client.get(reverse("crm_calendar_settings"))
        self.assertContains(response, "Connect Google Calendar")
        self.assertContains(response, "No calendar link to find or paste")
        self.assertNotContains(response, "Secret address in iCal format")
        response = self.client.get(reverse("inventory_slots"))
        self.assertContains(response, 'class="availability-filter"')
        self.assertContains(response, 'class="availability-actions"')


class GoogleCalendarProviderTests(SimpleTestCase):
    @patch("apps.crm.google_calendar.provider_request")
    def test_malformed_or_partial_freebusy_is_never_treated_as_available(
        self, provider
    ):
        now = timezone.now()
        for result in [
            {},
            {
                "calendars": {
                    "primary": {"errors": [{"reason": "notFound"}], "busy": []}
                }
            },
            {
                "calendars": {
                    "primary": {"busy": [{"start": "invalid", "end": "invalid"}]}
                }
            },
        ]:
            provider.return_value = result
            with self.assertRaises(CalendarError):
                freebusy("access", now, now + timedelta(hours=1))

    @patch("apps.crm.google_calendar.requests.post")
    def test_timeout_rate_limit_auth_failure_and_malformed_json_are_safe(self, post):
        for status in [401, 429, 503]:
            post.return_value.status_code = status
            with self.assertRaises(CalendarError) as caught:
                provider_request(
                    "https://oauth2.googleapis.com/token",
                    data={"refresh_token": "private"},
                )
            self.assertNotIn("private", str(caught.exception))
        post.return_value.status_code = 200
        post.return_value.json.side_effect = ValueError("secret payload")
        with self.assertRaises(CalendarError):
            provider_request("https://oauth2.googleapis.com/token")
        post.side_effect = requests.Timeout("secret URL")
        with self.assertRaises(CalendarError):
            provider_request("https://oauth2.googleapis.com/token")
