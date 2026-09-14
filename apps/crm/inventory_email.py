"""Shared assessment email layout for HTML, plain text, and the CRM preview."""

import re

from django.conf import settings

from apps.crm.inventory_models import InventoryMail

LOGO_PATH = "/assets/logo/cc-lockup-linen-ui.png"


def split_signoff(body: str) -> tuple[str, str]:
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    match = re.search(
        r"^[ \t]*thank you[,!.]?[ \t]*(?:\n|$)", body, re.IGNORECASE | re.MULTILINE
    )
    if match is None:
        return body, ""
    return body[: match.start()].rstrip(), body[match.start() :]


def logo_url() -> str:
    return settings.PUBLIC_APP_URL.rstrip("/") + LOGO_PATH


def plain_text(mail: InventoryMail) -> str:
    if not mail.action_url:
        return mail.body
    before, after = split_signoff(mail.body)
    return "\n\n".join(
        part
        for part in (before, f"{mail.action_label}: {mail.action_url}", after)
        if part
    )
