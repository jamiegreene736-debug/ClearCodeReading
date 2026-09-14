import hashlib
import secrets
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.resources.models import StaffSetupToken


class Command(BaseCommand):
    help = (
        "Issue a private one-use resource publisher setup link, valid for seven days."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--base-url", default="https://clearcodereading.com")

    def handle(self, *args, **options) -> None:
        token = secrets.token_urlsafe(32)
        StaffSetupToken.objects.create(
            digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.stdout.write(
            options["base_url"].rstrip("/")
            + reverse("resources:setup_staff", args=[token])
        )
