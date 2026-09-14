from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.crm.views import FAMILY_RESOURCES_SESSION_KEY
from apps.resources.forms import video_embed
from apps.resources.models import Asset, Resource, Revision, Topic, UploadBatch
from apps.resources.services import bulk_upload, create_resource, publish
from apps.resources.uploads import validated_upload
from apps.resources.views import published_revisions
from apps.users.models import CustomUser


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ResourceWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.publisher = CustomUser.objects.create_user(
            username="publisher", email="publisher@example.com", is_superuser=True
        )
        cls.author = CustomUser.objects.create_user(
            username="author", email="author@example.com", is_staff=True
        )
        cls.other = CustomUser.objects.create_user(
            username="other", email="other@example.com", is_staff=True
        )
        cls.parent = CustomUser.objects.create_user(
            username="parent", email="parent@example.com"
        )
        cls.topic = Topic.objects.create(name="Testing resources")

    def setUp(self):
        self.client.force_login(self.publisher)
        self.resource = create_resource(
            self.publisher,
            kind="article",
            title="Reading together",
            description="A simple family guide.",
            body="Original article.",
            topic=self.topic,
        )

    def post_edit(self, resource=None, **overrides):
        resource = resource or self.resource
        resource.refresh_from_db()
        revision = resource.draft
        data = {
            "version": resource.version,
            "title": revision.title,
            "description": revision.description,
            "kind": revision.kind,
            "audience": revision.audience,
            "topic": revision.topic_id or "",
            "body": revision.body,
            "url": revision.url,
            "language": revision.language,
            "access": revision.access,
            "asset": str(revision.asset_id or ""),
            "cover": str(revision.cover_id or ""),
            "cover_alt": revision.cover_alt,
            "autosave": "1",
        }
        data.update(overrides)
        return self.client.post(reverse("resources:edit", args=[resource.pk]), data)

    def action(self, operation, resource=None, **values):
        resource = resource or self.resource
        resource.refresh_from_db()
        return self.client.post(
            reverse("resources:action", args=[resource.pk]),
            {"action": operation, "version": resource.version, **values},
        )

    def unlock(self):
        session = self.client.session
        session[FAMILY_RESOURCES_SESSION_KEY] = True
        session.save()

    def test_editor_manager_and_add_render(self):
        for route in ("manager", "add", "media"):
            self.assertEqual(
                self.client.get(reverse(f"resources:{route}")).status_code, 200
            )
        response = self.client.get(reverse("resources:edit", args=[self.resource.pk]))
        self.assertContains(response, "Preview resource")
        self.assertContains(response, "Publish now")
        self.assertContains(response, "Schedule publication")

    def test_save_creates_revision_and_leaves_live_content_unchanged(self):
        publish(self.resource, self.publisher)
        live_id = self.resource.live_id
        self.assertEqual(self.post_edit(body="Updated draft.").status_code, 200)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.live_id, live_id)
        self.assertNotEqual(self.resource.draft_id, live_id)
        self.unlock()
        response = self.client.get(self.resource.get_absolute_url())
        self.assertContains(response, "Original article.")
        self.assertNotContains(response, "Updated draft.")
        self.action("publish")
        self.assertContains(
            self.client.get(self.resource.get_absolute_url()), "Updated draft."
        )

    def test_noop_autosave_does_not_create_revision(self):
        original = self.resource.draft_id
        response = self.post_edit()
        self.assertEqual(response.status_code, 200, response.content)
        self.resource.refresh_from_db()
        self.assertEqual(original, self.resource.draft_id)

    def test_stale_save_and_publish_are_rejected(self):
        version = self.resource.version
        self.post_edit(title="New title")
        self.assertEqual(
            self.post_edit(title="Stale title", version=version).status_code, 409
        )
        self.action("publish", version=version)
        self.resource.refresh_from_db()
        self.assertIsNone(self.resource.live_id)
        self.assertEqual(self.resource.draft.title, "New title")

    def test_contributor_sees_only_own_resources_and_cannot_publish(self):
        owned = create_resource(
            self.author,
            kind="article",
            title="Author guide",
            description="Summary",
            body="Body",
            topic=self.topic,
        )
        self.client.force_login(self.author)
        self.assertNotContains(
            self.client.get(reverse("resources:manager")), "Reading together"
        )
        self.assertEqual(
            self.client.get(
                reverse("resources:edit", args=[self.resource.pk])
            ).status_code,
            404,
        )
        self.assertEqual(self.action("publish", owned).status_code, 403)
        self.assertEqual(
            self.post_edit(owned, access="public", featured="on").status_code, 200
        )
        owned.refresh_from_db()
        self.assertEqual(owned.draft.access, "family")
        self.assertFalse(owned.draft.featured)
        self.action("submit", owned)
        owned.refresh_from_db()
        self.assertTrue(owned.submitted)
        self.client.force_login(self.publisher)
        self.assertContains(
            self.client.get(reverse("resources:manager") + "?tab=review"),
            "Author guide",
        )
        self.action("publish", owned)
        owned.refresh_from_db()
        self.assertIsNotNone(owned.live_id)
        self.assertFalse(owned.submitted)

    def test_explicit_publisher_permission_grants_review_access(self):
        self.author.user_permissions.add(
            Permission.objects.get(codename="publish_resource")
        )
        self.client.force_login(self.author)
        self.assertEqual(self.action("publish").status_code, 302)
        self.resource.refresh_from_db()
        self.assertIsNotNone(self.resource.live_id)

    def test_parent_and_anonymous_cannot_edit(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(reverse("resources:manager")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("resources:manager")).status_code, 302)

    def test_publication_requires_complete_content(self):
        self.post_edit(description="", topic="", body="")
        self.action("publish")
        self.resource.refresh_from_db()
        self.assertIsNone(self.resource.live_id)
        self.assertContains(
            self.client.get(reverse("resources:edit", args=[self.resource.pk])),
            "Add a short description",
        )

    def test_drafts_and_archived_resources_are_not_public(self):
        self.assertEqual(
            self.client.get(self.resource.get_absolute_url()).status_code, 404
        )
        self.action("publish")
        self.action("archive")
        self.assertFalse(published_revisions().filter(resource=self.resource).exists())
        self.assertEqual(
            self.client.get(self.resource.get_absolute_url()).status_code, 404
        )
        self.action("restore")
        self.unlock()
        self.assertEqual(
            self.client.get(self.resource.get_absolute_url()).status_code, 200
        )

    def test_restore_revision_and_duplicate_never_publish(self):
        original = self.resource.draft_id
        self.action("publish")
        self.post_edit(body="Second version")
        self.action("restore_revision", revision=original)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.draft.body, "Original article.")
        self.assertEqual(self.resource.live_id, original)
        self.action("duplicate")
        copy = Resource.objects.exclude(pk=self.resource.pk).get()
        self.assertIsNone(copy.live_id)
        self.assertIsNone(copy.scheduled_id)
        self.assertNotEqual(copy.get_absolute_url(), self.resource.get_absolute_url())

    def test_schedule_freezes_revision_and_becomes_visible_without_worker(self):
        now = timezone.now()
        self.action("schedule", publish_at=(now + timedelta(hours=1)).isoformat())
        self.resource.refresh_from_db()
        scheduled_id = self.resource.scheduled_id
        self.post_edit(body="Later edits should remain private")
        self.assertFalse(published_revisions().exists())
        with patch("django.utils.timezone.now", return_value=now + timedelta(hours=2)):
            self.assertEqual(published_revisions().get().pk, scheduled_id)
            self.unlock()
            self.assertNotContains(
                self.client.get(self.resource.get_absolute_url()), "Later edits"
            )

    def test_rescheduling_after_due_preserves_visible_revision(self):
        now = timezone.now()
        self.action("schedule", publish_at=(now + timedelta(hours=1)).isoformat())
        self.post_edit(body="Next release")
        with patch("django.utils.timezone.now", return_value=now + timedelta(hours=2)):
            self.action("schedule", publish_at=(now + timedelta(hours=3)).isoformat())
            self.assertEqual(published_revisions().get().body, "Original article.")
            self.action("cancel_schedule")
            self.assertEqual(published_revisions().get().body, "Original article.")

    def test_reject_past_schedule(self):
        self.action(
            "schedule", publish_at=(timezone.now() - timedelta(days=1)).isoformat()
        )
        self.resource.refresh_from_db()
        self.assertIsNone(self.resource.scheduled_id)

    def test_family_gate_applies_to_detail_and_direct_asset(self):
        self.post_edit(
            kind="file", upload=SimpleUploadedFile("guide.txt", b"Private file")
        )
        self.action("publish")
        self.client.logout()
        asset_url = reverse("resources:asset", args=[self.resource.pk, "file"])
        self.assertRedirects(self.client.get(asset_url), "/resources/")
        self.assertRedirects(
            self.client.get(self.resource.get_absolute_url()), "/resources/"
        )
        self.unlock()
        response = self.client.get(asset_url)
        self.assertEqual(b"".join(response.streaming_content), b"Private file")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_public_resource_requires_no_signup(self):
        self.post_edit(access="public")
        self.action("publish")
        self.client.logout()
        self.assertContains(
            self.client.get(self.resource.get_absolute_url()), "Original article."
        )

    def test_file_replacement_keeps_url_and_old_file_until_publish(self):
        self.post_edit(kind="file", upload=SimpleUploadedFile("first.txt", b"First"))
        self.action("publish")
        url = self.resource.get_absolute_url()
        self.post_edit(
            kind="file", upload=SimpleUploadedFile("replacement.txt", b"Second")
        )
        self.unlock()
        asset_url = reverse("resources:asset", args=[self.resource.pk, "file"])
        self.assertEqual(
            b"".join(self.client.get(asset_url).streaming_content), b"First"
        )
        self.action("publish")
        self.assertEqual(
            b"".join(self.client.get(asset_url).streaming_content), b"Second"
        )
        self.resource.refresh_from_db()
        self.assertEqual(url, self.resource.get_absolute_url())
        self.assertEqual(Asset.objects.count(), 2)

    def test_bulk_upload_is_atomic_and_idempotent(self):
        token = uuid4()

        def files():
            return [
                SimpleUploadedFile("a.txt", b"A"),
                SimpleUploadedFile("b.txt", b"B"),
            ]

        resources = bulk_upload(self.publisher, files(), token, self.topic, "families")
        again = bulk_upload(self.publisher, files(), token, self.topic, "families")
        self.assertEqual(
            {resource.pk for resource in resources}, {resource.pk for resource in again}
        )
        self.assertEqual(len(resources), 2)
        with self.assertRaises(ValidationError):
            bulk_upload(
                self.publisher,
                [
                    SimpleUploadedFile("valid.txt", b"C"),
                    SimpleUploadedFile("bad.pdf", b"broken"),
                ],
                uuid4(),
                self.topic,
                "families",
            )
        self.assertEqual(Asset.objects.count(), 2)
        self.assertEqual(UploadBatch.objects.count(), 1)

    def test_bulk_limits_and_owner_boundary(self):
        token = uuid4()
        bulk_upload(
            self.publisher,
            [SimpleUploadedFile("a.txt", b"A")],
            token,
            self.topic,
            "families",
        )
        with self.assertRaises(ValidationError):
            bulk_upload(
                self.author,
                [SimpleUploadedFile("a.txt", b"A")],
                token,
                self.topic,
                "families",
            )
        with self.assertRaises(ValidationError):
            bulk_upload(
                self.publisher,
                [SimpleUploadedFile("a.txt", b"A") for _ in range(11)],
                uuid4(),
                self.topic,
                "families",
            )

    def test_upload_endpoint_suggests_title(self):
        response = self.client.post(
            reverse("resources:add"),
            {
                "kind": "file",
                "token": uuid4(),
                "files": SimpleUploadedFile("Family_Reading_Guide.txt", b"Example"),
                "audience": "families",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Revision.objects.filter(title="Family Reading Guide").exists())

    def test_reusing_other_authors_asset_is_rejected(self):
        self.post_edit(
            kind="file", upload=SimpleUploadedFile("private.txt", b"Private")
        )
        self.resource.refresh_from_db()
        owned = create_resource(self.author, kind="file")
        self.client.force_login(self.author)
        self.assertEqual(
            self.post_edit(owned, asset=str(self.resource.draft.asset_id)).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                reverse("resources:preview_asset", args=[self.resource.pk, "file"])
            ).status_code,
            404,
        )

    def test_safe_urls_and_escaped_articles(self):
        for url in [
            "javascript:alert(1)",
            "http://example.com",
            "https://127.0.0.1",
            "https://user:password@example.com",
            "https://example.com:invalid",
        ]:
            self.assertEqual(self.post_edit(kind="link", url=url).status_code, 400, url)
        self.post_edit(body="<script>alert('x')</script>", access="public")
        self.action("publish")
        response = self.client.get(self.resource.get_absolute_url())
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert")
        self.assertEqual(
            video_embed("https://youtu.be/dQw4w9WgXcQ"),
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        )
        self.assertEqual(
            video_embed("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ"), ""
        )

    def test_search_filters_and_unpublished_text_is_not_discoverable(self):
        self.action("publish")
        self.post_edit(title="Secret draft title")
        self.assertContains(
            self.client.get("/resources/?q=Reading+together"), "Reading together"
        )
        self.assertNotContains(
            self.client.get("/resources/?q=Secret"), "Secret draft title"
        )
        self.assertNotContains(
            self.client.get("/resources/?audience=educators"), "Reading together"
        )

    def test_preview_shows_card_and_page_without_publishing(self):
        response = self.client.get(
            reverse("resources:preview", args=[self.resource.pk])
        )
        self.assertContains(response, "Resource card")
        self.assertContains(response, "Full resource page")
        self.assertContains(response, "Original article.")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(
            self.client.get(self.resource.get_absolute_url()).status_code, 404
        )

    def test_csrf_is_required_for_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.publisher)
        self.assertEqual(
            client.post(
                reverse("resources:action", args=[self.resource.pk]),
                {"action": "publish"},
            ).status_code,
            403,
        )

    def test_corrupt_and_oversized_uploads_are_rejected(self):
        for name, data in [
            ("bad.pdf", b"not pdf"),
            ("bad.png", b"not png"),
            ("bad.docx", b"not office"),
            ("bad.txt", b"\x00"),
            ("code.html", b"<script>"),
            ("huge.txt", b"A" * (10 * 1024 * 1024 + 1)),
        ]:
            with self.assertRaises(ValidationError):
                validated_upload(SimpleUploadedFile(name, data))

    def test_image_upload_is_normalized_and_cover_needs_description(self):
        image = BytesIO()
        Image.new("RGB", (20, 20)).save(image, "PNG")
        response = self.post_edit(
            cover_upload=SimpleUploadedFile("cover.png", image.getvalue())
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.action("publish")
        self.resource.refresh_from_db()
        self.assertIsNone(self.resource.live_id)
        self.assertEqual(self.resource.draft.cover.content_type, "image/jpeg")
        self.post_edit(cover_alt="An example cover")
        self.action("publish")
        self.resource.refresh_from_db()
        self.assertIsNotNone(self.resource.live_id)

    def test_contributor_can_keep_file_replaced_by_publisher(self):
        owned = create_resource(
            self.author,
            kind="file",
            title="Author worksheet",
            description="Summary",
            topic=self.topic,
        )
        self.post_edit(
            owned, upload=SimpleUploadedFile("reviewed.txt", b"Reviewed file")
        )
        self.client.force_login(self.author)
        response = self.post_edit(owned, description="More helpful summary")
        self.assertEqual(response.status_code, 200, response.content)
        owned.refresh_from_db()
        self.assertEqual(owned.draft.asset.name, "reviewed.txt")

    def test_invalid_revision_identifier_does_not_crash(self):
        response = self.action("restore_revision", revision="not-a-number")
        self.assertEqual(response.status_code, 302)
        self.assertContains(
            self.client.get(reverse("resources:edit", args=[self.resource.pk])),
            "Choose a valid version",
        )

    def test_staff_resources_link_is_visible_in_portal_header(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        request = RequestFactory().get("/dashboard/")
        request.user = self.author
        html = render_to_string("portal/_header.html", {"request": request})
        self.assertIn("/resources/manage/", html)

    def test_tenant_schema_cannot_use_public_resource_routes(self):
        from django.db import connection
        from django.http import Http404
        from django.test import RequestFactory

        from apps.resources.views import manager

        request = RequestFactory().get("/resources/manage/")
        request.user = self.publisher
        with (
            patch.object(connection, "schema_name", "school_private"),
            self.assertRaises(Http404),
        ):
            manager(request)

    def test_file_library_queries_do_not_select_asset_bytes(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.post_edit(kind="file", upload=SimpleUploadedFile("data.txt", b"Data"))
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(
                self.client.get(reverse("resources:media")).status_code, 200
            )
        self.assertFalse(
            any(
                '"resources_asset"."data"' in query["sql"]
                for query in captured.captured_queries
            )
        )

    def test_successful_family_signup_returns_to_requested_resource(self):
        self.action("publish")
        self.client.logout()
        self.client.get(self.resource.get_absolute_url())
        response = self.client.post(
            "/crm/signup/",
            {
                "redirect_to": "/resources/",
                "name": "Test family",
                "email": "family-new@example.com",
                "audience": "parent",
            },
        )
        self.assertRedirects(response, self.resource.get_absolute_url())
        self.assertTrue(self.client.session[FAMILY_RESOURCES_SESSION_KEY])

    def test_static_assets_are_packaged_with_the_app(self):
        from django.contrib.staticfiles.finders import find

        for asset in (
            "resources/editor.js",
            "resources/upload.js",
            "resources/resources.css",
        ):
            self.assertIsNotNone(find(asset), asset)

    def test_public_demo_identity_cannot_manage_resources_even_with_password_login(
        self,
    ):
        demo = CustomUser.objects.create_user(
            username="demo-admin", email="admin@clearcodereading.com", is_superuser=True
        )
        self.client.force_login(demo)
        self.assertEqual(self.client.get(reverse("resources:manager")).status_code, 403)
        self.assertEqual(self.action("publish").status_code, 403)

    def test_demo_origin_and_legacy_sessions_require_real_staff_signin(self):
        session = self.client.session
        session.pop("resources_staff_login", None)
        session.save()
        response = self.client.get(reverse("resources:manager"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("staff-sign-in", response.url)
        self.assertContains(
            self.client.get(reverse("resources:sign_in")), "Staff sign-in"
        )
        from django.test import RequestFactory

        from apps.resources.signals import mark_login_source

        request = RequestFactory().post("/demo-login/admin/")
        request.session = {}
        mark_login_source(None, request, self.publisher)
        self.assertFalse(request.session["resources_staff_login"])

    def test_pdf_preview_is_rendered_and_protected(self):
        output = BytesIO()
        Image.new("RGB", (120, 160), "white").save(output, "PDF")
        response = self.post_edit(
            kind="file", upload=SimpleUploadedFile("worksheet.pdf", output.getvalue())
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.resource.refresh_from_db()
        self.assertIsNotNone(self.resource.draft.asset.preview_id)
        self.action("publish")
        self.client.logout()
        url = (
            reverse("resources:asset", args=[self.resource.pk, "file"]) + "?thumbnail=1"
        )
        self.assertEqual(self.client.get(url).status_code, 302)
        self.unlock()
        response = self.client.get(url)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\xff\xd8"))
        self.assertContains(
            self.client.get(self.resource.get_absolute_url()), "First-page preview"
        )

    def test_pdf_timeout_leaves_no_asset_or_draft_changes(self):
        import subprocess

        original = self.resource.draft_id
        with patch(
            "apps.resources.uploads.subprocess.run",
            side_effect=subprocess.TimeoutExpired("renderer", 8),
        ):
            response = self.post_edit(
                kind="file",
                upload=SimpleUploadedFile("timeout.pdf", b"%PDF-1.4\n%%EOF"),
            )
        self.assertEqual(response.status_code, 400)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.draft_id, original)
        self.assertEqual(Asset.objects.count(), 0)

    def test_private_setup_token_creates_only_resource_publisher_and_is_one_use(self):
        import hashlib

        from apps.resources.models import StaffSetupToken

        token = "local-test-token"
        StaffSetupToken.objects.create(
            digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.logout()
        url = reverse("resources:setup_staff", args=[token])
        self.assertContains(self.client.get(url), "Your content starts here")
        response = self.client.post(
            url,
            {
                "email": "new-editor@example.com",
                "first_name": "Jamie",
                "last_name": "Editor",
                "password1": "Local-test-random!83193",
                "password2": "Local-test-random!83193",
            },
        )
        self.assertRedirects(response, reverse("resources:manager"))
        user = CustomUser.objects.get(email="new-editor@example.com")
        self.assertTrue(user.has_perm("resources.publish_resource"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(self.client.session["resources_staff_login"])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {}).status_code, 404)

    def test_expired_and_unknown_setup_tokens_are_rejected(self):
        import hashlib

        from apps.resources.models import StaffSetupToken

        token = "expired-test-token"
        StaffSetupToken.objects.create(
            digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() - timedelta(hours=1),
        )
        for value in (token, "unknown-token"):
            self.assertEqual(
                self.client.get(
                    reverse("resources:setup_staff", args=[value])
                ).status_code,
                404,
            )

    def test_topic_administration_uses_publisher_boundary(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from apps.resources.admin import TopicAdmin

        request = RequestFactory().get("/admin/resources/topic/")
        request.session = {"resources_staff_login": True}
        topic_admin = TopicAdmin(Topic, admin.site)
        request.user = self.author
        self.assertFalse(topic_admin.has_change_permission(request, self.topic))
        request.user = self.publisher
        self.assertTrue(topic_admin.has_change_permission(request, self.topic))
        request.session = {}
        self.assertFalse(topic_admin.has_change_permission(request, self.topic))
