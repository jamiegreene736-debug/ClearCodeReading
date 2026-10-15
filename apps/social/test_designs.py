import base64
import json
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image

from apps.social.designs import starter, validate_document
from apps.social.exceptions import SocialError
from apps.social.models import SocialImageTemplate, SocialPost, SocialPublication
from apps.users.models import CustomUser


def png():
    output = BytesIO()
    Image.new("RGB", (1080, 1080), "teal").save(output, "PNG")
    return output.getvalue()


def upload():
    return SimpleUploadedFile("page.png", png(), "image/png")


class DesignValidationTests(SimpleTestCase):
    def test_starters_and_round_trip(self):
        for style in ("scripts", "tip", "blank"):
            doc = starter(style)
            self.assertEqual(validate_document(json.dumps(doc)), doc)

    def test_rejects_remote_backgrounds_and_invalid_geometry(self):
        for field, value in (
            ("x", float("nan")),
            ("size", -1),
            ("font", "remote-font"),
            ("text", "x" * 2001),
            ("color", "url(evil)"),
            ("kind", []),
        ):
            doc = starter()
            doc["pages"][0]["elements"][0][field] = value
            with self.assertRaises(SocialError):
                validate_document(json.dumps(doc))
        doc = starter()
        doc["pages"][0]["image"] = "https://example.com/remote.png"
        with self.assertRaises(SocialError):
            validate_document(json.dumps(doc))
        doc["height"] = []
        with self.assertRaises(SocialError):
            validate_document(json.dumps(doc))

    def test_transparent_background_keeps_alpha(self):
        output = BytesIO()
        Image.new("RGBA", (10, 10), (255, 0, 0, 0)).save(output, "PNG")
        doc = starter("blank")
        doc["pages"][0]["image"] = (
            "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        )
        cleaned = validate_document(json.dumps(doc))
        raw = base64.b64decode(cleaned["pages"][0]["image"].split(",", 1)[1])
        with Image.open(BytesIO(raw)) as image:
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_backgrounds_are_reencoded_and_markup_is_plain_text(self):
        doc = starter()
        doc["pages"][0]["image"] = (
            "data:image/png;base64," + base64.b64encode(png()).decode()
        )
        doc["pages"][0]["elements"][0]["text"] = "<script>alert(1)</script>"
        cleaned = validate_document(json.dumps(doc))
        self.assertTrue(
            cleaned["pages"][0]["image"].startswith("data:image/png;base64,")
        )
        self.assertEqual(
            cleaned["pages"][0]["elements"][0]["text"], "<script>alert(1)</script>"
        )


class DesignWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="designer", email="designer@example.com", is_superuser=True
        )
        cls.reader = CustomUser.objects.create_user(
            username="reader", email="reader@example.com"
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.document = starter("blank")

    def save(self, **extra):
        return self.client.post(
            reverse("social:design_save"),
            {
                "name": "Parent scripts",
                "document": json.dumps(self.document),
                "preview": upload(),
                **extra,
            },
        )

    def apply(self, post=None, **extra):
        data = {
            "document": json.dumps(self.document),
            "images": [upload() for _ in self.document["pages"]],
            "mode": "append",
            **extra,
        }
        if post:
            data.update(post_id=post.pk, revision=post.updated_at.isoformat())
        return self.client.post(reverse("social:design_apply"), data)

    def test_template_save_reopen_update_and_archive(self):
        result = self.save()
        self.assertEqual(result.status_code, 200)
        item = SocialImageTemplate.objects.get()
        self.assertEqual(item.document, self.document)
        response = self.client.get(
            reverse("social:design_editor"), {"template": item.pk}
        )
        self.assertEqual(response.context["studio"]["document"], self.document)
        self.assertEqual(
            self.client.get(reverse("social:design_preview", args=[item.pk]))[
                "Content-Type"
            ],
            "image/png",
        )
        self.assertEqual(
            self.save(template_id=item.pk, revision="stale").status_code, 400
        )
        self.assertEqual(
            self.save(
                template_id=item.pk,
                revision=item.updated_at.isoformat(),
                name="Updated",
            ).status_code,
            200,
        )
        item.refresh_from_db()
        self.assertEqual(item.name, "Updated")
        self.client.post(
            reverse("social:design_archive", args=[item.pk]),
            {"revision": item.updated_at.isoformat()},
        )
        self.assertEqual(
            self.client.get(
                reverse("social:design_preview", args=[item.pk])
            ).status_code,
            404,
        )

    def test_new_carousel_and_editable_source_are_saved_in_order(self):
        self.document = starter()
        result = self.apply()
        self.assertEqual(result.status_code, 200)
        post = SocialPost.objects.get()
        self.assertEqual(post.status, "draft")
        self.assertEqual(post.image_design, self.document)
        self.assertEqual(post.slides.count(), 1)
        self.assertTrue(post.has_image)
        response = self.client.get(reverse("social:post_design", args=[post.pk]))
        self.assertEqual(response.context["studio"]["document"], self.document)
        self.assertFalse(post.publications.exists())

    def test_append_preserves_cover_replace_is_explicit_and_stale_write_rejected(self):
        post = SocialPost.objects.create(
            created_by=self.admin,
            image_data=png(),
            image_name="old.png",
            image_content_type="image/png",
            facebook_caption="Keep this caption",
        )
        self.assertEqual(self.apply(post).status_code, 200)
        self.assertEqual(self.apply(post).status_code, 400)
        post.refresh_from_db()
        self.assertEqual(post.slides.count(), 1)
        self.assertEqual(post.facebook_caption, "Keep this caption")
        self.assertEqual(self.apply(post, mode="replace").status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.slides.count(), 0)

    def test_sending_scheduled_and_partially_published_posts_are_protected(self):
        for state in ("publishing", "posted", "scheduled"):
            post = SocialPost.objects.create(status=state)
            self.assertEqual(self.apply(post).status_code, 400)
            post.refresh_from_db()
            self.assertFalse(post.has_image)
        post = SocialPost.objects.create(status="attention")
        SocialPublication.objects.create(
            post=post, network="facebook", status="published"
        )
        self.assertEqual(self.apply(post).status_code, 400)

    def test_missing_exports_and_invalid_images_do_not_create_posts(self):
        self.assertEqual(
            self.client.post(
                reverse("social:design_apply"), {"document": json.dumps(self.document)}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                reverse("social:design_apply"),
                {
                    "document": json.dumps(self.document),
                    "images": SimpleUploadedFile("bad.png", b"not an image"),
                },
            ).status_code,
            400,
        )
        self.assertFalse(SocialPost.objects.exists())

    def test_permissions_and_csrf(self):
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.admin)
        self.assertEqual(secure.post(reverse("social:design_apply")).status_code, 403)
        self.client.force_login(self.reader)
        for name in ("design_library", "design_editor"):
            self.assertEqual(
                self.client.get(reverse("social:" + name)).status_code, 403
            )
        self.assertEqual(self.save().status_code, 403)
        self.assertEqual(self.apply().status_code, 403)
