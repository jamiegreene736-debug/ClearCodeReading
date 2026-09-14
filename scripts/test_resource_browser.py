import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if os.environ.get("POSTGRES_HOST") not in {"localhost", "127.0.0.1"} or os.environ.get(
    "DATABASE_URL"
):
    raise SystemExit(
        "Run this smoke test only against a local test database; unset DATABASE_URL."
    )
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clearcodereading.settings")
import django

django.setup()
from django.test import Client
from playwright.sync_api import expect, sync_playwright

from apps.users.models import CustomUser

user, _ = CustomUser.objects.get_or_create(
    email="resource-browser@example.test",
    defaults={"username": "resource-browser", "is_staff": True, "is_superuser": True},
)
client = Client()
client.force_login(user)
session = client.cookies["sessionid"].value
with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 1100})
    context.add_cookies(
        [{"name": "sessionid", "value": session, "domain": "127.0.0.1", "path": "/"}]
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8876/resources/manage/add/")
    page.screenshot(path="/tmp/resource-add-desktop.png", full_page=True)
    page.get_by_role("button", name="Start writing").click()
    page.locator("#id_body").fill(
        "Make time for ten calm minutes of reading together.\n\nLet your child choose a book. Take turns reading and talk about what happens next."
    )
    page.locator("#id_title").fill("A calmer reading routine")
    page.locator("#id_description").fill(
        "A short, practical guide to making daily reading feel manageable for the whole family."
    )
    page.locator("#id_topic").select_option(label="Reading at home")
    expect(page.locator("#save-state")).to_contain_text("Draft saved", timeout=15000)
    page.get_by_role("link", name="Preview resource", exact=True).click()
    expect(page.locator("#preview-dialog")).to_be_visible()
    frame = page.frame_locator("#preview-frame")
    expect(
        frame.get_by_role("heading", name="A calmer reading routine", exact=True).last
    ).to_be_visible()
    page.get_by_role("button", name="Mobile", exact=True).click()
    expect(page.locator("#preview-frame")).to_have_class("rm-preview-frame mobile")
    page.screenshot(path="/tmp/resource-preview-mobile.png", full_page=True)
    page.get_by_role("button", name="Close", exact=True).click()
    page.get_by_role("button", name="Publish now", exact=True).click()
    expect(page.get_by_role("link", name="View resource ↗")).to_be_visible()
    edit_url = page.url
    resource_url = page.get_by_role("link", name="View resource ↗").get_attribute(
        "href"
    )
    page.screenshot(path="/tmp/resource-editor-desktop.png", full_page=True)
    page.locator("#id_body").fill("PRIVATE DRAFT CHANGE")
    expect(page.locator("#save-state")).to_contain_text("Draft saved", timeout=15000)
    guest = browser.new_context()
    gp = guest.new_page()
    gp.goto("http://127.0.0.1:8876" + resource_url)
    expect(gp).to_have_url("http://127.0.0.1:8876/resources/")
    gate = gp.locator('[data-testid="family-resources-gate"]')
    gate.locator("input[name=name]").fill("Browser Test Family")
    gate.locator("input[name=email]").fill("browser-family@example.test")
    gate.get_by_role("button", name="Unlock My Free Resources").click()
    expect(gp).to_have_url("http://127.0.0.1:8876" + resource_url)
    expect(gp.locator("body")).to_contain_text("Make time for ten calm minutes")
    expect(gp.locator("body")).not_to_contain_text("PRIVATE DRAFT CHANGE")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(edit_url)
    expect(page.locator("#id_title")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        "Mobile horizontal overflow"
    )
    page.screenshot(path="/tmp/resource-editor-mobile.png", full_page=True)
    page.goto("http://127.0.0.1:8876/resources/manage/add/")
    page.locator("#files").set_input_files(
        [
            {
                "name": "Reading_practice.txt",
                "mimeType": "text/plain",
                "buffer": b"Practice reading together.",
            }
        ]
    )
    page.get_by_role("button", name="Upload and review").click()
    expect(page.locator("#id_title")).to_have_value("Reading practice")
    page.locator("#id_description").fill("A simple practice worksheet.")
    page.locator("#id_topic").select_option(label="Reading at home")
    page.locator("#id_upload").set_input_files(
        {
            "name": "Reading_updated.txt",
            "mimeType": "text/plain",
            "buffer": b"New worksheet content.",
        }
    )
    expect(page.locator("#save-state")).to_contain_text("Draft saved", timeout=15000)
    expect(page.locator("#id_asset option:checked")).to_have_text("Reading_updated.txt")
    page.locator("#id_description").fill("An updated practice worksheet.")
    expect(page.locator("#save-state")).to_contain_text("Draft saved", timeout=15000)
    page.reload()
    expect(page.locator("#id_asset option:checked")).to_have_text("Reading_updated.txt")
    assert not errors, errors
    print(
        "Browser workflow passed: article autosave, responsive preview, publishing, draft isolation, signup return, mobile layout, upload and replacement retention. No JavaScript errors."
    )
    browser.close()
