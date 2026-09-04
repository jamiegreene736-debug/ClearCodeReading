import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import constants as message_constants
from django.contrib.messages.storage.base import Message
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
    "marketing_resources": "resources.html",
    "marketing_faq": "faq.html",
    "marketing_foundation": "foundation.html",
    "marketing_careers": "careers.html",
    "marketing_contact": "contact.html",
    "marketing_support": "support.html",
    "marketing_privacy": "privacy.html",
    "reading_assessment": "assessment.html",
    "early_interest_survey": "survey.html",
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
        "session-carousel-arrival.jpg",
        "session-carousel-settle-in.jpg",
        "session-carousel-multiple-groups.jpg",
        "session-carousel-hands-on.jpg",
        "session-carousel-wrap-up.jpg",
        "specialist-reading-session.jpg",
    },
    "marketing_how_it_works": {
        "inclusive-literacy-lesson.jpg",
        "specialist-reading-session.jpg",
    },
    "marketing_families": {"family-reading-practice.jpg"},
    "marketing_careers": {"educator-team-collaboration.jpg"},
}


class MarketingPageTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/")

    def _render(self, route_name, context=None):
        return get_template(PUBLIC_PAGES[route_name]).render(context or {}, self.request)

    def test_public_pages_render(self):
        for route_name, template_name in PUBLIC_PAGES.items():
            with self.subTest(route_name=route_name):
                route = resolve(reverse(route_name))
                self.assertEqual(route.func.view_initkwargs["template_name"], template_name)
                self.assertIn("<!doctype html>", self._render(route_name).lower())

    def test_homepage_matches_family_first_waitlist_flow(self):
        content = self._render("marketing_home")

        self.assertIn("Unlock Reading. Unlock Everything.", content)
        self.assertIn(
            "ClearCode is K–8 structured literacy intervention built to close the gap: "
            "precise placement, expert specialists, and a live dashboard that shows you "
            "real progress, week by week.",
            content,
        )
        self.assertIn("Join Priority Waitlist", content)
        self.assertIn("See how it works", content)
        self.assertNotIn("Our Approach", content)
        self.assertIn("One connected reading path.", content)
        self.assertNotIn("Three steps.", content)
        self.assertIn("A straightforward process, built around your child.", content)
        self.assertIn("Precise Placement", content)
        self.assertIn(
            "Every child completes an initial assessment and is placed into one "
            "structured literacy sequence.",
            content,
        )
        self.assertIn("Specialist-Led Sessions", content)
        self.assertIn(
            "Reading specialists deliver lessons aligned to each program’s methodology",
            content,
        )
        self.assertIn("Progress you can follow", content)
        self.assertEqual(content.count('data-testid="homepage-approach-step"'), 3)
        self.assertIn('href="/survey/"', content)
        self.assertIn("Join Our Waitlist", content)
        self.assertNotIn("How ClearCode works", content)
        removed_sections = [
            'id="progress"',
            "See the story behind every session.",
            "Sample reader progress",
            "See an example family journey",
            "What the journey can look like",
            "A family story they can actually follow.",
        ]
        for text in removed_sections:
            with self.subTest(text=text):
                self.assertNotIn(text, content)
        self.assertNotIn("Frequently asked questions", content)
        self.assertIn("A little clarity can change the whole conversation.", content)
        self.assertEqual(content.count('data-testid="homepage-next-step-tile"'), 3)

    def test_homepage_hero_actions_follow_the_photo_without_tagline(self):
        content = self._render("marketing_home")
        hero_actions_start = content.index('data-testid="homepage-hero-actions"')
        hero_actions = content[hero_actions_start:content.index("</div>", hero_actions_start)]

        self.assertLess(
            content.index('/assets/images/specialist-reading-session.jpg'),
            hero_actions_start,
        )
        self.assertIn('href="/survey/"', hero_actions)
        self.assertIn("Join Priority Waitlist", hero_actions)
        self.assertIn('href="#how-it-works"', hero_actions)
        self.assertIn("See how it works", hero_actions)
        self.assertNotIn(
            "Opening in the Orlando metro area · Priority access available",
            content,
        )

    def test_homepage_omits_the_reading_team_path_section(self):
        content = self._render("marketing_home")

        self.assertNotIn("Start with the path that fits you.", content)
        self.assertNotIn("Know what your child is learning.", content)
        self.assertNotIn("Keep evidence and next steps connected.", content)

    def test_faq_has_its_own_public_page_and_is_not_a_homepage_section(self):
        homepage = self._render("marketing_home")
        faq = self._render("marketing_faq")

        self.assertNotIn("Frequently asked questions", homepage)
        self.assertIn("Frequently asked questions", faq)
        self.assertIn("What families usually want to know.", faq)
        self.assertEqual(faq.count('details class="group rounded-2xl'), 6)
        self.assertIn("Who does ClearCode serve?", faq)
        self.assertIn("How do we begin?", faq)

    def test_three_primary_next_steps_are_prominent_in_the_homepage_body(self):
        content = self._render("marketing_home")

        self.assertIn('data-testid="desktop-blog-link"', content)
        self.assertIn('data-testid="mobile-blog-link"', content)
        self.assertIn('<details class="relative lg:hidden">', content)
        self.assertIn('aria-labelledby="next-step-title"', content)
        self.assertIn("Choose your next step", content)
        self.assertIn("Read smarter. Support with confidence.", content)
        self.assertIn("Be first in line for focused support.", content)
        self.assertIn("Less guessing. More useful next steps.", content)
        self.assertIn('href="/blog/"', content)
        self.assertIn('href="/survey/"', content)
        self.assertIn('href="/resources/"', content)
        self.assertIn('data-testid="homepage-blog-cta">Explore the Blog</a>', content)
        self.assertIn('data-testid="homepage-waitlist-cta">Join the Priority Waitlist</a>', content)
        self.assertIn('data-testid="homepage-resources-cta">Free Family Resources</a>', content)
        self.assertNotIn("Beyond one family", content)
        self.assertNotIn("Start with a conversation", content)

    def test_homepage_consultation_ctas_use_local_intake(self):
        content = self._render("marketing_home")

        self.assertGreaterEqual(
            content.count('href="/contact/#consultation-form"'),
            2,
        )
        self.assertNotIn("waitlist.html", content)
        self.assertNotIn("docs.google.com", content)

    def test_homepage_omits_the_removed_reading_difficulty_section(self):
        content = self._render("marketing_home")

        removed_copy = [
            "When reading feels harder than it should",
            "You do not have to keep guessing.",
            "Reading gaps don’t close on their own.",
            "Homework becomes a nightly battle",
            "Reports do not explain the next step",
            "Support feels disconnected",
            "Florida Department of Education",
        ]
        for text in removed_copy:
            with self.subTest(text=text):
                self.assertNotIn(text, content)

    def test_homepage_uses_a_local_optimized_hero_photo(self):
        content = self._render("marketing_home")

        self.assertIn('/assets/images/specialist-reading-session.jpg', content)
        self.assertIn('fetchpriority="high"', content)
        hero_path = Path(settings.BASE_DIR) / "marketing-website/assets/images/specialist-reading-session.jpg"
        self.assertTrue(hero_path.is_file())
        self.assertLess(hero_path.stat().st_size, 500_000)
        self.assertIn("specialist-reading-session-640.webp 640w", content)
        self.assertIn("specialist-reading-session-1024.webp 1024w", content)
        image_root = hero_path.parent
        self.assertTrue((image_root / "specialist-reading-session-640.webp").is_file())
        self.assertTrue((image_root / "specialist-reading-session-1024.webp").is_file())

    def test_homepage_removes_learning_gallery_and_keeps_session_carousel_accessible(self):
        content = self._render("marketing_home")

        self.assertNotIn("Learning in motion", content)
        self.assertNotIn('aria-label="ClearCode learning experiences"', content)
        self.assertEqual(content.count('aria-roledescription="carousel"'), 1)
        self.assertEqual(content.count('data-carousel-slide role="group"'), 5)
        self.assertEqual(content.count('aria-roledescription="slide"'), 5)
        self.assertEqual(content.count('data-carousel-dot='), 5)
        self.assertIn('aria-label="Show previous session moment"', content)
        self.assertIn('aria-label="Show next session moment"', content)
        self.assertIn("A ClearCode session feels calm, focused, and connected.", content)
        self.assertIn("no more than three students", content)
        self.assertIn("event.key === 'ArrowLeft'", content)
        self.assertIn("event.key === 'ArrowRight'", content)
        self.assertNotIn("setInterval", content)
        self.assertIn("data-mobile-deep-dive", content)
        self.assertIn("Explore a full ClearCode session", content)

        image_root = Path(settings.BASE_DIR) / "marketing-website/assets/images"
        session_image_names = sorted(
            name
            for name in LEARNING_PHOTOS_BY_PAGE["marketing_home"]
            if name.startswith("session-carousel-")
        )
        for image_name in session_image_names:
            with self.subTest(image_name=image_name):
                self.assertLess((image_root / image_name).stat().st_size, 500_000)
                self.assertTrue((image_root / image_name.replace(".jpg", "-640.webp")).is_file())
                self.assertTrue((image_root / image_name.replace(".jpg", "-1024.webp")).is_file())

    def test_frontend_uses_compiled_tailwind_instead_of_the_play_cdn(self):
        template_paths = [
            "marketing-website/base_marketing.html",
            "marketing-website/assessment.html",
            "templates/registration/login.html",
            "templates/portal/dashboard.html",
            "templates/portal/inbox.html",
            "templates/sessions/rapid_log.html",
        ]

        for template_path in template_paths:
            content = Path(template_path).read_text()
            with self.subTest(template_path=template_path):
                self.assertIn("css/clearcode-tailwind.css", content)
                self.assertNotIn("cdn.tailwindcss.com", content)
                self.assertNotIn("tailwind.config =", content)

    def test_mobile_survey_context_appears_before_questions(self):
        content = self._render("reading_assessment")

        self.assertLess(content.index("education-side snapshot"), content.index('id="assessment-app"'))
        self.assertIn('aside class="order-1', content)

    def test_mobile_forms_and_footer_targets_avoid_small_controls(self):
        careers = self._render("marketing_careers")
        homepage = self._render("marketing_home")

        self.assertIn('font: 400 1rem/1.6', careers)
        self.assertIn('inline-flex min-h-11 items-center', homepage)

    def test_shared_marketing_navigation_is_consistent(self):
        route_names = [
            "marketing_home",
            "marketing_about",
            "marketing_how_it_works",
            "marketing_families",
            "marketing_resources",
            "marketing_faq",
            "marketing_foundation",
            "marketing_careers",
            "marketing_contact",
            "marketing_support",
            "marketing_privacy",
        ]
        expected_links = [
            "/about/",
            "/how-it-works/",
            "/families/",
            "/resources/",
            "/faq/",
            "/foundation/",
            "/blog/",
            "/careers/",
            "/privacy/",
            "/support/",
            "/contact/",
            "/survey/",
            "/login/",
        ]
        for route_name in route_names:
            content = self._render(route_name)
            for link in expected_links:
                with self.subTest(route_name=route_name, link=link):
                    self.assertIn(f'href="{link}"', content)

            with self.subTest(route_name=route_name, link="/approach/"):
                self.assertNotIn('href="/approach/"', content)

    def test_legacy_approach_route_redirects_to_combined_page(self):
        route = resolve(reverse("marketing_approach"))
        response = route.func(RequestFactory().get("/approach/"))

        self.assertEqual(
            route.func.view_initkwargs["pattern_name"],
            "marketing_how_it_works",
        )
        self.assertTrue(route.func.view_initkwargs["permanent"])
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("marketing_how_it_works"))

    def test_foundation_is_a_secondary_path_with_explicit_updates_opt_in(self):
        homepage = self._render("marketing_home")
        foundation = self._render("marketing_foundation")

        self.assertNotIn("Explore the Foundation", homepage)
        self.assertIn('href="/foundation/"', homepage)
        self.assertIn("Help more children find their way into reading.", foundation)
        self.assertIn('href="#newsletter-signup"', foundation)
        self.assertIn('name="consent"', foundation)

    def test_family_resources_page_offers_free_actionable_paths(self):
        content = self._render("marketing_resources", {"resources_unlocked": True})

        self.assertIn("Less guessing. More useful next steps.", content)
        self.assertIn("Choose the question you need answered today.", content)
        self.assertIn("Three moves for a calmer reading week.", content)
        self.assertIn("Take the Reading Inventory", content)
        self.assertIn("Bring Better Questions", content)
        self.assertIn("See the Full Pathway", content)
        self.assertIn('href="/assessment/"', content)
        self.assertIn('href="/faq/"', content)
        self.assertIn('href="/how-it-works/"', content)
        self.assertIn('href="/blog/"', content)
        self.assertIn('href="/survey/"', content)
        self.assertIn("not a diagnosis", content)

    def test_family_resources_page_has_an_accessible_name_and_email_gate(self):
        content = self._render("marketing_resources")

        self.assertIn('data-testid="family-resources-gate"', content)
        self.assertIn('aria-labelledby="resource-gate-title"', content)
        self.assertIn('action="/crm/signup/"', content)
        self.assertIn('name="name"', content)
        self.assertIn('autocomplete="name"', content)
        self.assertIn('name="email"', content)
        self.assertIn('autocomplete="email"', content)
        self.assertIn('name="redirect_to" value="/resources/"', content)
        self.assertIn('name="audience" value="parent"', content)
        self.assertIn("Unlock My Free Resources", content)
        self.assertNotIn("Three moves for a calmer reading week.", content)

    def test_contact_form_is_short_and_supports_audience_routing(self):
        content = self._render("marketing_contact")

        self.assertIn("Request a consultation", content)
        self.assertIn('name="audience"', content)
        self.assertIn('value="parent"', content)
        self.assertIn('value="school"', content)
        self.assertIn('value="teacher"', content)
        self.assertNotIn('name="child_age_grade"', content)
        self.assertNotIn('name="phone"', content)

    def test_privacy_notice_covers_mobile_student_data_and_user_choices(self):
        content = self._render("marketing_privacy")

        for heading in (
            "Scope and roles",
            "Information we handle",
            "How we use information",
            "When we disclose information",
            "Student and children’s privacy",
            "Security and retention",
            "Your privacy choices",
            "Changes and contact",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, content)
        self.assertIn("Effective August 30, 2026", content)
        self.assertIn("public website, authenticated portal, and iOS app", content)
        self.assertIn("does not sell personal or student information", content)
        self.assertIn("Children’s Online Privacy Protection Act", content)
        self.assertIn("Sensitive tax, banking, identity, and tax-form details", content)
        self.assertIn("device Keychain", content)
        self.assertIn('id="privacy-choices"', content)
        self.assertIn('href="/support/#support-form"', content)

    def test_support_page_routes_app_privacy_and_security_help(self):
        content = self._render("marketing_support")

        self.assertIn("Help for the app, portal, and your information.", content)
        self.assertIn('id="support-form"', content)
        self.assertIn('name="redirect_to" value="/support/"', content)
        self.assertIn('name="support_topic"', content)
        self.assertIn('value="app_access"', content)
        self.assertIn('value="data_request"', content)
        self.assertIn('value="security"', content)
        self.assertIn("Never send a password", content)
        self.assertIn('href="/privacy/#privacy-choices"', content)

    def test_public_pages_include_explicit_consent_newsletter_signup(self):
        for route_name in PUBLIC_PAGES:
            content = self._render(route_name)
            with self.subTest(route_name=route_name):
                self.assertIn('id="newsletter-signup"', content)
                self.assertIn('action="/newsletter/subscribe/"', content)
                self.assertIn('name="consent"', content)
                self.assertIn("I can unsubscribe at any time", content)

    def test_newsletter_confirmation_is_prominent_at_signup_on_shared_and_standalone_pages(self):
        confirmation = "You’re subscribed. Look for ClearCode Reading updates in your inbox."

        for route_name in ("marketing_home", "reading_assessment"):
            request = RequestFactory().get("/?newsletter=thanks")
            content = get_template(PUBLIC_PAGES[route_name]).render(
                {"messages": [Message(message_constants.SUCCESS, confirmation)]},
                request,
            )

            with self.subTest(route_name=route_name):
                self.assertEqual(content.count(confirmation), 1)
                self.assertLess(content.index('id="newsletter-signup"'), content.index(confirmation))
                self.assertIn('data-testid="newsletter-signup-feedback"', content)
                self.assertIn('role="status"', content)
                self.assertIn("Newsletter signup confirmed", content)
                self.assertIn("border-4 border-gold bg-ink", content)

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

    def test_assessment_contact_handoff_includes_structured_survey_fields(self):
        content = self._render("reading_assessment")

        for field_name in (
            "child_name",
            "child_age",
            "home_zip",
            "child_grade",
            "assessment_answers",
            "inventory_answers",
            "inventory_stopped_group",
        ):
            with self.subTest(field_name=field_name):
                self.assertIn(f'name="{field_name}"', content)

    def test_main_early_interest_survey_uses_the_crm_contract(self):
        content = self._render("early_interest_survey")

        self.assertIn('id="early-interest-survey"', content)
        self.assertIn('action="/crm/survey/"', content)
        self.assertIn('name="source_path" value="/survey/"', content)
        self.assertIn("Question 1 of 10", content)
        self.assertIn('name="email_consent"', content)
        self.assertIn('name="home_zip"', content)
        self.assertIn('name="respondent_situation"', content)
        self.assertIn('name="supports_tried"', content)
        self.assertIn('name="engagement_interests"', content)
        self.assertIn("We will never sell or share your information", content)

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

    def test_how_it_works_page_includes_the_full_family_pathway(self):
        content = self._render("marketing_how_it_works")

        expected_sections = [
            "The Assessment",
            "Precise Placement",
            "What a Session Looks Like",
            "How Progress Is Tracked",
            "The Same Faces, Every Session",
            "Trained Reading Specialists",
            "Ongoing support",
        ]
        for section in expected_sections:
            with self.subTest(section=section):
                self.assertIn(section, content)

        self.assertIn("Students per group, maximum", content)
        self.assertIn("ClearCode provides educational reading instruction", content)
        self.assertIn("ClearCode recommendations are explainable and human-controlled.", content)
        self.assertIn('href="/contact/"', content)
        self.assertNotIn('href="waitlist.html"', content)

    def test_public_pages_use_the_clearcode_brand_system(self):
        for route_name in PUBLIC_PAGES:
            content = self._render(route_name)
            with self.subTest(route_name=route_name):
                self.assertIn("Plus+Jakarta+Sans", content)
                self.assertEqual(
                    content.count("/assets/logo/cc-monogram-gold-teal.png"),
                    2,
                )
                self.assertNotIn("/assets/logo/cc-lockup-ink-ui.png", content)
                self.assertIn("/assets/logo/cc-favicon-gold-teal-32.png", content)
                self.assertIn("/assets/logo/cc-apple-touch-icon-gold-teal-180.png", content)
                self.assertNotIn("clear-code-reading-logo", content)
                self.assertNotIn("clear-code-reading-icon", content)
                self.assertNotIn("logo-plate", content)

        homepage = self._render("marketing_home")
        tailwind_config = Path(settings.BASE_DIR, "tailwind.config.js").read_text()
        for color in BRAND_COLORS:
            with self.subTest(color=color):
                self.assertIn(color, tailwind_config)
        self.assertIn("text-ink", homepage)
        self.assertIn("bg-linen", homepage)

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
                self.assertIn("Plus+Jakarta+Sans", content)
                self.assertIn("cc-favicon-gold-teal-32.png", content)
                self.assertIn("cc-apple-touch-icon-gold-teal-180.png", content)
                self.assertIn("css/clearcode-tailwind.css", content)
                self.assertIn("text-ink", content)
                self.assertIn("bg-linen", content)

    def test_portal_uses_a_readable_responsive_brand_lockup(self):
        brand_partial = (
            Path(settings.BASE_DIR) / "templates" / "portal" / "_brand.html"
        ).read_text()

        self.assertIn('data-testid="portal-brand-lockup"', brand_partial)
        self.assertIn("cc-monogram-gold-teal.png", brand_partial)
        self.assertIn("Clear", brand_partial)
        self.assertIn("Code", brand_partial)
        self.assertIn("Reading portal", brand_partial)
        self.assertNotIn("cc-lockup-", brand_partial)

        for relative_path in [
            "templates/portal/_header.html",
            "templates/registration/login.html",
        ]:
            content = (Path(settings.BASE_DIR) / relative_path).read_text()
            with self.subTest(template=relative_path):
                self.assertIn('{% include "portal/_brand.html" %}', content)
                self.assertNotIn("cc-lockup-", content)
                self.assertNotIn("logo-plate", content)

        rapid_log = (
            Path(settings.BASE_DIR) / "templates" / "sessions" / "rapid_log.html"
        ).read_text()
        self.assertIn('{% include "portal/_header.html" %}', rapid_log)
        self.assertNotIn("cc-lockup-", rapid_log)
        self.assertNotIn("logo-plate", rapid_log)

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

    def test_support_form_records_a_routed_request_without_creating_a_deal(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Taylor Specialist",
                "email": "taylor@example.com",
                "audience": Lead.Audience.TEACHER,
                "organization_name": "ClearCode support",
                "support_topic": "technical",
                "notes": "The session queue is not clearing after reconnecting.",
                "redirect_to": "/support/",
            },
        )

        self.assertRedirects(
            response,
            "/support/?signup=thanks#support-form",
            fetch_redirect_response=False,
        )
        lead = Lead.objects.get(contact_email="taylor@example.com")
        submission = FormSubmission.objects.get(lead=lead)
        self.assertEqual(
            lead.notes,
            "Support topic: Technical problem\nThe session queue is not clearing after reconnecting.",
        )
        self.assertEqual(submission.form_type, FormSubmission.FormType.WEBSITE)
        self.assertEqual(submission.source_path, "/support/")
        self.assertEqual(submission.submitted_data["support_topic"], "technical")
        self.assertFalse(lead.opportunities.exists())

    def test_support_form_normalizes_an_unknown_topic(self):
        self.client.post(
            reverse("crm_signup"),
            {
                "name": "Avery Guardian",
                "email": "avery@example.com",
                "audience": Lead.Audience.PARENT,
                "organization_name": "ClearCode support",
                "support_topic": "not-a-real-topic",
                "notes": "I need help with my account.",
                "redirect_to": "/support/",
            },
        )

        lead = Lead.objects.get(contact_email="avery@example.com")
        self.assertTrue(lead.notes.startswith("Support topic: Other support request\n"))
        submission = FormSubmission.objects.get(lead=lead)
        self.assertEqual(submission.submitted_data["support_topic"], "other")

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
