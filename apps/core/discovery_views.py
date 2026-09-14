from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import cache_control

from apps.core.discovery import (
    AI_CRAWLER_USER_AGENTS,
    ROBOTS_DISALLOW_PATHS,
    absolute_public_url,
    llms_txt,
    marketing_sitemap_paths,
)


def _xml_escape(value):
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


@method_decorator(cache_control(public=True, max_age=3600), name="dispatch")
class RobotsTxtView(View):
    def get(self, request, *args, **kwargs):
        lines = [
            "User-agent: *",
            "Allow: /",
            "",
        ]
        for agent in AI_CRAWLER_USER_AGENTS:
            lines.extend([f"User-agent: {agent}", "Allow: /", ""])
        for path in ROBOTS_DISALLOW_PATHS:
            lines.append(f"Disallow: {path}")
        lines.extend(["", f"Sitemap: {absolute_public_url('/sitemap.xml')}", ""])
        return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@method_decorator(cache_control(public=True, max_age=3600), name="dispatch")
class LlmsTxtView(View):
    def get(self, request, *args, **kwargs):
        return HttpResponse(llms_txt(), content_type="text/plain; charset=utf-8")


@method_decorator(cache_control(public=True, max_age=1800), name="dispatch")
class SitemapXmlView(View):
    def get(self, request, *args, **kwargs):
        paths = marketing_sitemap_paths()
        try:
            from apps.blog.models import BlogPost

            paths.extend(
                post.get_absolute_url()
                for post in BlogPost.objects.published().only("slug")
            )
        except Exception:
            pass

        unique_paths = list(dict.fromkeys(paths))
        url_blocks = []
        for path in unique_paths:
            loc = _xml_escape(absolute_public_url(path))
            url_blocks.append(f"  <url>\n    <loc>{loc}</loc>\n  </url>")
        body = "\n".join(url_blocks)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n"
            "</urlset>\n"
        )
        return HttpResponse(xml, content_type="application/xml; charset=utf-8")
