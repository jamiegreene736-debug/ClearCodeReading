from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from whitenoise.middleware import WhiteNoiseMiddleware


class StaticAssetDeploymentTests(SimpleTestCase):
    def test_collected_admin_css_is_served_by_whitenoise(self):
        with TemporaryDirectory() as static_root:
            with override_settings(STATIC_ROOT=static_root):
                call_command("collectstatic", interactive=False, verbosity=0)
                middleware = WhiteNoiseMiddleware(lambda request: HttpResponse(status=404))
                response = middleware(RequestFactory().get("/static/admin/css/base.css"))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], 'text/css; charset="utf-8"')
                self.assertTrue(Path(static_root, "admin/css/base.css").is_file())

    def test_container_image_collects_static_files_after_copying_source(self):
        dockerfile = Path("Dockerfile").read_text()

        copy_position = dockerfile.index("COPY . .")
        collect_position = dockerfile.index("RUN python manage.py collectstatic --noinput")
        entrypoint_position = dockerfile.index('ENTRYPOINT ["/app/scripts/entrypoint.sh"]')

        self.assertLess(copy_position, collect_position)
        self.assertLess(collect_position, entrypoint_position)
