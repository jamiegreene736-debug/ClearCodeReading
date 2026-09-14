"""Exercise native HTTPS form headers against Django's real CSRF middleware.

Requires Playwright with Chromium and WebKit, and a migrated local database.
All database changes roll back; email uses the in-memory test backend.
"""

import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_HOST") not in {
    "localhost",
    "127.0.0.1",
    "/tmp",
}:
    raise SystemExit("Use a local test database and unset DATABASE_URL.")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clearcodereading.settings")
import django

django.setup()
from django.core import mail
from django.db import transaction
from django.test import Client, override_settings
from playwright.sync_api import sync_playwright

from apps.users.models import CustomUser

BASE = "https://testserver"


def submit_in_browser(
    engine: str,
    html: str,
    path: str,
    client: Client,
    fields: dict[str, str],
    button: str,
) -> dict:
    captured = {}
    external = {}
    with sync_playwright() as p:
        browser = getattr(p, engine).launch(headless=True)
        context = browser.new_context()
        context.add_cookies(
            [
                {
                    "name": key,
                    "value": cookie.value,
                    "domain": "testserver",
                    "path": "/",
                    "secure": True,
                }
                for key, cookie in client.cookies.items()
            ]
        )
        page = context.new_page()
        page.set_default_timeout(10000)

        def intercept(route):
            request = route.request
            if request.url.startswith("https://outside.example"):
                external.update(request.all_headers())
                route.fulfill(body="ok", headers={"Access-Control-Allow-Origin": "*"})
            elif request.method == "POST":
                captured.update(headers=request.all_headers(), data=request.post_data)
                route.fulfill(body="Submission captured locally")
            elif request.resource_type == "document":
                route.fulfill(body=html, content_type="text/html")
            else:
                route.abort()

        page.route("**/*", intercept)
        page.goto(BASE + path)
        page.evaluate(
            "document.body.insertAdjacentHTML('beforeend', '<a id=external-probe href=https://outside.example/pixel>External probe</a>')"
        )
        page.locator("#external-probe").click()
        page.goto(BASE + path)
        for label, value in fields.items():
            page.get_by_label(label, exact=True).fill(value)
        page.get_by_role("button", name=button, exact=True).click()
        page.wait_for_load_state()
        assert captured, "The browser did not submit the form"
        assert captured["headers"].get("origin") == BASE, captured["headers"]
        assert captured["headers"].get("referer", "").startswith(BASE)
        assert external and "referer" not in external, (
            "External request leaked the setup page URL"
        )
        browser.close()
    return captured


def replay(client: Client, path: str, captured: dict):
    return client.post(
        path,
        dict(parse_qsl(captured["data"])),
        secure=True,
        HTTP_ORIGIN=captured["headers"]["origin"],
        HTTP_REFERER=captured["headers"]["referer"],
    )


with (
    override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        USER_INVITATIONS_ALLOW_TEST_EMAIL=True,
        CRM_EMAIL_ENABLED=False,
        PUBLIC_APP_URL=BASE,
        ALLOWED_HOSTS=["testserver"],
    ),
    transaction.atomic(),
):
    admin = CustomUser.objects.create_user(
        username="csrf-browser-admin",
        email="csrf-admin@example.test",
        role="super_admin",
    )
    for engine in ("chromium", "webkit"):
        client = Client(enforce_csrf_checks=True)
        client.force_login(admin)
        path = "/portal/users/"
        page = client.get(path, secure=True)
        assert page.status_code == 200, page.status_code
        email = f"csrf-{engine}@example.test"
        captured = submit_in_browser(
            engine,
            page.content.decode(),
            path,
            client,
            {"First name": "Browser", "Email address": email},
            "Create user & send invitation",
        )
        response = replay(client, path, captured)
        assert response.status_code == 302, response.status_code
        user = CustomUser.objects.get(email=email)
        assert user.invitation.status == "sent"
        setup = re.search(
            r"https://testserver(/account/setup/\S+)", mail.outbox[-1].body
        ).group(1)
        recipient = Client(enforce_csrf_checks=True)
        path = recipient.get(setup, secure=True).url
        page = recipient.get(path, secure=True)
        assert page.status_code == 200, page.status_code
        captured = submit_in_browser(
            engine,
            page.content.decode(),
            path,
            recipient,
            {
                "New password:": "Browser-Test-839!",
                "New password confirmation:": "Browser-Test-839!",
            },
            "Set password",
        )
        response = replay(recipient, path, captured)
        assert response.status_code == 302, response.status_code
        user.refresh_from_db()
        assert user.check_password("Browser-Test-839!")
        assert user.invitation.accepted_at is not None
        print(
            f"{engine}: account created, invitation captured, password set with browser headers and CSRF enforcement; external referrer suppressed"
        )
    transaction.set_rollback(True)
