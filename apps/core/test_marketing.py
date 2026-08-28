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
    "marketing_careers": "careers.html",
    "marketing_contact": "contact.html",
    "marketing_privacy": "privacy.html",
    "marketing_approach": "approach.html",
    "reading_assessment": "assessment.html",
}

BRAND_COLORS = {
    "#0F2B35",
    "#2C4A45",
    "#1A7A7A",
    "#2EB8B8",
    "#5A9E8F",
    "#A8CFC4",
    "#F7F2EA",
    "#E8D5B0",
    "#F5A623",
}

BRAND_KIT_FILES = {
    "cc-lockup-ink-flagship.png",
    "cc-lockup-linen.png",
    "cc-lockup-seafoam.png",
    "cc-lockup-white (1).png",
    "cc-monogram-forest-euc.png",
    "cc-monogram-gold-teal.png",
    "cc-monogram-ink.png",
    "cc-monogram-linen.png",
    "cc-wordmark-dark.png",
    "cc-wordmark-light.png",
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
        self.assertIn("Unlock Reading. Unlock Everything.", content)
        self.assertIn("Schedule a consultation", content)
        self.assertIn("Live parent dashboard", content)
        self.assertNotIn("For schools &amp; teachers", content)
        self.assertNotIn("4x more clarity", content)

    def test_shared_marketing_navigation_is_consistent(self):
        route_names = [
            "marketing_home",
            "marketing_how_it_works",
            "marketing_families",
            "marketing_careers",
            "marketing_contact",
            "marketing_privacy",
            "marketing_approach",
        ]
        expected_links = [
            "/how-it-works/",
            "/families/",
            "/approach/",
            "/blog/",
            "/careers/",
            "/privacy/",
            "/contact/",
            "/login/",
        ]
        for route_name in route_names:
            content = self._render(route_name)
            for link in expected_links:
                with self.subTest(route_name=route_name, link=link):
                    self.assertIn(f'href="{link}"', content)

    def test_public_pages_include_explicit_consent_newsletter_signup(self):
        for route_name in PUBLIC_PAGES:
            content = self._render(route_name)
            with self.subTest(route_name=route_name):
                self.assertIn('id="newsletter-signup"', content)
                self.assertIn('action="/newsletter/subscribe/"', content)
                self.assertIn('name="consent"', content)
                self.assertIn("I can unsubscribe at any time", content)

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

    def test_careers_page_has_teacher_and_company_paths(self):
        content = self._render("marketing_careers")

        self.assertIn("Teach with ClearCode", content)
        self.assertIn("Build ClearCode with us", content)
        self.assertIn('id="career-interest-form"', content)
        self.assertIn('name="career_path"', content)
        self.assertIn('action="/crm/signup/"', content)

    def test_approach_page_uses_the_full_family_pathway(self):
        content = self._render("marketing_approach")

        expected_sections = [
            "The Right Starting Point for Every Student.",
            "The Assessment",
            "Precise Placement",
            "What a Session Looks Like",
            "How Progress Is Tracked",
            "The Same Faces, Every Session",
            "Trained Reading Specialists",
        ]
        for section in expected_sections:
            with self.subTest(section=section):
                self.assertIn(section, content)

        self.assertIn("Students per group, maximum", content)
        self.assertIn("ClearCode provides educational reading instruction", content)
        self.assertIn('href="/contact/"', content)
        self.assertNotIn('href="waitlist.html"', content)

    def test_public_pages_use_the_clearcode_brand_system(self):
        for route_name in PUBLIC_PAGES:
            content = self._render(route_name)
            with self.subTest(route_name=route_name):
                self.assertIn("Barlow+Condensed", content)
                self.assertEqual(
                    content.count("/assets/logo/cc-lockup-ink-ui.png"),
                    2,
                )
                self.assertNotIn("/assets/logo/cc-lockup-linen-ui.png", content)
                self.assertIn("/assets/logo/cc-favicon-gold-teal-32.png", content)
                self.assertIn("/assets/logo/cc-apple-touch-icon-gold-teal-180.png", content)
                self.assertNotIn("clear-code-reading-logo", content)
                self.assertNotIn("clear-code-reading-icon", content)
                self.assertNotIn("logo-plate", content)

        homepage = self._render("marketing_home")
        for color in BRAND_COLORS:
            with self.subTest(color=color):
                self.assertIn(color, homepage)

    def test_supplied_brand_kit_is_retained(self):
        brand_kit = (
            Path(settings.BASE_DIR)
            / "marketing-website"
            / "assets"
            / "logo"
            / "brand-kit"
        )

        self.assertEqual(
            {asset.name for asset in brand_kit.iterdir() if asset.is_file()},
            BRAND_KIT_FILES,
        )

    def test_authenticated_templates_share_the_brand_identity(self):
        template_paths = [
            "templates/registration/login.html",
            "templates/portal/dashboard.html",
            "templates/portal/inbox.html",
            "templates/sessions/rapid_log.html",
        ]

        for relative_path in template_paths:
            content = (Path(settings.BASE_DIR) / relative_path).read_text()
            with self.subTest(template=relative_path):
                self.assertIn("Barlow+Condensed", content)
                self.assertIn("cc-favicon-gold-teal-32.png", content)
                self.assertIn("cc-apple-touch-icon-gold-teal-180.png", content)
                self.assertIn("#0F2B35", content)
                self.assertIn("#F7F2EA", content)

    def test_root_favicon_uses_the_branded_icon(self):
        route = resolve("/favicon.ico")
        favicon_path = route.kwargs["document_root"] / route.kwargs["path"]

        self.assertEqual(route.url_name, "favicon")
        self.assertEqual(route.kwargs["path"], "logo/favicon.ico")
        self.assertTrue(favicon_path.is_file())


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

    def test_career_interest_creates_teacher_lead_and_returns_to_careers(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Morgan Specialist",
                "email": "morgan@example.com",
                "audience": Lead.Audience.OTHER,
                "career_path": "teacher",
                "role_interest": "Reading specialist",
                "notes": "I have five years of structured literacy experience.",
                "redirect_to": "/careers/",
            },
        )

        self.assertRedirects(
            response,
            "/careers/?signup=thanks#career-interest-form",
            fetch_redirect_response=False,
        )
        lead = Lead.objects.get(contact_email="morgan@example.com")
        self.assertEqual(lead.audience, Lead.Audience.TEACHER)
        self.assertEqual(lead.metadata["career_path"], "teacher")
        self.assertIn("Role interest: Reading specialist", lead.notes)

    def test_career_interest_creates_company_lead(self):
        self.client.post(
            reverse("crm_signup"),
            {
                "name": "Avery Builder",
                "email": "avery@example.com",
                "career_path": "company",
                "role_interest": "Product design",
                "notes": "I build accessible education products.",
                "redirect_to": "/careers/",
            },
        )

        lead = Lead.objects.get(contact_email="avery@example.com")
        self.assertEqual(lead.audience, Lead.Audience.OTHER)
        self.assertEqual(lead.metadata["career_path"], "company")
        self.assertIn("Role interest: Product design", lead.notes)

    def test_career_interest_requires_a_valid_path_and_role(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Taylor Candidate",
                "email": "taylor@example.com",
                "career_path": "invalid",
                "role_interest": "",
                "redirect_to": "/careers/",
            },
        )

        self.assertRedirects(
            response,
            "/careers/?signup=missing#career-interest-form",
            fetch_redirect_response=False,
        )
        self.assertFalse(Lead.objects.exists())
