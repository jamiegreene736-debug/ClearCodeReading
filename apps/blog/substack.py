import logging
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)

SUBSTACK_PROFILE_URL = "https://substack.com/@bethanyprincefleming"
SUBSTACK_PUBLICATION_URL = "https://bethanyprincefleming.substack.com"
SUBSTACK_SUBSCRIBE_URL = f"{SUBSTACK_PUBLICATION_URL}/subscribe"

_CACHE_KEY = "blog:substack:posts:v1"
_FAILURE_CACHE_SECONDS = 60
_MAX_FEED_BYTES = 1_000_000
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_ALLOWED_ARTICLE_HOST = "bethanyprincefleming.substack.com"
_ALLOWED_IMAGE_HOSTS = {
    "substackcdn.com",
    "substack-post-media.s3.amazonaws.com",
}


class SubstackFeedError(RuntimeError):
    """Raised when the configured Substack feed cannot be read safely."""


class _PlainTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


@dataclass(frozen=True)
class ExternalCoverImage:
    url: str


@dataclass(frozen=True)
class SubstackPost:
    title: str
    excerpt: str
    published_at: datetime
    external_url: str
    author_name: str
    cover_image_url: str = ""
    category: str = "From Substack"
    is_featured: bool = False
    is_external: bool = True

    def get_absolute_url(self) -> str:
        return self.external_url

    @property
    def display_author(self) -> str:
        return self.author_name or "Bethany Fleming"

    @property
    def cover_image(self) -> ExternalCoverImage | None:
        if not self.cover_image_url:
            return None
        return ExternalCoverImage(url=self.cover_image_url)

    @property
    def cover_image_alt(self) -> str:
        return f"Cover image for {self.title}"


def get_substack_posts() -> list[SubstackPost]:
    feed_url = settings.BLOG_SUBSTACK_FEED_URL.strip()
    if not feed_url:
        return []

    cached_posts = cache.get(_CACHE_KEY)
    if cached_posts is not None:
        return cached_posts

    try:
        payload = _download_feed(feed_url)
        posts = parse_substack_feed(payload)
    except SubstackFeedError as error:
        logger.warning("Substack blog feed is unavailable: %s", error)
        cache.set(_CACHE_KEY, [], _FAILURE_CACHE_SECONDS)
        return []

    cache.set(_CACHE_KEY, posts, max(60, settings.BLOG_SUBSTACK_CACHE_SECONDS))
    return posts


def _download_feed(feed_url: str) -> bytes:
    if not _is_allowed_url(feed_url, {_ALLOWED_ARTICLE_HOST}):
        raise SubstackFeedError("feed URL is not the approved ClearCode Substack host")

    request = Request(
        feed_url,
        headers={
            "Accept": "application/rss+xml, application/xml;q=0.9",
            "User-Agent": "ClearCodeReadingBlog/1.0",
        },
    )
    timeout = min(10.0, max(0.5, settings.BLOG_SUBSTACK_TIMEOUT_SECONDS))

    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if not 200 <= status < 300:
                raise SubstackFeedError(f"feed returned HTTP {status}")
            if not _is_allowed_url(response.geturl(), {_ALLOWED_ARTICLE_HOST}):
                raise SubstackFeedError("feed redirected away from the approved host")
            payload = response.read(_MAX_FEED_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise SubstackFeedError("feed request failed") from error

    if len(payload) > _MAX_FEED_BYTES:
        raise SubstackFeedError("feed response exceeded the size limit")
    return payload


def parse_substack_feed(payload: bytes) -> list[SubstackPost]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise SubstackFeedError("feed XML is malformed") from error

    posts: list[SubstackPost] = []
    for item in root.findall("./channel/item"):
        title = _clean_text(item.findtext("title", default=""))
        excerpt = _clean_text(item.findtext("description", default=""))
        article_url = item.findtext("link", default="").strip()
        published_at = _parse_published_at(item.findtext("pubDate", default=""))
        if not title or not excerpt or published_at is None:
            continue
        if not _is_allowed_url(article_url, {_ALLOWED_ARTICLE_HOST}):
            continue

        creator = _clean_text(item.findtext(_DC_CREATOR, default=""))
        enclosure = item.find("enclosure")
        image_url = enclosure.get("url", "").strip() if enclosure is not None else ""
        if image_url and not _is_allowed_url(image_url, _ALLOWED_IMAGE_HOSTS):
            image_url = ""

        posts.append(
            SubstackPost(
                title=_truncate(title, 200),
                excerpt=_truncate(excerpt, 320),
                published_at=published_at,
                external_url=article_url,
                author_name=_truncate(creator, 150),
                cover_image_url=image_url,
            )
        )

    return sorted(posts, key=lambda post: post.published_at, reverse=True)


def _clean_text(value: str) -> str:
    parser = _PlainTextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return " ".join(value.split())
    return parser.text()


def _parse_published_at(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed


def _is_allowed_url(value: str, allowed_hosts: set[str]) -> bool:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme == "https" and hostname in allowed_hosts


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1].rstrip()}…"
