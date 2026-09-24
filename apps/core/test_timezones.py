from datetime import UTC, date, datetime
from unittest.mock import patch

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase
from django.utils import timezone

from apps.crm.models import FormSubmission, Lead
from apps.crm.website_emails import receipt_context
from apps.resources.forms import ScheduleForm
from apps.scheduling.optimizer import _window_bounds
from apps.users.models import Profile


class EasternTimeTests(SimpleTestCase):
    # Team receipts read editable wording from the database (no writes happen).
    databases = {"default"}

    def test_application_worker_and_new_profiles_default_to_eastern(self):
        self.assertEqual(settings.TIME_ZONE, "America/New_York")
        self.assertEqual(settings.CELERY_TIMEZONE, settings.TIME_ZONE)
        self.assertEqual(Profile().timezone, settings.TIME_ZONE)
        self.assertTrue(settings.USE_TZ)

    def test_display_converts_summer_winter_and_previous_calendar_day(self):
        cases = [
            (datetime(2026, 9, 14, 18, 33, tzinfo=UTC), "Sep 14, 2026, 2:33 PM EDT"),
            (datetime(2026, 1, 14, 18, 33, tzinfo=UTC), "Jan 14, 2026, 1:33 PM EST"),
            (datetime(2026, 9, 14, 2, 33, tzinfo=UTC), "Sep 13, 2026, 10:33 PM EDT"),
        ]
        template = Template('{{ value|date:"M j, Y, g:i A T" }}')
        for instant, expected in cases:
            with self.subTest(instant=instant), timezone.override(settings.TIME_ZONE):
                self.assertEqual(template.render(Context({"value": instant})), expected)

    def test_team_receipt_uses_eastern_time(self):
        submission = FormSubmission(
            pk=16,
            lead=Lead(pk=14),
            form_type="website",
            source_path="/contact/",
            created_at=datetime(2026, 9, 14, 18, 33, tzinfo=UTC),
            submitted_data={"name": "Test", "email": "test@example.com"},
        )
        context = receipt_context(submission, team=True)
        self.assertIn(
            {"label": "Received (Eastern Time)", "value": "2026-09-14 02:33 PM EDT"},
            context["rows"],
        )

    @patch(
        "apps.resources.forms.timezone.now",
        return_value=datetime(2026, 1, 1, tzinfo=UTC),
    )
    def test_publish_input_converts_eastern_to_correct_instant(self, now):
        for value, hour in [("2026-09-14T14:33", 18), ("2026-01-14T14:33", 19)]:
            with self.subTest(value=value):
                form = ScheduleForm({"publish_at": value})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(
                    form.cleaned_data["publish_at"].astimezone(UTC).hour, hour
                )

    @patch(
        "apps.resources.forms.timezone.now",
        return_value=datetime(2026, 1, 1, tzinfo=UTC),
    )
    def test_publish_rejects_nonexistent_and_ambiguous_dst_times(self, now):
        for value in ["2026-03-08T02:30", "2026-11-01T01:30"]:
            with self.subTest(value=value):
                self.assertFalse(ScheduleForm({"publish_at": value}).is_valid())

    def test_availability_defaults_to_eastern_and_preserves_explicit_zone(self):
        window = {"day_of_week": "monday", "start_time": "09:00", "end_time": "10:00"}
        for zone, hour in [(None, 13), ("America/Los_Angeles", 16)]:
            with self.subTest(zone=zone):
                bounds = _window_bounds({**window, "timezone": zone}, date(2026, 9, 14))
                self.assertIsNotNone(bounds)
                self.assertEqual(bounds[0].astimezone(UTC).hour, hour)
