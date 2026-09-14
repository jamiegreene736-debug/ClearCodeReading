import logging
import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import close_old_connections
from django_tenants.utils import schema_context

from apps.crm_email.worker import run_pass

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process the durable CRM email outbox and synchronize connected mailboxes."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        while True:
            try:
                with schema_context("public"):
                    run_pass()
            except Exception as exc:
                # A supervisor loop must survive unexpected provider/data failures. Never log email content,
                # credentials, provider response bodies or exception strings from external libraries.
                logger.error(
                    "crm_email_worker_pass_failed error_type=%s", type(exc).__name__
                )
                if options["once"]:
                    raise
            finally:
                close_old_connections()
            if options["once"]:
                return
            time.sleep(15)
