from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.admin import BlogPostAdmin, publish_posts, unpublish_posts
from apps.blog.models import BlogPost
from apps.blog.substack import SUBSTACK_PUBLICATION_URL
from apps.blog.views import BlogPostDetailView, BlogPostListView
from apps.users.models import CustomUser


class BlogPostModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = CustomUser.objects.create_user(
            username="blog-author",
            email="author@example.com",
            first_name="Avery",
            last_name="Reader",
        )

    def test_publishing_sets_timestamp_and_generates_unique_slugs(self):
        first = BlogPost.objects.create(
            title="Reading Growth at Home",
            excerpt="A clear summary.",
            body="A practical article.",
            status=BlogPost.Status.PUBLISHED,
            author=self.author,
        )
        second = BlogPost.objects.create(
            title="Reading Growth at Home",
            excerpt="Another clear summary.",
            body="Another practical article.",
        )

        self.assertEqual(first.slug, "reading-growth-at-home")
        self.assertEqual(second.slug, "reading-growth-at-home-2")
        self.assertIsNotNone(first.published_at)
        self.assertEqual(first.get_absolute_url(), "/blog/reading-growth-at-home/")

    def test_published_queryset_excludes_drafts_and_scheduled_posts(self):
        visible = BlogPost.objects.create(
            title="Visible insight",
            excerpt="Visible now.",
            body="Article body.",
            status=BlogPost.Status.PUBLISHED,
        )
        BlogPost.objects.create(
            title="Draft insight",
            excerpt="Not ready.",
            body="Draft body.",
            status=BlogPost.Status.DRAFT,
        )
        BlogPost.objects.create(
            title="Scheduled insight",
            excerpt="Coming soon.",
            body="Scheduled body.",
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
        )

        self.assertEqual(list(BlogPost.objects.published()), [visible])

    def test_cover_image_requires_accessible_description(self):
        post = BlogPost(
            title="Accessible images",
            excerpt="Cover images need descriptions.",
            body="Article body.",
            cover_image="blog/covers/example.jpg",
        )

        with self.assertRaises(ValidationError) as context:
            post.full_clean()

        self.assertIn("cover_image_alt", context.exception.message_dict)

    def test_display_author_and_reading_time_have_safe_defaults(self):
        post = BlogPost(
            title="Defaults",
            excerpt="Default presentation.",
            body="word " * 201,
        )

        self.assertEqual(post.display_author, "ClearCode Reading")
        self.assertEqual(post.reading_time_minutes, 2)


class BlogPublicViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = CustomUser.objects.create_user(
            username="public-author",
            email="public-author@example.com",
            first_name="Jordan",
            last_name="Lee",
        )
        cls.published = BlogPost.objects.create(
            title="Five Ways to Make Reading Practice Clearer",
            excerpt="Small changes can make practice easier to understand.",
            body="Start with one clear goal.\n\n<script>alert('unsafe')</script>",
            category="For families",
            author=cls.author,
            status=BlogPost.Status.PUBLISHED,
            is_featured=True,
            seo_title="Clearer Reading Practice",
            seo_description="Five practical ideas for clearer reading practice.",
        )
        cls.draft = BlogPost.objects.create(
            title="Unfinished staff draft",
            excerpt="This should never be public.",
            body="Private draft notes.",
        )
        cls.scheduled = BlogPost.objects.create(
            title="Tomorrow's article",
            excerpt="This is scheduled for later.",
            body="Future article.",
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
        )

    def setUp(self):
        self.request_factory = RequestFactory()

    def _request(self, path):
        request = self.request_factory.get(path)
        request.user = AnonymousUser()
        return request

    def test_blog_landing_page_only_lists_currently_published_posts(self):
        path = reverse("blog:list")
        response = BlogPostListView.as_view()(self._request(path))
        response.render()
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.published.title, content)
        self.assertIn("Reading insights, made clear.", content)
        self.assertNotIn(self.draft.title, content)
        self.assertNotIn(self.scheduled.title, content)

    def test_blog_landing_page_keeps_substack_secondary_to_internal_posts(self):
        path = reverse("blog:list")
        response = BlogPostListView.as_view()(self._request(path))
        response.render()
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context_data["posts"]), [self.published])
        self.assertIn("Original guidance from ClearCode Reading specialists", content)
        self.assertIn(f'href="{SUBSTACK_PUBLICATION_URL}"', content)
        self.assertEqual(content.count(f'href="{SUBSTACK_PUBLICATION_URL}"'), 1)
        self.assertIn("Visit Substack", content)
        self.assertNotIn("Read on Substack", content)
        self.assertNotIn("Subscribe on Substack", content)
        self.assertIn('target="_blank" rel="noopener"', content)

    def test_article_page_uses_seo_fields_and_escapes_admin_content(self):
        path = self.published.get_absolute_url()
        response = BlogPostDetailView.as_view()(
            self._request(path),
            slug=self.published.slug,
        )
        response.render()
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Clearer Reading Practice | ClearCode Reading", content)
        self.assertIn("Five practical ideas for clearer reading practice.", content)
        self.assertIn("Jordan Lee", content)
        self.assertIn("&lt;script&gt;alert(&#x27;unsafe&#x27;)&lt;/script&gt;", content)
        self.assertNotIn("<script>alert('unsafe')</script>", content)
        self.assertIn('id="early-interest-survey"', content)
        self.assertIn('action="/crm/survey/"', content)
        self.assertIn(f'name="source_path" value="{self.published.get_absolute_url()}"', content)
        self.assertIn(f'name="blog_post_slug" value="{self.published.slug}"', content)
        self.assertIn("Ten questions. About two minutes.", content)

    def test_draft_and_scheduled_article_urls_return_not_found(self):
        for post in (self.draft, self.scheduled):
            with self.subTest(post=post.title):
                with self.assertRaises(Http404):
                    BlogPostDetailView.as_view()(
                        self._request(post.get_absolute_url()),
                        slug=post.slug,
                    )


class BlogAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = CustomUser.objects.create_user(
            username="blog-editor",
            email="editor@example.com",
            is_staff=True,
        )

    def test_blog_post_is_registered_with_editor_workflow(self):
        model_admin = admin.site._registry[BlogPost]

        self.assertIsInstance(model_admin, BlogPostAdmin)
        self.assertEqual(model_admin.prepopulated_fields, {"slug": ("title",)})
        self.assertIn(publish_posts, model_admin.actions)
        self.assertIn(unpublish_posts, model_admin.actions)

    def test_admin_defaults_author_to_current_editor(self):
        request = RequestFactory().post("/admin/blog/blogpost/add/")
        request.user = self.staff_user
        post = BlogPost(
            title="Admin-authored post",
            excerpt="Created in the admin editor.",
            body="Article body.",
        )
        model_admin = BlogPostAdmin(BlogPost, admin.site)

        model_admin.save_model(request, post, form=None, change=False)

        self.assertEqual(post.author, self.staff_user)

    def test_publish_and_unpublish_actions_control_public_visibility(self):
        post = BlogPost.objects.create(
            title="Action workflow",
            excerpt="Publish this from the list.",
            body="Article body.",
            is_featured=True,
        )

        publish_posts(None, None, BlogPost.objects.filter(pk=post.pk))
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.PUBLISHED)
        self.assertIsNotNone(post.published_at)
        self.assertTrue(BlogPost.objects.published().filter(pk=post.pk).exists())

        unpublish_posts(None, None, BlogPost.objects.filter(pk=post.pk))
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.DRAFT)
        self.assertFalse(post.is_featured)
        self.assertFalse(BlogPost.objects.published().filter(pk=post.pk).exists())


def _png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (4, 4), color=(26, 122, 122)).save(buffer, format="PNG")
    return buffer.getvalue()


class BlogCmsAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.editor = CustomUser.objects.create_user(
            username="cms-editor",
            email="cms-editor@example.com",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        cls.parent = CustomUser.objects.create_user(
            username="cms-parent",
            email="cms-parent@example.com",
            role=CustomUser.Role.GUARDIAN,
        )

    def test_anonymous_visitors_are_sent_to_login(self):
        response = self.client.get(reverse("blog_manage:list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_parents_cannot_open_the_blog_manager(self):
        self.client.force_login(self.parent)

        response = self.client.get(reverse("blog_manage:list"))

        self.assertEqual(response.status_code, 403)

    def test_manage_menu_links_to_the_blog_cms_for_editors(self):
        self.client.force_login(self.editor)

        response = self.client.get(reverse("blog_manage:list"))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-testid="blog-manage-menu-link"', content)
        self.assertNotIn('data-testid="blog-new-menu-link"', content)
        self.assertEqual(content.count('href="/portal/blog/"'), 1)
        self.assertIn('data-testid="blog-empty-state"', content)
        self.assertNotIn("/admin/blog/blogpost/", content)


class BlogCmsWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.editor = CustomUser.objects.create_user(
            username="cms-writer",
            email="cms-writer@example.com",
            first_name="Bethany",
            last_name="Fleming",
            is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.editor)

    def _form_data(self, **overrides):
        data = {
            "title": "How to make reading practice stick",
            "slug": "",
            "category": "For families",
            "excerpt": "Three small routines that help practice actually happen.",
            "body_format": BlogPost.BodyFormat.HTML,
            "body": "<h2>Start small</h2><p>Ten minutes <strong>every day</strong> beats an hour on Sunday.</p><script>alert('x')</script>",
            "cover_image_alt": "",
            "status": BlogPost.Status.DRAFT,
            "published_at": "",
            "seo_title": "",
            "seo_description": "",
        }
        data.update(overrides)
        return data

    def test_new_post_is_saved_as_a_private_sanitised_draft(self):
        response = self.client.post(reverse("blog_manage:create"), self._form_data())

        post = BlogPost.objects.get()
        self.assertRedirects(response, reverse("blog_manage:edit", args=[post.pk]))
        self.assertEqual(post.author, self.editor)
        self.assertEqual(post.slug, "how-to-make-reading-practice-stick")
        self.assertEqual(post.status, BlogPost.Status.DRAFT)
        self.assertIn("<h2>Start small</h2>", post.body)
        self.assertNotIn("<script>", post.body)
        self.assertFalse(BlogPost.objects.published().exists())

        public = self.client.get(reverse("blog:list"))
        self.assertNotIn(post.title, public.content.decode())

    def test_publishing_adds_a_tile_to_the_public_blog(self):
        self.client.post(reverse("blog_manage:create"), self._form_data())
        post = BlogPost.objects.get()

        response = self.client.post(
            reverse("blog_manage:publish", args=[post.pk]),
            {"next": reverse("blog_manage:list") + "?tab=published"},
        )

        self.assertRedirects(response, reverse("blog_manage:list") + "?tab=published")
        post.refresh_from_db()
        self.assertTrue(post.is_live)

        public = self.client.get(reverse("blog:list"))
        content = public.content.decode()
        self.assertIn('data-testid="blog-tile"', content)
        self.assertIn(post.title, content)
        self.assertIn(post.excerpt, content)
        self.assertIn("For families", content)
        self.assertIn("Bethany Fleming", content)
        self.assertIn(post.get_absolute_url(), content)

        article = self.client.get(post.get_absolute_url())
        self.assertContains(article, "<h2>Start small</h2>", html=False)
        self.assertNotContains(article, "<script>alert")

    def test_cover_image_is_stored_in_the_database_and_served_publicly_once_live(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("cover.png", _png_bytes(), content_type="image/png")
        response = self.client.post(
            reverse("blog_manage:create"),
            self._form_data(cover_image_alt="A child reading on a sofa"),
        )
        post = BlogPost.objects.get()
        response = self.client.post(
            reverse("blog_manage:edit", args=[post.pk]),
            {**self._form_data(cover_image_alt="A child reading on a sofa"), "cover_upload": upload},
        )
        self.assertEqual(response.status_code, 302)

        post.refresh_from_db()
        self.assertTrue(post.has_cover)
        self.assertEqual(post.cover_content_type, "image/png")
        self.assertEqual(post.cover_url, reverse("blog:cover", args=[post.slug]))

        # Drafts keep their cover private from the public.
        self.client.logout()
        self.assertEqual(self.client.get(post.cover_url).status_code, 404)

        post.status = BlogPost.Status.PUBLISHED
        post.save()
        served = self.client.get(post.cover_url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        self.assertEqual(served.content, bytes(post.cover_data))

        tiles = self.client.get(reverse("blog:list")).content.decode()
        self.assertIn(f'src="{post.cover_url}"', tiles)
        self.assertIn('alt="A child reading on a sofa"', tiles)

    def test_cover_requires_a_description(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("cover.png", _png_bytes(), content_type="image/png")
        response = self.client.post(
            reverse("blog_manage:create"),
            {**self._form_data(), "cover_upload": upload},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(BlogPost.objects.exists())
        self.assertIn("Describe the cover image", response.content.decode())

    def test_scheduled_posts_stay_private_until_their_publish_time(self):
        later = timezone.localtime(timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(
            reverse("blog_manage:create"),
            self._form_data(status=BlogPost.Status.PUBLISHED, published_at=later),
        )
        post = BlogPost.objects.get()

        self.assertTrue(post.is_scheduled)
        self.assertEqual(post.state, "Scheduled")
        self.assertFalse(BlogPost.objects.published().exists())
        self.assertNotIn('data-testid="blog-tile"', self.client.get(reverse("blog:list")).content.decode())
        manager = self.client.get(reverse("blog_manage:list") + "?tab=scheduled").content.decode()
        self.assertIn(post.title, manager)

        preview = self.client.get(reverse("blog_manage:preview", args=[post.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertIn('data-testid="blog-preview-banner"', preview.content.decode())

    def test_unpublish_feature_duplicate_and_delete_actions(self):
        post = BlogPost.objects.create(
            title="Workflow post",
            excerpt="Summary.",
            body="Body.",
            status=BlogPost.Status.PUBLISHED,
            author=self.editor,
        )

        self.client.post(reverse("blog_manage:feature", args=[post.pk]))
        post.refresh_from_db()
        self.assertTrue(post.is_featured)

        self.client.post(reverse("blog_manage:unpublish", args=[post.pk]))
        post.refresh_from_db()
        self.assertEqual(post.status, BlogPost.Status.DRAFT)
        self.assertFalse(post.is_featured)
        self.assertFalse(BlogPost.objects.published().exists())

        response = self.client.post(reverse("blog_manage:duplicate", args=[post.pk]))
        copy = BlogPost.objects.exclude(pk=post.pk).get()
        self.assertRedirects(response, reverse("blog_manage:edit", args=[copy.pk]))
        self.assertEqual(copy.title, "Workflow post (copy)")
        self.assertEqual(copy.slug, "workflow-post-copy")
        self.assertEqual(copy.status, BlogPost.Status.DRAFT)

        response = self.client.post(reverse("blog_manage:delete", args=[copy.pk]))
        self.assertRedirects(response, reverse("blog_manage:list"))
        self.assertFalse(BlogPost.objects.filter(pk=copy.pk).exists())

    def test_editor_search_and_tabs_filter_posts(self):
        BlogPost.objects.create(title="Fluency at home", excerpt="a", body="b", status=BlogPost.Status.PUBLISHED)
        BlogPost.objects.create(title="Phonics draft", excerpt="a", body="b")

        drafts = self.client.get(reverse("blog_manage:list") + "?tab=drafts").content.decode()
        self.assertIn("Phonics draft", drafts)
        self.assertNotIn("Fluency at home", drafts)

        search = self.client.get(reverse("blog_manage:list") + "?q=fluency").content.decode()
        self.assertIn("Fluency at home", search)
        self.assertNotIn("Phonics draft", search)

    def test_rich_text_article_keeps_safe_links_and_strips_dangerous_markup(self):
        post = BlogPost.objects.create(
            title="Links",
            excerpt="Summary.",
            body='<p>Read <a href="https://example.com" onclick="steal()">this</a> and <a href="javascript:alert(1)">that</a>.</p><iframe src="https://evil"></iframe>',
            body_format=BlogPost.BodyFormat.HTML,
            status=BlogPost.Status.PUBLISHED,
        )

        rendered = str(post.rendered_body)
        self.assertIn('href="https://example.com"', rendered)
        self.assertIn('rel="noopener noreferrer"', rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertNotIn("<iframe", rendered)
        self.assertEqual(post.reading_time_minutes, 1)


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_1PX = _png_bytes()


class BlogInlineImageTests(TestCase):
    """Images pasted from Google Docs / Word must be stored, never silently dropped."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = CustomUser.objects.create_user(
            username="cms-images",
            email="cms-images@example.com",
            first_name="Bethany",
            last_name="Fleming",
            is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.editor)

    def _data_uri(self):
        import base64

        return "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()

    def _form_data(self, body):
        return {
            "title": "Pasted from Google Docs",
            "slug": "",
            "category": "",
            "excerpt": "An article with pictures.",
            "body_format": BlogPost.BodyFormat.HTML,
            "body": body,
            "cover_image_alt": "",
            "status": BlogPost.Status.PUBLISHED,
            "published_at": "",
            "seo_title": "",
            "seo_description": "",
        }

    def test_pasted_base64_images_are_stored_and_served(self):
        from apps.blog.models import BlogImage

        body = f'<p>Before</p><img src="{self._data_uri()}" width="624" height="329"><p>After</p>'
        response = self.client.post(reverse("blog_manage:create"), self._form_data(body))
        self.assertEqual(response.status_code, 302, response.content[:500])

        post = BlogPost.objects.get()
        image = BlogImage.objects.get()
        self.assertEqual(bytes(image.data), PNG_1PX)
        self.assertEqual(image.content_type, "image/png")
        self.assertEqual(image.uploaded_by, self.editor)
        self.assertNotIn("data:", post.body)
        self.assertIn(f'src="{image.url}"', post.body)
        self.assertIn('width="624"', post.body)

        article = self.client.get(post.get_absolute_url())
        self.assertContains(article, f'src="{image.url}"')

        self.client.logout()
        served = self.client.get(image.url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        self.assertEqual(served.content, PNG_1PX)

    def test_model_save_imports_base64_images_on_every_path(self):
        from apps.blog.models import BlogImage

        post = BlogPost.objects.create(
            title="Saved from the admin",
            excerpt="x",
            body_format=BlogPost.BodyFormat.HTML,
            body=f'<img src="{self._data_uri()}" alt="chart">',
            author=self.editor,
        )
        image = BlogImage.objects.get()
        self.assertIn(f'<img src="{image.url}" alt="chart">', post.body)

    def test_unreadable_image_sources_are_refused_instead_of_dropped(self):
        body = '<p>Text</p><img src="file:///C:/Users/b/clip_image001.png" width="624"><img width="311" height="436">'
        response = self.client.post(reverse("blog_manage:create"), self._form_data(body))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 images in the article could not be imported")
        self.assertFalse(BlogPost.objects.exists())

    def test_oversized_pasted_image_is_refused(self):
        import base64

        big = "data:image/png;base64," + base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode()
        response = self.client.post(reverse("blog_manage:create"), self._form_data(f'<img src="{big}">'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "larger than 8 MB")

    def test_upload_endpoint_stores_image_and_returns_url(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.blog.models import BlogImage

        upload = SimpleUploadedFile("diagram.png", PNG_1PX, content_type="image/png")
        response = self.client.post(reverse("blog_manage:image_upload"), {"image": upload})
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        image = BlogImage.objects.get()
        self.assertEqual(payload["url"], image.url)
        self.assertEqual(image.original_name, "diagram.png")
        self.assertEqual(self.client.get(payload["url"]).content, PNG_1PX)

    def test_upload_endpoint_rejects_non_images_and_outsiders(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        bad = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        response = self.client.post(reverse("blog_manage:image_upload"), {"image": bad})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

        self.client.logout()
        parent = CustomUser.objects.create_user(username="parent-x", email="parent-x@example.com")
        self.client.force_login(parent)
        upload = SimpleUploadedFile("diagram.png", PNG_1PX, content_type="image/png")
        self.assertEqual(self.client.post(reverse("blog_manage:image_upload"), {"image": upload}).status_code, 403)

    def test_unknown_image_key_is_404(self):
        import uuid

        self.assertEqual(self.client.get(reverse("blog:image", kwargs={"key": uuid.uuid4()})).status_code, 404)
