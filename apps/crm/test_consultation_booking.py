from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.crm.consultation_booking import consultation_booking_url
from apps.crm.inventory_models import ConsultationBooking, ConsultationSlot
from apps.crm.models import FormSubmission, Lead, Opportunity, WebsiteReceipt
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
        self.assertEqual(list(response.context["slots"]), [self.slot])
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
        self.assertIn(
            "Consultation time", [row["label"] for row in context["rows"]]
        )
        confirmation = self.client.get(response.url)
        self.assertContains(confirmation, "Your consultation is booked.")
        self.assertContains(confirmation, "pat@example.com")
        # The slot is gone for the next visitor and cannot be double booked.
        self.client.logout()
        second = self.client.post(self.url, {**self.payload, "email": "b@example.com"})
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "no longer available")
        self.assertEqual(ConsultationBooking.objects.count(), 1)
        self.assertEqual(list(self.client.get(self.url).context["slots"]), [])

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
