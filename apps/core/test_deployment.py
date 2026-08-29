from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings
from whitenoise.middleware import WhiteNoiseMiddleware

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


class AdminBrandingTests(SimpleTestCase):
    def test_admin_site_uses_clearcode_portal_name(self):
        self.assertEqual(admin.site.site_header, "ClearCode Reading Admin Portal")
        self.assertEqual(admin.site.site_title, "ClearCode Reading Admin Portal")
        self.assertEqual(admin.site.index_title, "Admin Portal")

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
