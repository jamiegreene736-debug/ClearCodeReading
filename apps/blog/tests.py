from datetime import timedelta
from unittest.mock import patch
from urllib.error import URLError

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog.admin import BlogPostAdmin, publish_posts, unpublish_posts
from apps.blog.models import BlogPost
from apps.blog.substack import (
    SubstackFeedError,
    SubstackPost,
    get_substack_posts,
    parse_substack_feed,
)
from apps.blog.views import BlogPostDetailView, BlogPostListView
from apps.users.models import CustomUser


SUBSTACK_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <item>
      <title><![CDATA[A <strong>clear</strong> reading plan]]></title>
      <description><![CDATA[Practical guidance <em>for families</em>.]]></description>
      <link>https://bethanyprincefleming.substack.com/p/clear-reading-plan</link>
      <dc:creator><![CDATA[Bethany Fleming]]></dc:creator>
      <pubDate>Wed, 05 Aug 2026 02:00:21 GMT</pubDate>
      <enclosure url="https://substackcdn.com/image/fetch/cover.png" type="image/jpeg" />
    </item>
  </channel>
</rss>
"""


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


@override_settings(BLOG_SUBSTACK_FEED_URL="")
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

    @patch("apps.blog.views.get_substack_posts")
    def test_blog_landing_page_includes_canonical_substack_articles(
        self,
        get_substack_posts_mock,
    ):
        external_post = SubstackPost(
            title="A Substack reading insight",
            excerpt="A guide for families.",
            published_at=timezone.now() + timedelta(minutes=1),
            external_url=(
                "https://bethanyprincefleming.substack.com/p/reading-insight"
            ),
            author_name="Bethany Fleming",
        )
        get_substack_posts_mock.return_value = [external_post]

        path = reverse("blog:list")
        response = BlogPostListView.as_view()(self._request(path))
        response.render()
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(external_post.title, content)
        self.assertIn(external_post.external_url, content)
        self.assertIn("Read on Substack", content)
        self.assertIn("Follow Bethany Fleming", content)
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


class SubstackFeedTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_parser_builds_safe_external_blog_posts(self):
        posts = parse_substack_feed(SUBSTACK_RSS)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].title, "A clear reading plan")
        self.assertEqual(posts[0].excerpt, "Practical guidance for families.")
        self.assertEqual(posts[0].display_author, "Bethany Fleming")
        self.assertEqual(posts[0].category, "From Substack")
        self.assertEqual(
            posts[0].cover_image.url,
            "https://substackcdn.com/image/fetch/cover.png",
        )
        self.assertEqual(posts[0].published_at.isoformat(), "2026-08-05T02:00:21+00:00")

    def test_parser_skips_articles_outside_the_clearcode_substack(self):
        invalid_urls = (
            b"https://example.com/p/clear-reading-plan",
            b"https://[broken",
        )

        for invalid_url in invalid_urls:
            with self.subTest(invalid_url=invalid_url):
                payload = SUBSTACK_RSS.replace(
                    b"https://bethanyprincefleming.substack.com/p/clear-reading-plan",
                    invalid_url,
                )

                self.assertEqual(parse_substack_feed(payload), [])

    def test_parser_rejects_malformed_xml(self):
        with self.assertRaises(SubstackFeedError):
            parse_substack_feed(b"<rss>")

    @override_settings(
        BLOG_SUBSTACK_FEED_URL="https://bethanyprincefleming.substack.com/feed",
        BLOG_SUBSTACK_CACHE_SECONDS=900,
        BLOG_SUBSTACK_TIMEOUT_SECONDS=1,
    )
    @patch("apps.blog.substack.urlopen", side_effect=URLError("offline"))
    def test_provider_failure_is_cached_and_returns_no_external_posts(
        self,
        urlopen_mock,
    ):
        self.assertEqual(get_substack_posts(), [])
        self.assertEqual(get_substack_posts(), [])

        urlopen_mock.assert_called_once()
