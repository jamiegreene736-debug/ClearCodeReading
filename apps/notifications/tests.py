from unittest.mock import patch

from django.test import SimpleTestCase

from apps.notifications.services import NotificationService


class NotificationServiceTests(SimpleTestCase):
    def test_sms_stub_returns_sent_count(self):
        with patch("apps.notifications.services.AuditLog.objects.create"):
            result = NotificationService().send_sms("Test message", ["+15555550123", ""])
        self.assertEqual(result["channel"], "sms")
        self.assertEqual(result["sent"], 1)

    def test_absolute_consent_url_uses_api_path(self):
        url = NotificationService()._absolute_url("guardian-relationship-detail", 123)
        self.assertTrue(url.endswith("/api/v1/guardian-relationships/123/"))
