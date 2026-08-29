from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
from django.urls import reverse
from django.utils.crypto import get_random_string
from whitenoise.middleware import WhiteNoiseMiddleware

from apps.core.templatetags.admin_navigation import admin_navigation_groups
from clearcodereading import settings


class StaticAssetDeploymentTests(SimpleTestCase):
    def test_public_domain_is_allowed_and_csrf_trusted(self):
        self.assertIn("clearcodereading.com", settings.ALLOWED_HOSTS)
        self.assertIn("www.clearcodereading.com", settings.ALLOWED_HOSTS)
        self.assertIn("https://clearcodereading.com", settings.CSRF_TRUSTED_ORIGINS)
        self.assertIn("https://www.clearcodereading.com", settings.CSRF_TRUSTED_ORIGINS)

    def test_collected_admin_css_is_served_by_whitenoise(self):
        with TemporaryDirectory() as static_root:
            with override_settings(STATIC_ROOT=static_root):
                call_command("collectstatic", interactive=False, verbosity=0)
                middleware = WhiteNoiseMiddleware(lambda request: HttpResponse(status=404))
                response = middleware(RequestFactory().get("/static/admin/css/base.css"))
                brand_response = middleware(
                    RequestFactory().get("/static/admin/css/clearcode_admin.css")
                )
                frontend_response = middleware(
                    RequestFactory().get("/static/css/clearcode-tailwind.css")
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], 'text/css; charset="utf-8"')
                self.assertEqual(brand_response.status_code, 200)
                self.assertEqual(frontend_response.status_code, 200)
                self.assertEqual(
                    brand_response["Content-Type"],
                    'text/css; charset="utf-8"',
                )
                self.assertTrue(Path(static_root, "admin/css/base.css").is_file())
                self.assertTrue(
                    Path(static_root, "admin/css/clearcode_admin.css").is_file()
                )
                self.assertTrue(Path(static_root, "css/clearcode-tailwind.css").is_file())

    def test_container_image_collects_static_files_after_copying_source(self):
        dockerfile = Path("Dockerfile").read_text()

        copy_position = dockerfile.index("COPY . .")
        css_build_position = dockerfile.index("RUN npm run build:css")
        collect_position = dockerfile.index("RUN python manage.py collectstatic --noinput")
        entrypoint_position = dockerfile.index('ENTRYPOINT ["/app/scripts/entrypoint.sh"]')

        self.assertLess(css_build_position, collect_position)
        self.assertLess(copy_position, collect_position)
        self.assertLess(collect_position, entrypoint_position)

    def test_admin_styles_include_mobile_reflow_and_zoom_guards(self):
        css = Path("apps/core/static/admin/css/clearcode_admin.css").read_text()

        self.assertIn("@media (max-width: 1024px)", css)
        self.assertIn(".dashboard #content-related", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("font-size: 16px", css)
        self.assertIn(".clearcode-admin-nav__panel", css)
        self.assertIn(".clearcode-admin-model-grid", css)
        self.assertIn("flex-shrink: 0", css)
        self.assertIn(".clearcode-admin-nav__models > li", css)
        self.assertIn("list-style: none", css)


class LoginCsrfRecoveryTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def _stale_token(self) -> str:
        self.client.get(reverse("login"))
        stale_token = self.client.cookies["csrftoken"].value
        self.client.cookies["csrftoken"] = get_random_string(32)
        return stale_token

    def test_stale_portal_login_redirects_to_a_fresh_form(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "admin@example.com",
                "password": "not-persisted",
                "csrfmiddlewaretoken": self._stale_token(),
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?csrf=refreshed",
            fetch_redirect_response=False,
        )
        refreshed = self.client.get(response.url)
        self.assertContains(refreshed, "Your sign-in form had expired.")

    def test_stale_demo_login_uses_the_same_safe_recovery(self):
        response = self.client.post(
            reverse("demo_login", args=("admin",)),
            {"csrfmiddlewaretoken": self._stale_token()},
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?csrf=refreshed",
            fetch_redirect_response=False,
        )

    def test_stale_django_admin_login_preserves_a_local_destination(self):
        response = self.client.post(
            reverse("admin:login"),
            {
                "next": "/admin/users/customuser/",
                "csrfmiddlewaretoken": self._stale_token(),
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?csrf=refreshed&next=%2Fadmin%2Fusers%2Fcustomuser%2F",
            fetch_redirect_response=False,
        )

    def test_stale_login_drops_an_external_destination(self):
        response = self.client.post(
            reverse("login"),
            {
                "next": "https://attacker.example/steal",
                "csrfmiddlewaretoken": self._stale_token(),
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?csrf=refreshed",
            fetch_redirect_response=False,
        )

    def test_non_login_csrf_failures_remain_forbidden(self):
        response = self.client.post(
            reverse("logout"),
            {"csrfmiddlewaretoken": self._stale_token()},
        )

        self.assertEqual(response.status_code, 403)


class AdminBrandingTests(SimpleTestCase):
    def test_admin_site_uses_clearcode_portal_name(self):
        self.assertEqual(admin.site.site_header, "ClearCode Reading Admin Portal")
        self.assertEqual(admin.site.site_title, "ClearCode Reading Admin Portal")
        self.assertEqual(admin.site.index_title, "Admin Portal")
        self.assertFalse(admin.site.enable_nav_sidebar)
        self.assertEqual(admin.site.index_template, "admin/clearcode_index.html")
        self.assertEqual(
            admin.site.app_index_template,
            "admin/clearcode_app_index.html",
        )

    def test_admin_base_template_reuses_homepage_branding(self):
        request = RequestFactory().get("/admin/")
        request.user = AnonymousUser()
        context = admin.site.each_context(request)
        context.update(
            {
                "has_permission": False,
                "is_nav_sidebar_enabled": False,
                "is_popup": False,
                "subtitle": None,
                "title": admin.site.index_title,
            }
        )

        html = render_to_string("admin/base_site.html", context, request=request)

        self.assertIn("ClearCode Reading Admin Portal", html)
        self.assertIn("Admin Portal", html)
        self.assertIn('/assets/logo/cc-monogram-gold-teal.png', html)
        self.assertIn('/static/admin/css/clearcode_admin.css', html)
        self.assertIn('href="/admin/"', html)
        self.assertNotIn("Django administration", html)

    def test_admin_navigation_uses_permission_filtered_horizontal_groups(self):
        request = RequestFactory().get("/admin/workforce/")
        request.user = SimpleNamespace(
            is_active=True,
            is_staff=True,
            is_authenticated=True,
            get_short_name=lambda: "Demo",
            get_username=lambda: "demo@example.com",
            has_usable_password=lambda: True,
        )
        context = {
            "app_label": "workforce",
            "available_apps": [
                {
                    "app_label": "workforce",
                    "app_url": "/admin/workforce/",
                    "name": "Workforce",
                    "models": [
                        {
                            "admin_url": "/admin/workforce/paymentrun/",
                            "name": "Payment runs",
                        }
                    ],
                }
            ],
            "has_permission": True,
            "is_nav_sidebar_enabled": False,
            "is_popup": False,
            "opts": SimpleNamespace(app_label="workforce"),
            "site_header": admin.site.site_header,
            "site_title": admin.site.site_title,
            "site_url": "/",
            "subtitle": None,
            "title": "Workforce administration",
            "user": request.user,
        }

        html = render_to_string("admin/base_site.html", context, request=request)

        self.assertIn('aria-label="Admin portal navigation"', html)
        self.assertIn("Scheduling &amp; payments", html)
        self.assertIn("Workforce overview", html)
        self.assertIn('/admin/workforce/paymentrun/', html)
        self.assertIn('href="/dashboard/"', html)
        self.assertIn('href="/crm/"', html)
        self.assertNotIn("People &amp; access", html)
        self.assertNotIn('role="menu"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('aria-controls="admin-nav-operations"', html)
        self.assertIn('class="clearcode-admin-nav__model-label"', html)
        self.assertIn('class="clearcode-admin-nav__model-arrow"', html)
        self.assertIn('event.key !== "Escape"', html)

    def test_admin_app_list_uses_responsive_model_cards(self):
        request = RequestFactory().get("/admin/workforce/")
        html = render_to_string(
            "admin/_clearcode_app_list.html",
            {
                "app_list": [
                    {
                        "app_label": "workforce",
                        "app_url": "/admin/workforce/",
                        "name": "Workforce",
                        "models": [
                            {
                                "add_url": "/admin/workforce/paymentrun/add/",
                                "admin_url": "/admin/workforce/paymentrun/",
                                "name": "Payment runs",
                                "object_name": "PaymentRun",
                                "view_only": False,
                            }
                        ],
                    }
                ],
                "request": request,
                "show_changelinks": True,
            },
            request=request,
        )

        self.assertIn('class="clearcode-admin-model-grid"', html)
        self.assertIn("Payment runs", html)
        self.assertIn("Manage", html)
        self.assertNotIn("<table", html)


class AdminNavigationGroupingTests(SimpleTestCase):
    def test_groups_known_apps_and_keeps_unknown_apps_available(self):
        groups = admin_navigation_groups(
            [
                {"app_label": "assessments", "models": [{"name": "Assessments"}]},
                {"app_label": "workforce", "models": [{"name": "Payments"}]},
                {"app_label": "custom_tools", "models": [{"name": "Imports"}]},
            ]
        )

        self.assertEqual(
            [group["label"] for group in groups],
            ["Readers & teaching", "Scheduling & payments", "System"],
        )
        self.assertEqual(groups[1]["model_count"], 1)
        self.assertEqual(groups[2]["apps"][0]["app_label"], "custom_tools")
