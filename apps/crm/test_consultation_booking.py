from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.crm.consultation_booking import consultation_booking_url
from apps.crm.inventory_models import (
    ConsultationBooking,
    ConsultationHoursSeed,
    ConsultationOpenWindow,
    ConsultationSlot,
)
from apps.crm.models import FormSubmission, Lead, Opportunity, WebsiteReceipt
from apps.crm.open_hours import (
    PUBLISHED_DATES,
    PUBLISHED_EMAIL,
    PUBLISHED_KEY,
    iter_window_slots,
)
from apps.crm.website_emails import receipt_context


class ConsultationBookingPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.bethany = get_user_model().objects.create_user(
            username="bethany",
            email="bethany@example.com",
            first_name="Bethany",
            last_name="Fleming",
            role="crm_user",
        )
        self.slot = ConsultationSlot.objects.create(
            host=self.bethany,
            active=True,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, minutes=30),
        )
        self.url = reverse("consultation_booking")
        self.payload = {
            "name": "Pat Parent",
            "email": "Pat@Example.com",
            "phone": "4075550123",
            "child_age_grade": "2nd grade",
            "notes": "Struggles with sight words.",
            "slot": str(self.slot.pk),
            "timezone": "America/New_York",
        }

    def test_public_page_lists_only_confirmed_open_slots(self):
        ConsultationSlot.objects.create(
            host=self.bethany,
            active=False,
            starts_at=self.slot.starts_at + timedelta(hours=1),
            ends_at=self.slot.ends_at + timedelta(hours=1),
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        slots = list(response.context["slots"])
        inactive = ConsultationSlot.objects.get(active=False)
        self.assertIn(self.slot, slots)
        self.assertNotIn(inactive, slots)
        self.assertTrue(all(slot.active for slot in slots))
        self.assertContains(response, "Book a consultation with Bethany Fleming")

    def test_booking_creates_contact_deal_receipt_and_takes_the_slot(self):
        response = self.client.post(self.url, self.payload)
        booking = ConsultationBooking.objects.get()
        self.assertRedirects(
            response, f"{self.url}?booked={booking.pk}", fetch_redirect_response=False
        )
        lead = Lead.objects.get(contact_email="pat@example.com")
        self.assertEqual(booking.lead, lead)
        self.assertEqual(booking.slot, self.slot)
        self.assertEqual(lead.contact_phone, "4075550123")
        deal = Opportunity.objects.get(lead=lead)
        self.assertEqual(deal.pipeline, Opportunity.Pipeline.FAMILY_ENROLLMENT)
        self.assertEqual(deal.stage, Opportunity.Stage.FAMILY_CONSULTATION)
        self.assertEqual(deal.owner, self.bethany)
        submission = FormSubmission.objects.get()
        self.assertEqual(submission.form_type, FormSubmission.FormType.CONSULTATION)
        self.assertTrue(submission.submitted_data["consultation_booked"])
        self.assertTrue(WebsiteReceipt.objects.filter(submission=submission).exists())
        context = receipt_context(submission, team=False)
        self.assertEqual(context["heading"], "Your consultation is booked.")
        self.assertIn("Consultation time", [row["label"] for row in context["rows"]])
        confirmation = self.client.get(response.url)
        self.assertContains(confirmation, "Your consultation is booked.")
        self.assertContains(confirmation, "pat@example.com")
        # The slot is gone for the next visitor and cannot be double booked.
        self.client.logout()
        second = self.client.post(self.url, {**self.payload, "email": "b@example.com"})
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "no longer available")
        self.assertEqual(ConsultationBooking.objects.count(), 1)
        self.assertNotIn(self.slot, self.client.get(self.url).context["slots"])

    def test_confirmation_is_only_shown_to_the_booking_session(self):
        self.client.post(self.url, self.payload)
        booking = ConsultationBooking.objects.get()
        other = self.client_class()
        response = other.get(f"{self.url}?booked={booking.pk}")
        self.assertNotContains(response, "Your consultation is booked.")
        self.assertNotContains(response, "pat@example.com")

    def test_honeypot_and_rate_limit_block_abuse(self):
        response = self.client.post(self.url, {**self.payload, "website": "spam"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ConsultationBooking.objects.exists())
        cache.set("consultation-booking:127.0.0.1", 20, 60)
        response = self.client.post(self.url, self.payload)
        self.assertContains(response, "Too many booking attempts")
        self.assertFalse(ConsultationBooking.objects.exists())

    def test_invalid_slot_or_email_is_rejected(self):
        response = self.client.post(self.url, {**self.payload, "slot": "999999"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ConsultationBooking.objects.exists())
        response = self.client.post(self.url, {**self.payload, "email": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Lead.objects.exists())

    def test_booking_link_requires_public_https_site(self):
        with override_settings(PUBLIC_APP_URL="http://localhost:8000"):
            self.assertEqual(consultation_booking_url(), "")
        with override_settings(PUBLIC_APP_URL="https://clearcodereading.com/"):
            self.assertEqual(
                consultation_booking_url(), "https://clearcodereading.com/book/"
            )


class PublishedConsultationHoursTests(TestCase):
    def setUp(self):
        cache.clear()
        self.now = datetime(2026, 9, 28, 18, 38, tzinfo=dt_timezone.utc)
        patcher = patch("django.utils.timezone.now", return_value=self.now)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.bethany = get_user_model().objects.create_user(
            username="bethany-published",
            email=PUBLISHED_EMAIL,
            first_name="Bethany",
            last_name="Fleming",
            role="crm_user",
        )
        self.url = reverse("consultation_booking")

    def test_quarter_hours_fill_three_to_five(self):
        slots = iter_window_slots(
            PUBLISHED_DATES[0], time(15, 0), time(17, 0), "America/New_York"
        )
        self.assertEqual(len(slots), 8)
        self.assertEqual(
            slots[0][0].astimezone(ZoneInfo("America/New_York")).strftime("%H:%M"),
            "15:00",
        )
        self.assertEqual(
            slots[-1][0].astimezone(ZoneInfo("America/New_York")).strftime("%H:%M"),
            "16:45",
        )
        self.assertEqual(slots[-1][1] - slots[-1][0], timedelta(minutes=15))

    def test_family_booking_page_lists_bethanys_published_hours(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        slots = list(response.context["slots"])
        self.assertEqual(len(slots), len(PUBLISHED_DATES) * 8)
        self.assertEqual(ConsultationOpenWindow.objects.count(), len(PUBLISHED_DATES))
        self.assertTrue(
            ConsultationHoursSeed.objects.filter(key=PUBLISHED_KEY).exists()
        )
        self.assertContains(response, "Choose a 15-minute time")
        self.assertContains(response, "October 7")
        self.assertContains(response, "4:45–5:00 PM")
        self.assertNotContains(response, "September 27")
        again = self.client.get(self.url)
        self.assertEqual(len(again.context["slots"]), len(slots))

    def test_booking_takes_one_fifteen_minute_time(self):
        self.client.get(self.url)
        slot = ConsultationSlot.objects.get(
            starts_at=datetime(2026, 10, 7, 20, 0, tzinfo=dt_timezone.utc)
        )
        response = self.client.post(
            self.url,
            {
                "name": "Pat Parent",
                "email": "pat@example.com",
                "phone": "4075550123",
                "child_age_grade": "2nd grade",
                "notes": "",
                "slot": str(slot.pk),
                "timezone": "America/New_York",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ConsultationBooking.objects.filter(slot=slot).exists())
        self.assertEqual(len(self.client.get(self.url).context["slots"]), 63)

    def test_withdrawn_time_stays_off_the_public_page(self):
        self.client.get(self.url)
        slot = (
            ConsultationSlot.objects.filter(host=self.bethany)
            .order_by("starts_at")
            .first()
        )
        slot.active = False
        slot.save(update_fields=["active"])
        self.client.get(self.url)
        slot.refresh_from_db()
        self.assertFalse(slot.active)
        self.assertEqual(
            ConsultationSlot.objects.filter(
                host=self.bethany, starts_at=slot.starts_at
            ).count(),
            1,
        )
        self.assertNotIn(slot, self.client.get(self.url).context["slots"])

    def test_unique_bethany_receives_hours_when_login_email_differs(self):
        self.bethany.email = "bethany.fleming@example.com"
        self.bethany.save(update_fields=["email"])
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["slots"]), len(PUBLISHED_DATES) * 8)
        self.assertEqual(ConsultationOpenWindow.objects.count(), len(PUBLISHED_DATES))
        self.assertEqual(
            set(ConsultationOpenWindow.objects.values_list("host_id", flat=True)),
            {self.bethany.pk},
        )
        self.assertNotContains(response, "no consultation times open")

    def test_ambiguous_bethany_name_does_not_receive_the_published_hours(self):
        self.bethany.email = "bethany@example.com"
        self.bethany.save(update_fields=["email"])
        get_user_model().objects.create_user(
            username="bethany-two",
            email="bethany.two@example.com",
            first_name="Bethany",
            last_name="Fleming",
            role="crm_user",
        )
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["slots"]), [])
        self.assertFalse(ConsultationOpenWindow.objects.exists())
