import re
from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse

from apps.crm.models import Lead


PUBLIC_PAGES = {
    "marketing_home": "index.html",
    "marketing_how_it_works": "how-it-works.html",
    "marketing_families": "families.html",
    "marketing_contact": "contact.html",
    "marketing_privacy": "privacy.html",
    "marketing_approach": "approach.html",
    "reading_assessment": "assessment.html",
}


class MarketingPageTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/")

    def _render(self, route_name):
        return get_template(PUBLIC_PAGES[route_name]).render({}, self.request)

    def test_public_pages_render(self):
        for route_name, template_name in PUBLIC_PAGES.items():
            with self.subTest(route_name=route_name):
                route = resolve(reverse(route_name))
                self.assertEqual(route.func.view_initkwargs["template_name"], template_name)
                self.assertIn("<!doctype html>", self._render(route_name).lower())

    def test_homepage_leads_with_intervention_and_consultation(self):
        content = self._render("marketing_home")

        self.assertIn("Reading intervention that shows clear progress", content)
        self.assertIn("Schedule a consultation", content)
        self.assertIn("Live parent dashboard", content)
        self.assertNotIn("For schools &amp; teachers", content)
        self.assertNotIn("4x more clarity", content)

    def test_shared_marketing_navigation_is_consistent(self):
        route_names = [
            "marketing_home",
            "marketing_how_it_works",
            "marketing_families",
            "marketing_contact",
            "marketing_privacy",
            "marketing_approach",
        ]
        expected_links = [
            "/how-it-works/",
            "/families/",
            "/approach/",
            "/privacy/",
            "/contact/",
            "/login/",
        ]
        for route_name in route_names:
            content = self._render(route_name)
            for link in expected_links:
                with self.subTest(route_name=route_name, link=link):
                    self.assertIn(f'href="{link}"', content)

    def test_touched_pages_only_reference_existing_local_assets(self):
        marketing_root = Path(settings.BASE_DIR) / "marketing-website"
        for route_name in PUBLIC_PAGES:
            content = self._render(route_name)
            asset_paths = set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', content))
            self.assertTrue(asset_paths, route_name)
            for asset_path in asset_paths:
                with self.subTest(route_name=route_name, asset_path=asset_path):
                    self.assertTrue((marketing_root / asset_path.removeprefix("/")).is_file())

    def test_assessment_is_positioned_as_optional_not_placement(self):
        content = self._render("reading_assessment")
        self.assertIn("Optional family reading survey", content)
        self.assertIn("not a PFR or OG+ placement instrument", content)
        self.assertIn('href="/contact/"', content)


class ConsultationFormTests(TestCase):
    def test_contact_form_creates_family_lead_and_returns_to_contact_page(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Jamie Parent",
                "email": "parent@example.com",
                "phone": "555-0102",
                "audience": Lead.Audience.PARENT,
                "organization_name": "Family consultation",
                "estimated_students": "1",
                "child_age_grade": "Age 7, grade 2",
                "notes": "We would like help understanding current reading progress.",
                "redirect_to": "/contact/",
            },
        )

        self.assertRedirects(
            response,
            "/contact/?signup=thanks#consultation-form",
            fetch_redirect_response=False,
        )
        lead = Lead.objects.get(contact_email="parent@example.com")
        self.assertEqual(lead.audience, Lead.Audience.PARENT)
        self.assertIn("Child age or grade: Age 7, grade 2", lead.notes)
        self.assertIn("current reading progress", lead.notes)

    def test_contact_form_missing_required_contact_returns_to_form(self):
        response = self.client.post(
            reverse("crm_signup"),
            {"name": "", "email": "", "redirect_to": "/contact/"},
        )

        self.assertRedirects(
            response,
            "/contact/?signup=missing#consultation-form",
            fetch_redirect_response=False,
        )
        self.assertFalse(Lead.objects.exists())
