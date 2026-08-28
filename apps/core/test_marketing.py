import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse

from apps.core.models import RecruitingInterest
from apps.crm.models import FormSubmission, Lead


PUBLIC_PAGES = {
    "marketing_home": "index.html",
    "marketing_about": "about.html",
    "marketing_how_it_works": "how-it-works.html",
    "marketing_families": "families.html",
    "marketing_foundation": "foundation.html",
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

LEARNING_PHOTOS_BY_PAGE = {
    "marketing_home": {
        "specialist-reading-session.jpg",
        "family-reading-practice.jpg",
    },
    "marketing_how_it_works": {"specialist-reading-session.jpg"},
    "marketing_families": {"family-reading-practice.jpg"},
    "marketing_approach": {"inclusive-literacy-lesson.jpg"},
    "marketing_careers": {"educator-team-collaboration.jpg"},
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

    def test_homepage_matches_family_first_consultation_flow(self):
        content = self._render("marketing_home")

        self.assertIn("Unlock Reading. Unlock Everything.", content)
        self.assertIn("Reading intervention with progress families can see.", content)
        self.assertIn("Request a consultation", content)
        self.assertIn("Three steps. One connected reading path.", content)
        self.assertIn("For schools and specialists", content)
        self.assertIn("Illustrative data", content)
        self.assertIn("Frequently asked questions", content)
        self.assertIn("Explore the Foundation", content)

    def test_homepage_consultation_ctas_use_local_intake(self):
        content = self._render("marketing_home")

        self.assertGreaterEqual(
            content.count('href="/contact/#consultation-form"'),
            4,
        )
        self.assertNotIn("waitlist.html", content)
        self.assertNotIn("docs.google.com", content)

    def test_homepage_keeps_research_claims_attributed(self):
        content = self._render("marketing_home")

        self.assertIn("Florida Department of Education", content)
        self.assertIn("Juel, C.", content)
        self.assertIn("G. Reid Lyon", content)

    def test_homepage_uses_a_local_optimized_hero_photo(self):
        content = self._render("marketing_home")

        self.assertIn('/assets/images/hero-reading-specialist.jpg', content)
        self.assertIn('fetchpriority="high"', content)
        hero_path = Path(settings.BASE_DIR) / "marketing-website/assets/images/hero-reading-specialist.jpg"
        self.assertTrue(hero_path.is_file())
        self.assertLess(hero_path.stat().st_size, 500_000)

    def test_shared_marketing_navigation_is_consistent(self):
        route_names = [
            "marketing_home",
            "marketing_about",
            "marketing_how_it_works",
            "marketing_families",
            "marketing_foundation",
            "marketing_careers",
            "marketing_contact",
            "marketing_privacy",
            "marketing_approach",
        ]
        expected_links = [
            "/about/",
            "/how-it-works/",
            "/families/",
            "/foundation/",
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

    def test_foundation_is_a_secondary_path_with_explicit_updates_opt_in(self):
        homepage = self._render("marketing_home")
        foundation = self._render("marketing_foundation")

        self.assertGreater(homepage.index("Frequently asked questions"), homepage.index("For families"))
        self.assertGreater(homepage.index("Explore the Foundation"), homepage.index("Frequently asked questions"))
        self.assertIn("Help more children find their way into reading.", foundation)
        self.assertIn('href="#newsletter-signup"', foundation)
        self.assertIn('name="consent"', foundation)

    def test_contact_form_is_short_and_supports_audience_routing(self):
        content = self._render("marketing_contact")

        self.assertIn("Request a consultation", content)
        self.assertIn('name="audience"', content)
        self.assertIn('value="parent"', content)
        self.assertIn('value="school"', content)
        self.assertIn('value="teacher"', content)
        self.assertNotIn('name="child_age_grade"', content)
        self.assertNotIn('name="phone"', content)

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

    def test_key_pages_include_accessible_learning_photography(self):
        for route_name, photo_names in LEARNING_PHOTOS_BY_PAGE.items():
            content = self._render(route_name)
            for photo_name in photo_names:
                with self.subTest(route_name=route_name, photo_name=photo_name):
                    image_tag = re.search(
                        rf'<img[^>]+src="/assets/images/{re.escape(photo_name)}"[^>]*>',
                        content,
                    )
                    self.assertIsNotNone(image_tag)
                    self.assertRegex(image_tag.group(0), r'alt="[^"]+"')
                    self.assertIn('width="1536"', image_tag.group(0))
                    self.assertIn('height="1024"', image_tag.group(0))

    def test_assessment_is_positioned_as_optional_not_placement(self):
        content = self._render("reading_assessment")
        self.assertIn("Optional family reading survey", content)
        self.assertIn("not a PFR or OG+ placement instrument", content)
        self.assertIn('href="/contact/"', content)

    def test_assessment_appends_complete_grade_routed_parent_inventory(self):
        content = self._render("reading_assessment")

        self.assertIn("One more step for the parent.", content)
        self.assertIn("ZIP Code", content)
        self.assertIn("Select your child’s grade", content)
        self.assertIn("High School", content)
        self.assertIn("Start Parent Inventory", content)
        self.assertIn("Choose Yes or No.", content)
        self.assertEqual(content.count("inventoryQuestion('kindergarten-"), 20)
        self.assertEqual(content.count("inventoryQuestion('first-grade-"), 25)
        self.assertEqual(content.count("inventoryQuestion('second-grade-"), 25)
        self.assertEqual(content.count("inventoryQuestion('third-plus-"), 24)
        self.assertIn("resourceAt: 13", content)
        self.assertIn("resourceAt: 16", content)
        self.assertEqual(content.count("resourceAt: 19"), 2)
        self.assertIn("state.inventoryStoppedAtGroup = step.groupIndex", content)
        self.assertIn("Consider reading support.", content)
        self.assertIn("Comprehension and fluency resources", content)
        self.assertIn("Age-appropriate book lists", content)

    def test_assessment_support_interest_allows_three_independent_selections(self):
        content = self._render("reading_assessment")

        self.assertEqual(content.count('name="relationship_interests"'), 3)
        self.assertIn('value="referral_partner"', content)
        self.assertIn('value="donor"', content)
        self.assertIn('value="advocate"', content)
        self.assertIn("select all that apply", content)

    def test_about_page_preserves_the_supplied_positioning_and_sources(self):
        content = self._render("marketing_about")

        self.assertIn("Every Child Deserves to Read", content)
        self.assertIn("The solution exists. Access to it doesn't.", content)
        self.assertIn("2 in 5", content)
        self.assertIn("90&ndash;95%", content)
        self.assertIn("Orton-Gillingham", content)
        self.assertIn("Phonics for Reading", content)
        self.assertIn("Four principles", content)
        self.assertIn("A sustainable model built for students, educators, and families", content)
        self.assertIn("Florida Department of Education", content)
        self.assertIn('href="/contact/#consultation-form"', content)

    def test_contact_page_uses_the_short_local_consultation_form(self):
        content = self._render("marketing_contact")

        self.assertNotIn("docs.google.com", content)
        self.assertIn("Short consultation request", content)
        self.assertIn('id="consultation-form"', content)
        self.assertIn('name="name"', content)
        self.assertIn('name="email"', content)
        self.assertIn('name="audience"', content)
        self.assertIn('name="notes"', content)
        self.assertNotIn('name="phone"', content)
        self.assertNotIn('name="child_age_grade"', content)

    def test_about_principles_use_large_accessible_editorial_marks(self):
        content = self._render("marketing_about")

        self.assertEqual(content.count('data-testid="principle-icon"'), 4)
        self.assertEqual(content.count('data-icon-style="editorial-mark"'), 4)
        self.assertEqual(content.count('viewBox="0 0 128 128"'), 4)
        self.assertEqual(content.count('class="mx-auto mt-5 h-40 w-40 max-w-full"'), 4)
        self.assertEqual(content.count('aria-hidden="true" focusable="false"'), 4)
        self.assertNotIn("<linearGradient", content)
        self.assertNotIn("<filter", content)
        self.assertNotIn("feDropShadow", content)
        self.assertNotIn('class="mx-auto h-12 w-12', content)

    def test_careers_page_uses_supplied_clinical_team_positioning(self):
        content = self._render("marketing_careers")

        self.assertIn("You Know How to Teach Reading.", content)
        self.assertIn("Three Students, Maximum", content)
        self.assertIn("Paid Planning Time", content)
        self.assertIn("Profit Sharing", content)
        self.assertIn("Certification, On Us", content)
        self.assertIn("We're hiring ahead of our 2027 opening", content)
        self.assertIn("Reading Specialist", content)
        self.assertIn("Educators Seeking OG Certification", content)
        self.assertIn("Orlando Metro", content)
        self.assertIn('id="career-interest-form"', content)
        self.assertIn('name="career_path"', content)
        self.assertIn('action="/crm/signup/"', content)
        self.assertIn('enctype="multipart/form-data"', content)
        self.assertIn('name="phone"', content)
        self.assertIn('name="address"', content)
        self.assertIn('name="email"', content)
        self.assertIn('name="resume"', content)
        self.assertIn('name="cover_letter"', content)
        self.assertIn('name="how_heard"', content)
        self.assertNotIn('name="role_interest"', content)
        self.assertNotIn('name="notes"', content)
        self.assertEqual(content.count('href="#career-interest-form"'), 3)

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


class ContactFormTests(TestCase):
    @staticmethod
    def _document(name):
        return SimpleUploadedFile(name, b"%PDF-1.4\nClearCode test document", "application/pdf")

    def _career_application(self, career_path="teacher"):
        return {
            "name": "Morgan Specialist",
            "email": "morgan@example.com",
            "phone": "555-0142",
            "address": "123 Reading Lane\nOrlando, FL 32801",
            "how_heard": "Teacher referral",
            "career_path": career_path,
            "resume": self._document("Morgan Resume.pdf"),
            "cover_letter": self._document("Morgan Cover Letter.pdf"),
            "redirect_to": "/careers/",
        }

    def test_contact_form_creates_generic_lead_and_returns_to_contact_page(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Jamie Reader",
                "email": "reader@example.com",
                "audience": Lead.Audience.OTHER,
                "organization_name": "Website contact",
                "notes": "I have a question about ClearCode Reading.",
                "redirect_to": "/contact/",
            },
        )

        self.assertRedirects(
            response,
            "/contact/?signup=thanks#consultation-form",
            fetch_redirect_response=False,
        )
        lead = Lead.objects.get(contact_email="reader@example.com")
        submission = FormSubmission.objects.get(lead=lead)
        self.assertEqual(lead.audience, Lead.Audience.OTHER)
        self.assertEqual(lead.notes, "I have a question about ClearCode Reading.")
        self.assertEqual(submission.form_type, FormSubmission.FormType.WEBSITE)
        self.assertEqual(submission.source_path, "/contact/")
        self.assertFalse(lead.opportunities.exists())

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
        self.assertFalse(RecruitingInterest.objects.exists())

    def test_contact_form_requires_a_message(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Jamie Reader",
                "email": "reader@example.com",
                "audience": Lead.Audience.OTHER,
                "organization_name": "Website contact",
                "notes": "   ",
                "redirect_to": "/contact/",
            },
        )

        self.assertRedirects(
            response,
            "/contact/?signup=missing#consultation-form",
            fetch_redirect_response=False,
        )
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(RecruitingInterest.objects.exists())

    def test_career_interest_stays_in_recruiting_and_returns_to_careers(self):
        response = self.client.post(
            reverse("crm_signup"),
            self._career_application(),
        )

        self.assertRedirects(
            response,
            "/careers/?signup=thanks#career-interest-form",
            fetch_redirect_response=False,
        )
        interest = RecruitingInterest.objects.get(email="morgan@example.com")
        self.assertEqual(interest.career_path, RecruitingInterest.CareerPath.TEACHER)
        self.assertEqual(interest.role_interest, "Teaching or reading specialist")
        self.assertEqual(interest.phone, "555-0142")
        self.assertEqual(interest.address, "123 Reading Lane\nOrlando, FL 32801")
        self.assertEqual(interest.how_heard, "Teacher referral")
        self.assertEqual(interest.resume_original_name, "Morgan Resume.pdf")
        self.assertEqual(interest.cover_letter_original_name, "Morgan Cover Letter.pdf")
        self.assertIn(b"ClearCode test document", bytes(interest.resume_data))
        self.assertIn(b"ClearCode test document", bytes(interest.cover_letter_data))
        self.assertEqual(interest.resume_content_type, "application/pdf")
        self.assertEqual(interest.cover_letter_content_type, "application/pdf")
        self.assertFalse(interest.resume)
        self.assertFalse(interest.cover_letter)
        self.assertFalse(Lead.objects.filter(contact_email="morgan@example.com").exists())

    @override_settings(RECRUITING_OWNER_EMAIL="recruiting-owner@example.com")
    def test_career_interest_is_handed_to_named_recruiting_owner(self):
        owner = get_user_model().objects.create_superuser(
            username="recruiting-owner",
            email="recruiting-owner@example.com",
            password="test-password",
        )

        application = self._career_application()
        application["email"] = "morgan-owner@example.com"
        self.client.post(reverse("crm_signup"), application)

        interest = RecruitingInterest.objects.get(email="morgan-owner@example.com")
        self.assertEqual(interest.candidate_pool, "ClearCode recruiting")
        self.assertEqual(interest.owner, owner)
        self.assertEqual(interest.status, RecruitingInterest.Status.REVIEWING)
        self.assertFalse(Lead.objects.filter(contact_email="morgan-owner@example.com").exists())

    def test_company_career_interest_stays_out_of_crm(self):
        application = self._career_application(career_path="company")
        application.update(name="Avery Builder", email="avery@example.com")
        self.client.post(
            reverse("crm_signup"),
            application,
        )

        interest = RecruitingInterest.objects.get(email="avery@example.com")
        self.assertEqual(interest.career_path, RecruitingInterest.CareerPath.COMPANY)
        self.assertEqual(interest.role_interest, "Company team")
        self.assertFalse(Lead.objects.filter(contact_email="avery@example.com").exists())

    def test_career_interest_rejects_an_unsafe_document_type(self):
        application = self._career_application()
        application["resume"] = SimpleUploadedFile(
            "payload.exe",
            b"not a document",
            "application/octet-stream",
        )
        response = self.client.post(
            reverse("crm_signup"),
            application,
        )

        self.assertRedirects(
            response,
            "/careers/?signup=invalid#career-interest-form",
            fetch_redirect_response=False,
        )
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(RecruitingInterest.objects.exists())

    def test_recruiting_documents_are_only_downloadable_by_authorized_staff(self):
        self.client.post(reverse("crm_signup"), self._career_application())
        interest = RecruitingInterest.objects.get()
        download_url = reverse(
            "admin:core_recruitinginterest_document",
            args=(interest.pk, "resume"),
        )

        anonymous_response = self.client.get(download_url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn("/admin/login/", anonymous_response.url)

        admin_user = get_user_model().objects.create_superuser(
            username="recruiting-admin",
            email="recruiting-admin@example.com",
            password="test-password",
        )
        self.client.force_login(admin_user)
        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn('filename="Morgan Resume.pdf"', response["Content-Disposition"])
        self.assertIn(b"ClearCode test document", b"".join(response.streaming_content))
